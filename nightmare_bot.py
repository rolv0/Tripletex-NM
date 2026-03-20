import asyncio
import json
import logging
import os
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import websockets

logging.basicConfig(
    level=os.environ.get("GROCERY_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("nightmare-bot")
logging.getLogger("websockets").setLevel(logging.WARNING)

DIRECTIONS: Dict[str, Tuple[int, int]] = {
    "up": (0, -1),
    "right": (1, 0),
    "down": (0, 1),
    "left": (-1, 0),
}

ORIGINS = [
    "https://game.ainm.no",
    "https://game.aimn.no",
    "https://aimn.no",
]

HORIZON = int(os.environ.get("NIGHTMARE_HORIZON", "2"))
COURIER_FRAC = float(os.environ.get("NIGHTMARE_COURIER_FRAC", "0.3"))
DECONGEST_ROUND = int(os.environ.get("NIGHTMARE_DECONGEST_ROUND", "150"))
DROP_LANE_CAP = int(os.environ.get("NIGHTMARE_DROP_LANE_CAP", "3"))
ENABLE_TRAFFIC_CONTROL = os.environ.get("NIGHTMARE_TRAFFIC_CONTROL", "0") == "1"


@dataclass
class BotState:
    target_item_id: Optional[str] = None
    last_direction: Optional[str] = None
    recent_positions: deque = None
    last_goal_dist: int = 10**9
    no_progress_ticks: int = 0
    wait_ticks: int = 0

    def __post_init__(self) -> None:
        if self.recent_positions is None:
            self.recent_positions = deque(maxlen=8)


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().lstrip("\ufeff")
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def parse_xy(node: Any) -> Optional[Tuple[int, int]]:
    if isinstance(node, dict):
        if "x" in node and "y" in node:
            return int(node["x"]), int(node["y"])
        if "position" in node:
            return parse_xy(node["position"])
    if isinstance(node, (list, tuple)) and len(node) >= 2:
        return int(node[0]), int(node[1])
    return None


def extract_token_from_uri(uri: str) -> Optional[str]:
    if "token=" not in uri:
        return None
    token = uri.split("token=", 1)[1]
    if "&" in token:
        token = token.split("&", 1)[0]
    return token


def extract_bounds(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    grid = data.get("grid", {})
    if isinstance(grid, dict) and "width" in grid and "height" in grid:
        return int(grid["width"]), int(grid["height"])
    return None


def norm_item(v: Any) -> str:
    return str(v)


def extract_blocked(data: Dict[str, Any]) -> Set[Tuple[int, int]]:
    blocked: Set[Tuple[int, int]] = set()
    grid = data.get("grid", {})
    if isinstance(grid, dict):
        for wall in grid.get("walls", []):
            p = parse_xy(wall)
            if p is not None:
                blocked.add(p)
    for item in data.get("items", []):
        p = parse_xy(item.get("position"))
        if p is not None:
            blocked.add(p)
    return blocked


def neighbors(cell: Tuple[int, int], bounds: Tuple[int, int], blocked: Set[Tuple[int, int]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    w, h = bounds
    x, y = cell
    for dx, dy in DIRECTIONS.values():
        nx, ny = x + dx, y + dy
        np = (nx, ny)
        if nx < 0 or ny < 0 or nx >= w or ny >= h:
            continue
        if np in blocked:
            continue
        out.append(np)
    return out


def is_open_cell(cell: Tuple[int, int], bounds: Tuple[int, int], blocked: Set[Tuple[int, int]]) -> bool:
    return len(neighbors(cell, bounds, blocked)) >= 3


def pick_escape_goal(
    pos: Tuple[int, int],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    preferred: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int]]:
    w, h = bounds
    best_cell: Optional[Tuple[int, int]] = None
    best_score = -10**9
    for y in range(h):
        for x in range(w):
            c = (x, y)
            if c in blocked:
                continue
            if not is_open_cell(c, bounds, blocked):
                continue
            d = bfs_distance(pos, {c}, bounds, blocked)
            if d >= 10**9:
                continue
            # Prefer reachable open cells that are not too close.
            score = min(d, 12)
            if preferred is not None:
                score -= abs(preferred[0] - x) + abs(preferred[1] - y) * 0.05
            if score > best_score:
                best_score = score
                best_cell = c
    return best_cell


def ring_goals(
    center: Tuple[int, int],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    min_r: int = 2,
    max_r: int = 5,
) -> Set[Tuple[int, int]]:
    w, h = bounds
    cx, cy = center
    out: Set[Tuple[int, int]] = set()
    for y in range(h):
        for x in range(w):
            c = (x, y)
            if c in blocked:
                continue
            md = abs(cx - x) + abs(cy - y)
            if min_r <= md <= max_r and is_open_cell(c, bounds, blocked):
                out.add(c)
    return out


def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def bfs_first_direction(
    start: Tuple[int, int],
    goals: Set[Tuple[int, int]],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
) -> Optional[str]:
    if not goals:
        return None
    if start in goals:
        return None

    q = deque([start])
    prev: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {start: None}

    while q:
        cur = q.popleft()
        if cur in goals:
            c = cur
            while prev[c] is not None and prev[c] != start:
                c = prev[c]
            p = prev[c]
            if p is None:
                return None
            dx, dy = c[0] - p[0], c[1] - p[1]
            for name, (mx, my) in DIRECTIONS.items():
                if (dx, dy) == (mx, my):
                    return name
            return None

        for n in neighbors(cur, bounds, blocked):
            if n in prev:
                continue
            prev[n] = cur
            q.append(n)

    return None


def bfs_distance(
    start: Tuple[int, int],
    goals: Set[Tuple[int, int]],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
) -> int:
    if not goals:
        return 10**9
    if start in goals:
        return 0

    q = deque([(start, 0)])
    seen = {start}
    while q:
        cur, d = q.popleft()
        for n in neighbors(cur, bounds, blocked):
            if n in seen:
                continue
            if n in goals:
                return d + 1
            seen.add(n)
            q.append((n, d + 1))
    return 10**9


def direction_to_action(direction: str) -> str:
    return {
        "up": "move_up",
        "right": "move_right",
        "down": "move_down",
        "left": "move_left",
    }[direction]


def reverse_of(direction: str) -> str:
    return {"up": "down", "down": "up", "left": "right", "right": "left"}[direction]


def is_oscillating_positions(rp: deque) -> bool:
    vals = list(rp)
    if len(vals) < 6:
        return False
    # A-B-A-B ping-pong
    if vals[-1] == vals[-3] == vals[-5] and vals[-2] == vals[-4] == vals[-6] and vals[-1] != vals[-2]:
        return True
    return len(set(vals[-6:])) <= 2


def get_active_order(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    orders = data.get("orders", [])
    if not orders:
        return None
    active = next((o for o in orders if o.get("status") == "active"), None)
    if active is not None:
        return active
    idx = int(data.get("active_order_index", 0))
    idx = min(max(idx, 0), len(orders) - 1)
    return orders[idx]


def build_item_lookup(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in data.get("items", []):
        iid = item.get("id")
        if iid is not None:
            out[str(iid)] = item
    return out


def assign_tasks(
    data: Dict[str, Any],
    bot_ids: List[int],
    bot_pos: Dict[int, Tuple[int, int]],
    bot_inv: Dict[int, List[str]],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    state_map: Dict[int, BotState],
) -> Dict[int, Dict[str, Any]]:
    active = get_active_order(data)
    drop = parse_xy(data.get("drop_off")) or (1, 1)

    tasks: Dict[int, Dict[str, Any]] = {}
    if active is None:
        for bid in bot_ids:
            tasks[bid] = {"kind": "wait"}
        return tasks

    required = Counter(norm_item(x) for x in active.get("items_required", []))
    delivered = Counter(norm_item(x) for x in active.get("items_delivered", []))
    remaining = +(required - delivered)

    # Role split
    courier_count = max(2, int(len(bot_ids) * COURIER_FRAC))
    couriers = set(sorted(bot_ids)[-courier_count:])

    # Deliver tasks first
    for bid in bot_ids:
        inv = bot_inv.get(bid, [])
        has_del = any(remaining.get(norm_item(it), 0) > 0 for it in inv if it is not None)
        if has_del:
            tasks[bid] = {"kind": "deliver", "goals": {drop}}

    # Missing after current carrying
    carrying = Counter()
    for bid in bot_ids:
        for it in bot_inv.get(bid, []):
            if it is None:
                continue
            itn = norm_item(it)
            if remaining.get(itn, 0) > carrying[itn]:
                carrying[itn] += 1
    missing = +(remaining - carrying)
    remaining_total = sum(missing.values())
    order_sprint = remaining_total <= 2

    items = data.get("items", [])
    claimed_items: Set[str] = set()

    # Global greedy pickup assignment for active missing items.
    cand: List[Tuple[int, int, str, Set[Tuple[int, int]]]] = []
    for bid in bot_ids:
        if bid in tasks:
            continue
        if bid in couriers and not order_sprint:
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        if len(bot_inv.get(bid, [])) >= 3:
            continue

        for item in items:
            iid = item.get("id")
            itype_raw = item.get("type")
            ipos = parse_xy(item.get("position"))
            if iid is None or itype_raw is None or ipos is None:
                continue
            itype = norm_item(itype_raw)
            iid_s = str(iid)
            if missing.get(itype, 0) <= 0:
                continue
            goals = set(neighbors(ipos, bounds, blocked))
            if not goals:
                continue
            d = bfs_distance(pos, goals, bounds, blocked)
            if d >= 10**9:
                continue
            # completion bonus
            bonus = 20 if missing.get(itype, 0) == 1 else 0
            cost = d * 10 - bonus
            cand.append((cost, bid, iid_s, goals))

    cand.sort(key=lambda x: x[0])
    used_bots: Set[int] = set()
    for _, bid, iid, goals in cand:
        if bid in used_bots or iid in claimed_items:
            continue
        tasks[bid] = {"kind": "pickup", "item_id": iid, "goals": goals}
        used_bots.add(bid)
        claimed_items.add(iid)
        state_map.setdefault(bid, BotState()).target_item_id = iid

    # If active order is fully covered, pre-position pickers on nearest items
    # so we don't freeze between order transitions.
    if not missing:
        pre_cand: List[Tuple[int, int, str, Set[Tuple[int, int]]]] = []
        for bid in bot_ids:
            if bid in tasks or bid in couriers:
                continue
            pos = bot_pos.get(bid)
            if pos is None:
                continue
            if len(bot_inv.get(bid, [])) >= 3:
                continue
            for item in items:
                iid = item.get("id")
                ipos = parse_xy(item.get("position"))
                if iid is None or ipos is None:
                    continue
                iid_s = str(iid)
                if iid_s in claimed_items:
                    continue
                goals = set(neighbors(ipos, bounds, blocked))
                if not goals:
                    continue
                d = bfs_distance(pos, goals, bounds, blocked)
                if d >= 10**9:
                    continue
                pre_cand.append((d, bid, iid_s, goals))

        pre_cand.sort(key=lambda x: x[0])
        for _, bid, iid, goals in pre_cand:
            if bid in tasks or iid in claimed_items:
                continue
            tasks[bid] = {"kind": "pickup", "item_id": iid, "goals": goals}
            claimed_items.add(iid)
            state_map.setdefault(bid, BotState()).target_item_id = iid

    # Remaining idle bots: try active patrol (nearest item-adjacent) to avoid corner-freeze.
    rnd = int(data.get("round", 0) or 0)
    late_game = ENABLE_TRAFFIC_CONTROL and rnd >= DECONGEST_ROUND
    patrol_candidates: List[Tuple[int, int, Set[Tuple[int, int]]]] = []
    for bid in bot_ids:
        if bid in tasks:
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        best_d = 10**9
        best_goals: Optional[Set[Tuple[int, int]]] = None
        for item in items:
            ipos = parse_xy(item.get("position"))
            if ipos is None:
                continue
            goals = set(neighbors(ipos, bounds, blocked))
            if not goals:
                continue
            d = bfs_distance(pos, goals, bounds, blocked)
            if d < best_d:
                best_d = d
                best_goals = goals
        if best_goals is not None and best_d < 10**9:
            patrol_candidates.append((best_d, bid, best_goals))

    patrol_candidates.sort(key=lambda x: x[0])
    max_patrol = 2 if late_game else max(3, len(bot_ids) // 3)
    for _, bid, goals in patrol_candidates[:max_patrol]:
        if bid in tasks:
            continue
        tasks[bid] = {"kind": "patrol", "goals": goals}

    # Remaining truly idle bots: couriers stay near drop, others wait.
    for bid in bot_ids:
        if bid in tasks:
            continue
        if bid in couriers:
            staging = ring_goals(drop, bounds, blocked, min_r=2, max_r=5)
            if not staging:
                staging = set(neighbors(drop, bounds, blocked))
            staging = {c for c in staging if is_open_cell(c, bounds, blocked)} or staging
            if not staging:
                staging = {drop}
            tasks[bid] = {"kind": "staging", "goals": staging}
        else:
            tasks[bid] = {"kind": "wait"}

    # Escape override for bots that have been stuck with no progress.
    for bid in bot_ids:
        st = state_map.setdefault(bid, BotState())
        if st.no_progress_ticks < 4:
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        esc = pick_escape_goal(pos, bounds, blocked, preferred=drop)
        if esc is not None:
            tasks[bid] = {"kind": "escape", "goals": {esc}}

    return tasks


def resolve_moves(
    data: Dict[str, Any],
    bot_ids: List[int],
    bot_pos: Dict[int, Tuple[int, int]],
    tasks: Dict[int, Dict[str, Any]],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    state_map: Dict[int, BotState],
) -> List[Dict[str, Any]]:
    drop = parse_xy(data.get("drop_off")) or (1, 1)
    rnd = int(data.get("round", 0) or 0)
    drop_buffer_on = ENABLE_TRAFFIC_CONTROL and rnd >= DECONGEST_ROUND
    item_lookup = build_item_lookup(data)

    active = get_active_order(data)
    remaining_active_total = 99
    if active is not None:
        req = Counter(norm_item(x) for x in active.get("items_required", []))
        deliv = Counter(norm_item(x) for x in active.get("items_delivered", []))
        rem = +(req - deliv)
        remaining_active_total = sum(rem.values())

    # Queue-control for drop_off: only a limited number of delivery bots
    # are allowed to route directly to drop at the same time.
    deliver_bots = [bid for bid in bot_ids if tasks.get(bid, {}).get("kind") == "deliver" and bot_pos.get(bid) is not None]
    lane_cap = DROP_LANE_CAP
    if remaining_active_total <= 3:
        lane_cap = max(DROP_LANE_CAP, 6)
    if ENABLE_TRAFFIC_CONTROL and len(deliver_bots) > lane_cap:
        deliver_ranked = sorted(deliver_bots, key=lambda bid: manhattan(bot_pos[bid], drop))
        allow_drop = set(deliver_ranked[:lane_cap])
        hold_ring = ring_goals(drop, bounds, blocked, min_r=2, max_r=5)
        if not hold_ring:
            hold_ring = set(neighbors(drop, bounds, blocked))
        for bid in deliver_bots:
            if bid in allow_drop:
                continue
            # Keep carrying bots near drop, but out of the final lane.
            tasks[bid] = {"kind": "queue_deliver", "goals": hold_ring}

    actions: Dict[int, Dict[str, Any]] = {bid: {"bot": bid, "action": "wait"} for bid in bot_ids}

    # Immediate drop/pick.
    reserved_pick_ids: Set[str] = set()
    for bid in bot_ids:
        task = tasks.get(bid, {"kind": "wait"})
        pos = bot_pos.get(bid)
        if pos is None:
            continue

        if task["kind"] == "deliver" and pos == drop:
            actions[bid] = {"bot": bid, "action": "drop_off"}
            continue

        if task["kind"] == "pickup":
            iid = str(task.get("item_id", ""))
            item = item_lookup.get(iid)
            ipos = parse_xy(item.get("position")) if item else None
            if ipos is not None and abs(ipos[0] - pos[0]) + abs(ipos[1] - pos[1]) == 1 and iid not in reserved_pick_ids:
                reserved_pick_ids.add(iid)
                actions[bid] = {"bot": bid, "action": "pick_up", "item_id": iid}

    # Multi-step reservation (horizon=2 default)
    reservations: Dict[int, Set[Tuple[int, int]]] = {t: set() for t in range(HORIZON + 1)}
    edge_res: Set[Tuple[Tuple[int, int], Tuple[int, int], int]] = set()
    for bid, p in bot_pos.items():
        reservations[0].add(p)

    # Prioritize deliveries then pickups
    ordered = sorted(
        bot_ids,
        key=lambda bid: (
            0 if tasks.get(bid, {}).get("kind") == "deliver" else 1,
            0 if tasks.get(bid, {}).get("kind") == "queue_deliver" else 1,
            0 if tasks.get(bid, {}).get("kind") == "pickup" else 1,
            0 if tasks.get(bid, {}).get("kind") == "escape" else 1,
            bid,
        ),
    )

    first_move: Dict[int, Optional[str]] = {}
    # Track progress toward each bot's current goal to detect local oscillation.
    for bid in bot_ids:
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        goals = set(tasks.get(bid, {}).get("goals", set()))
        if not goals:
            continue
        dcur = bfs_distance(pos, goals, bounds, blocked)
        st = state_map.setdefault(bid, BotState())
        if dcur >= st.last_goal_dist:
            st.no_progress_ticks += 1
        else:
            st.no_progress_ticks = 0
        st.last_goal_dist = dcur

    # Late-game decongestion: if many bots are not progressing, throttle movers.
    stuck_count = sum(1 for bid in bot_ids if state_map.setdefault(bid, BotState()).no_progress_ticks >= 4)
    decongest = ENABLE_TRAFFIC_CONTROL and rnd >= DECONGEST_ROUND and stuck_count >= max(3, len(bot_ids) // 3)
    allowed_movers: Optional[Set[int]] = None
    if decongest:
        mover_budget = max(3, len(bot_ids) // 2)
        forced = {bid for bid in bot_ids if state_map.setdefault(bid, BotState()).wait_ticks >= 2}
        ranked = sorted(
            bot_ids,
            key=lambda bid: (
                0 if tasks.get(bid, {}).get("kind") == "deliver" else 1,
                0 if tasks.get(bid, {}).get("kind") == "queue_deliver" else 1,
                0 if tasks.get(bid, {}).get("kind") == "escape" else 1,
                0 if tasks.get(bid, {}).get("kind") == "pickup" else 1,
                -state_map.setdefault(bid, BotState()).wait_ticks,
                -state_map.setdefault(bid, BotState()).no_progress_ticks,
                bid,
            ),
        )
        allowed_movers = set(ranked[:mover_budget]) | forced

    for bid in ordered:
        if actions[bid]["action"] != "wait":
            continue
        if allowed_movers is not None and bid not in allowed_movers:
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        goals = set(tasks.get(bid, {}).get("goals", set()))
        if not goals:
            first_move[bid] = None
            continue

        cur = pos
        first_dir: Optional[str] = None
        for t in range(1, HORIZON + 1):
            blocked_t = set(blocked)
            blocked_t.update(reservations[t])
            d = bfs_first_direction(cur, goals, bounds, blocked_t)
            if d is None:
                nxt = cur
            else:
                dx, dy = DIRECTIONS[d]
                nxt = (cur[0] + dx, cur[1] + dy)
                task_kind = tasks.get(bid, {}).get("kind")
                # Late-game: reserve drop-off neighborhood for delivery traffic.
                if drop_buffer_on and task_kind != "deliver" and manhattan(pos, drop) > 1 and manhattan(nxt, drop) <= 1:
                    d = None
                    nxt = cur
                if (nxt, cur, t) in edge_res:
                    d = None
                    nxt = cur

            reservations[t].add(nxt)
            edge_res.add((cur, nxt, t))
            if t == 1:
                first_dir = d
            cur = nxt

        first_move[bid] = first_dir

    # Build final actions
    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        d = first_move.get(bid)
        if d:
            st = state_map.setdefault(bid, BotState())
            # Anti-oscillation: avoid immediate reverse when bot is ping-ponging.
            if st.last_direction and d == reverse_of(st.last_direction) and is_oscillating_positions(st.recent_positions):
                d = None
            if st.last_direction and d == reverse_of(st.last_direction) and st.no_progress_ticks >= 3:
                d = None
            if d:
                st.last_direction = d
                actions[bid] = {"bot": bid, "action": direction_to_action(d)}

    # Anti-corner fallback: if still waiting, try a safe local step.
    # Only treat truly stationary bots as blockers; allow stepping into cells
    # that are being vacated this tick.
    stationary_cells: Set[Tuple[int, int]] = set()
    for bid in bot_ids:
        act = actions[bid].get("action", "")
        if isinstance(act, str) and act.startswith("move_"):
            continue
        p = bot_pos.get(bid)
        if p is not None:
            stationary_cells.add(p)

    occupied_now = set(bot_pos.values())
    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        if allowed_movers is not None and bid not in allowed_movers:
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        task = tasks.get(bid, {"kind": "wait"})
        goals = set(task.get("goals", set()))
        best_dir: Optional[str] = None
        best_score = 10**9
        st = state_map.setdefault(bid, BotState())
        prev_cell = None
        if len(st.recent_positions) >= 2:
            prev_cell = st.recent_positions[-2]
        for n in neighbors(pos, bounds, blocked):
            if n in stationary_cells:
                continue
            task_kind = task.get("kind", "wait")
            if drop_buffer_on and task_kind != "deliver" and manhattan(pos, drop) > 1 and manhattan(n, drop) <= 1:
                continue
            # Prefer cells that improve distance to current goal; else explore.
            d_goal = bfs_distance(n, goals, bounds, blocked) if goals else 0
            score = d_goal
            # If currently too close to drop-off, bias strongly to move away.
            if drop_buffer_on and task_kind != "deliver" and manhattan(pos, drop) <= 1:
                score -= 4 * manhattan(n, drop)
            # Avoid bouncing back into crowded hotspot.
            score += sum(1 for bpos in occupied_now if abs(bpos[0] - n[0]) + abs(bpos[1] - n[1]) <= 1)
            # Strongly discourage local loops when bot has not progressed.
            if prev_cell is not None and n == prev_cell:
                score += 6
            score += sum(1 for rp in st.recent_positions if rp == n) * 2
            if score < best_score:
                dx, dy = n[0] - pos[0], n[1] - pos[1]
                for name, (mx, my) in DIRECTIONS.items():
                    if (dx, dy) == (mx, my):
                        if st.last_direction and name == reverse_of(st.last_direction) and st.no_progress_ticks >= 2:
                            score += 4
                        best_dir = name
                        best_score = score
                        break
        if best_dir:
            st = state_map.setdefault(bid, BotState())
            st.last_direction = best_dir
            actions[bid] = {"bot": bid, "action": direction_to_action(best_dir)}

    # Deadlock breaker: if too many bots are waiting, force a dispersion pass.
    wait_bots = [bid for bid in bot_ids if actions[bid].get("action") == "wait"]
    if len(wait_bots) >= max(4, int(0.6 * len(bot_ids))):
        occupied = set(bot_pos.values())
        claimed_next: Set[Tuple[int, int]] = set()
        for bid in bot_ids:
            act = actions[bid].get("action", "")
            if isinstance(act, str) and act.startswith("move_"):
                pos = bot_pos.get(bid)
                if pos is None:
                    continue
                dname = act.replace("move_", "")
                dx, dy = DIRECTIONS[dname]
                claimed_next.add((pos[0] + dx, pos[1] + dy))

        for bid in wait_bots:
            pos = bot_pos.get(bid)
            if pos is None:
                continue
            task_kind = tasks.get(bid, {}).get("kind", "wait")
            best_dir: Optional[str] = None
            best_score = -10**9
            for name, (dx, dy) in DIRECTIONS.items():
                n = (pos[0] + dx, pos[1] + dy)
                if n in blocked or n in claimed_next:
                    continue
                if n in occupied and n != pos:
                    continue
                # Prefer reducing local crowding. Keep non-delivery away from drop.
                local_crowd = sum(1 for p in occupied if manhattan(p, n) <= 1)
                score = -3 * local_crowd
                if task_kind != "deliver":
                    score += 2 * manhattan(n, drop)
                # Avoid immediate reverse when possible.
                st = state_map.setdefault(bid, BotState())
                if st.last_direction and name == reverse_of(st.last_direction):
                    score -= 2
                if score > best_score:
                    best_score = score
                    best_dir = name
            if best_dir:
                dx, dy = DIRECTIONS[best_dir]
                claimed_next.add((pos[0] + dx, pos[1] + dy))
                st = state_map.setdefault(bid, BotState())
                st.last_direction = best_dir
                actions[bid] = {"bot": bid, "action": direction_to_action(best_dir)}

    # Update per-bot waiting counters for fairness in next tick.
    for bid in bot_ids:
        st = state_map.setdefault(bid, BotState())
        act = actions[bid].get("action", "wait")
        if act == "wait":
            st.wait_ticks += 1
        else:
            st.wait_ticks = 0

    return [actions[bid] for bid in bot_ids]


def decide_actions(data: Dict[str, Any], state_map: Dict[int, BotState]) -> List[Dict[str, Any]]:
    bounds = extract_bounds(data)
    if bounds is None:
        return []
    bots = data.get("bots", [])
    if not bots:
        return []

    bot_ids = sorted(int(b.get("id", i)) for i, b in enumerate(bots))
    bot_pos: Dict[int, Tuple[int, int]] = {}
    bot_inv: Dict[int, List[str]] = {}
    for i, b in enumerate(bots):
        bid = int(b.get("id", i))
        p = parse_xy(b.get("position"))
        if p is not None:
            bot_pos[bid] = p
            st = state_map.setdefault(bid, BotState())
            st.recent_positions.append(p)
        bot_inv[bid] = list(b.get("inventory", []))

    blocked = extract_blocked(data)
    tasks = assign_tasks(data, bot_ids, bot_pos, bot_inv, bounds, blocked, state_map)
    return resolve_moves(data, bot_ids, bot_pos, tasks, bounds, blocked, state_map)


async def run_bot_loop(uri: str, origin: Optional[str], headers: Optional[Dict[str, str]] = None) -> None:
    variants = [
        {"origin": origin, "additional_headers": headers},
        {"origin": origin},
        {"additional_headers": headers},
        {},
    ]

    ws = None
    last_exc: Optional[Exception] = None
    for v in variants:
        kwargs: Dict[str, Any] = {}
        for k, val in v.items():
            if val is None:
                continue
            if k == "additional_headers" and not val:
                continue
            kwargs[k] = val
        try:
            ws = await websockets.connect(uri, **kwargs)
            log.info("CONNECTED kwargs=%s", list(kwargs.keys()))
            break
        except Exception as exc:
            last_exc = exc

    if ws is None:
        raise RuntimeError(f"Unable to connect: {last_exc!r}")

    state_map: Dict[int, BotState] = {}

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except Exception:
                continue

            t = data.get("type")
            if t == "game_over":
                summary = {
                    "score": data.get("score"),
                    "rounds_used": data.get("rounds_used"),
                    "orders_completed": data.get("orders_completed"),
                    "items_delivered": data.get("items_delivered"),
                }
                log.info("GAME OVER: %s", summary)
                return
            if t != "game_state":
                continue

            actions = decide_actions(data, state_map)
            await ws.send(json.dumps({"actions": actions}))
    finally:
        try:
            await ws.close()
        except Exception:
            pass


async def main() -> None:
    load_dotenv()
    uri = os.environ.get("GROCERY_WS", "").strip()
    if not uri:
        raise SystemExit("Set GROCERY_WS in environment or .env")

    token = extract_token_from_uri(uri)

    for origin in ORIGINS:
        try:
            await run_bot_loop(uri, origin=origin, headers=None)
            return
        except Exception as exc:
            log.debug("query-token failed origin=%s: %r", origin, exc)

    if token:
        base = uri.split("?", 1)[0]
        headers = {"Authorization": f"Bearer {token}"}
        for origin in ORIGINS:
            try:
                await run_bot_loop(base, origin=origin, headers=headers)
                return
            except Exception as exc:
                log.debug("bearer failed origin=%s: %r", origin, exc)

    raise SystemExit("No connection variant worked")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
