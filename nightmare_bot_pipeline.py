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
log = logging.getLogger("nightmare-pipeline")
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


@dataclass
class BotState:
    zone: int = 1
    last_direction: Optional[str] = None
    target_item_id: Optional[str] = None
    recent_positions: deque = None
    wait_ticks: int = 0
    oscillation_ticks: int = 0

    def __post_init__(self) -> None:
        if self.recent_positions is None:
            self.recent_positions = deque(maxlen=10)


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
    return token.split("&", 1)[0]


def extract_bounds(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    grid = data.get("grid", {})
    if isinstance(grid, dict) and "width" in grid and "height" in grid:
        return int(grid["width"]), int(grid["height"])
    return None


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


def bfs_distance(start: Tuple[int, int], goals: Set[Tuple[int, int]], bounds: Tuple[int, int], blocked: Set[Tuple[int, int]]) -> int:
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


def bfs_first_direction(start: Tuple[int, int], goals: Set[Tuple[int, int]], bounds: Tuple[int, int], blocked: Set[Tuple[int, int]]) -> Optional[str]:
    if not goals or start in goals:
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


def direction_to_action(direction: str) -> str:
    return {"up": "move_up", "right": "move_right", "down": "move_down", "left": "move_left"}[direction]


def reverse_of(direction: str) -> str:
    return {"up": "down", "down": "up", "left": "right", "right": "left"}[direction]


def get_active_order(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    orders = data.get("orders", [])
    if not orders:
        return None
    active = next((o for o in orders if o.get("status") == "active"), None)
    if active is not None:
        return active
    idx = min(max(int(data.get("active_order_index", 0)), 0), len(orders) - 1)
    return orders[idx]


def bot_zone(x: int, width: int) -> int:
    third = max(1, width // 3)
    if x < third:
        return 0
    if x < 2 * third:
        return 1
    return 2


def is_open_cell(cell: Tuple[int, int], bounds: Tuple[int, int], blocked: Set[Tuple[int, int]]) -> bool:
    return len(neighbors(cell, bounds, blocked)) >= 3


def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def pick_escape_goal(
    pos: Tuple[int, int],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    avoid: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int]]:
    w, h = bounds
    best: Optional[Tuple[int, int]] = None
    best_score = -10**9
    for y in range(h):
        for x in range(w):
            c = (x, y)
            if c in blocked or not is_open_cell(c, bounds, blocked):
                continue
            d = bfs_distance(pos, {c}, bounds, blocked)
            if d >= 10**9:
                continue
            score = min(d, 10)
            if avoid is not None:
                score += abs(c[0] - avoid[0]) + abs(c[1] - avoid[1])
            if score > best_score:
                best_score = score
                best = c
    return best


def pick_waypoint_from_set(
    pos: Tuple[int, int],
    goals: Set[Tuple[int, int]],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
) -> Optional[Tuple[int, int]]:
    best: Optional[Tuple[int, int]] = None
    best_d = -1
    for g in goals:
        if g == pos:
            continue
        d = bfs_distance(pos, {g}, bounds, blocked)
        if d >= 10**9:
            continue
        if d > best_d:
            best_d = d
            best = g
    return best


def is_ping_pong(rp: deque) -> bool:
    vals = list(rp)
    if len(vals) < 6:
        return False
    a, b, c, d, e, f = vals[-1], vals[-2], vals[-3], vals[-4], vals[-5], vals[-6]
    return a == c == e and b == d == f and a != b


def choose_step_with_penalty(
    bid: int,
    rnd: int,
    pos: Tuple[int, int],
    goals: Set[Tuple[int, int]],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    st: BotState,
) -> Optional[str]:
    best_dir: Optional[str] = None
    best_score = 10**9
    prev_cell = st.recent_positions[-2] if len(st.recent_positions) >= 2 else None
    last_cell = st.recent_positions[-1] if len(st.recent_positions) >= 1 else None
    ping = is_ping_pong(st.recent_positions)
    for name, (dx, dy) in DIRECTIONS.items():
        n = (pos[0] + dx, pos[1] + dy)
        if n in blocked:
            continue
        d = bfs_distance(n, goals, bounds, blocked) if goals else 0
        if d >= 10**9:
            continue
        score = d * 10
        if st.last_direction and name == reverse_of(st.last_direction):
            score += 8
        if prev_cell is not None and n == prev_cell:
            score += 12
        if ping and last_cell is not None and prev_cell is not None and n in {last_cell, prev_cell}:
            score += 25
        score += sum(1 for rp in st.recent_positions if rp == n) * 3
        # Deterministic tie-break to avoid synchronized oscillation.
        score += ((bid * 31 + rnd * 17 + n[0] * 7 + n[1] * 11) % 5)
        if score < best_score:
            best_score = score
            best_dir = name
    return best_dir


def choose_side_step(
    bid: int,
    rnd: int,
    pos: Tuple[int, int],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    st: BotState,
) -> Optional[str]:
    best_dir: Optional[str] = None
    best_score = 10**9
    prev_cell = st.recent_positions[-2] if len(st.recent_positions) >= 2 else None
    for name, (dx, dy) in DIRECTIONS.items():
        n = (pos[0] + dx, pos[1] + dy)
        if n in blocked:
            continue
        score = sum(1 for rp in st.recent_positions if rp == n) * 4
        if prev_cell is not None and n == prev_cell:
            score += 8
        if st.last_direction and name == reverse_of(st.last_direction):
            score += 6
        score += ((bid * 19 + rnd * 13 + n[0] * 5 + n[1] * 7) % 5)
        if score < best_score:
            best_score = score
            best_dir = name
    return best_dir


def decide_actions(data: Dict[str, Any], state_map: Dict[int, BotState]) -> List[Dict[str, Any]]:
    bounds = extract_bounds(data)
    if bounds is None:
        return []
    w, _ = bounds

    bots = data.get("bots", [])
    if not bots:
        return []

    blocked = extract_blocked(data)
    drop = parse_xy(data.get("drop_off")) or (1, 1)
    rnd = int(data.get("round", 0) or 0)
    active = get_active_order(data)
    late_push = rnd >= 350
    if active is None:
        return [{"bot": int(b.get("id", i)), "action": "wait"} for i, b in enumerate(bots)]

    required = Counter(str(x) for x in active.get("items_required", []))
    delivered = Counter(str(x) for x in active.get("items_delivered", []))
    remaining = +(required - delivered)
    remaining_total = sum(remaining.values())
    burst_deliver = remaining_total <= 2

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
            st.zone = bot_zone(p[0], w)
            if is_ping_pong(st.recent_positions):
                st.oscillation_ticks += 1
            else:
                st.oscillation_ticks = 0
        bot_inv[bid] = [str(x) for x in b.get("inventory", []) if x is not None]

    actions: Dict[int, Dict[str, Any]] = {bid: {"bot": bid, "action": "wait"} for bid in bot_ids}

    # Immediate drop / pick.
    items = data.get("items", [])
    item_lookup = {str(it.get("id")): it for it in items if it.get("id") is not None}
    reserved_pick: Set[str] = set()

    for bid in bot_ids:
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        inv = bot_inv.get(bid, [])
        has_deliverable = any(remaining.get(it, 0) > 0 for it in inv)
        if has_deliverable and pos == drop:
            actions[bid] = {"bot": bid, "action": "drop_off"}
            continue

        st = state_map.setdefault(bid, BotState())
        if st.target_item_id:
            cur = item_lookup.get(st.target_item_id)
            ip = parse_xy(cur.get("position")) if cur else None
            if ip is not None and abs(ip[0] - pos[0]) + abs(ip[1] - pos[1]) == 1 and st.target_item_id not in reserved_pick and len(inv) < 3:
                reserved_pick.add(st.target_item_id)
                actions[bid] = {"bot": bid, "action": "pick_up", "item_id": st.target_item_id}
                st.target_item_id = None

    # Assign targets.
    carrying = Counter()
    for bid in bot_ids:
        for it in bot_inv.get(bid, []):
            if remaining.get(it, 0) > carrying[it]:
                carrying[it] += 1
    missing = +(remaining - carrying)

    goals_by_bot: Dict[int, Set[Tuple[int, int]]] = {}
    claimed_items: Set[str] = set(reserved_pick)

    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        st = state_map.setdefault(bid, BotState())
        inv = bot_inv.get(bid, [])

        if any(remaining.get(it, 0) > 0 for it in inv):
            goals_by_bot[bid] = {drop}
            continue

        best: Optional[Tuple[int, str, Set[Tuple[int, int]]]] = None
        for item in items:
            iid = item.get("id")
            itype = item.get("type")
            ipos = parse_xy(item.get("position"))
            if iid is None or itype is None or ipos is None:
                continue
            iid_s = str(iid)
            itype_s = str(itype)
            if missing.get(itype_s, 0) <= 0 or iid_s in claimed_items:
                continue
            goals = set(neighbors(ipos, bounds, blocked))
            if not goals:
                continue
            d = bfs_distance(pos, goals, bounds, blocked)
            if d >= 10**9:
                continue
            zone_pen = 0 if bot_zone(ipos[0], w) == st.zone else 6
            score = d * 10 + zone_pen
            if best is None or score < best[0]:
                best = (score, iid_s, goals)
        if best is not None:
            _, iid_s, goals = best
            st.target_item_id = iid_s
            claimed_items.add(iid_s)
            goals_by_bot[bid] = goals
            continue

        # Late push: avoid patrol noise; stay put unless we have actionable work.
        if late_push:
            goals_by_bot[bid] = {pos}
            continue

        # Patrol: stay in own zone and away from drop-off choke.
        zone_cells: Set[Tuple[int, int]] = set()
        for y in range(bounds[1]):
            for x in range(bounds[0]):
                c = (x, y)
                if c in blocked:
                    continue
                if bot_zone(x, w) != st.zone:
                    continue
                if abs(x - drop[0]) + abs(y - drop[1]) <= 1:
                    continue
                zone_cells.add(c)
        if zone_cells:
            wp = pick_waypoint_from_set(pos, zone_cells, bounds, blocked)
            goals_by_bot[bid] = {wp} if wp is not None else {pos}
        else:
            goals_by_bot[bid] = {pos}

    # Escape override for bots that are repeatedly waiting.
    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        st = state_map.setdefault(bid, BotState())
        if st.wait_ticks < 3:
            continue
        esc = pick_escape_goal(pos, bounds, blocked, avoid=drop)
        if esc is not None:
            goals_by_bot[bid] = {esc}
        continue
        
    # Strong escape override for persistent ping-pong.
    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        st = state_map.setdefault(bid, BotState())
        if st.oscillation_ticks < 2:
            continue
        esc = pick_escape_goal(pos, bounds, blocked, avoid=drop)
        if esc is not None:
            goals_by_bot[bid] = {esc}

    # Move reservation.
    reserved_next: Set[Tuple[int, int]] = set()
    moving_from_to: Set[Tuple[Tuple[int, int], Tuple[int, int]]] = set()
    soft_drop_cap = 3
    near_drop_non_delivery = 0
    for bid in bot_ids:
        p = bot_pos.get(bid)
        if p is None or manhattan(p, drop) > 2:
            continue
        inv = bot_inv.get(bid, [])
        has_deliverable = any(remaining.get(it, 0) > 0 for it in inv)
        if not has_deliverable:
            near_drop_non_delivery += 1

    ordered = sorted(
        bot_ids,
        key=lambda bid: (
            0 if goals_by_bot.get(bid) == {drop} else 1,
            bid,
        ),
    )

    for bid in ordered:
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        goals = goals_by_bot.get(bid, set())
        if not goals:
            continue
        if pos in goals and len(goals) > 1:
            alt = pick_waypoint_from_set(pos, goals, bounds, blocked)
            if alt is not None:
                goals = {alt}
        local_blocked = set(blocked)
        local_blocked.update(reserved_next)

        st = state_map.setdefault(bid, BotState())
        d = bfs_first_direction(pos, goals, bounds, local_blocked)
        if d is None or (st.last_direction and d == reverse_of(st.last_direction) and st.wait_ticks >= 1):
            d = choose_step_with_penalty(bid, rnd, pos, goals, bounds, local_blocked, st)
        if d is None:
            continue
        nx, ny = pos[0] + DIRECTIONS[d][0], pos[1] + DIRECTIONS[d][1]
        np = (nx, ny)
        if (np, pos) in moving_from_to:
            continue
        inv = bot_inv.get(bid, [])
        has_deliverable = any(remaining.get(it, 0) > 0 for it in inv) or goals == {drop}
        if not burst_deliver and not has_deliverable and manhattan(np, drop) <= 2 and near_drop_non_delivery >= soft_drop_cap:
            continue
        reserved_next.add(np)
        moving_from_to.add((pos, np))
        if not has_deliverable and manhattan(np, drop) <= 2:
            near_drop_non_delivery += 1
        st.last_direction = d
        actions[bid] = {"bot": bid, "action": direction_to_action(d)}

    # Queue jitter: avoid waits when conflict blocked a forward move.
    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        st = state_map.setdefault(bid, BotState())
        blocked_side = set(blocked) | reserved_next
        d2 = choose_side_step(bid, rnd, pos, bounds, blocked_side, st)
        if d2 is None:
            continue
        nx, ny = pos[0] + DIRECTIONS[d2][0], pos[1] + DIRECTIONS[d2][1]
        np = (nx, ny)
        if np in reserved_next or (np, pos) in moving_from_to:
            continue
        reserved_next.add(np)
        moving_from_to.add((pos, np))
        st.last_direction = d2
        actions[bid] = {"bot": bid, "action": direction_to_action(d2)}

    for bid in bot_ids:
        st = state_map.setdefault(bid, BotState())
        if actions[bid]["action"] == "wait":
            st.wait_ticks += 1
        else:
            st.wait_ticks = 0

    return [actions[bid] for bid in bot_ids]


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
            break
        except Exception as exc:
            last_exc = exc
    if ws is None:
        raise RuntimeError(f"Unable to connect: {last_exc!r}")

    state_map: Dict[int, BotState] = {}
    try:
        async for raw in ws:
            data = json.loads(raw)
            t = data.get("type")
            if t == "game_over":
                log.info("GAME OVER score=%s orders=%s items=%s", data.get("score"), data.get("orders_completed"), data.get("items_delivered"))
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
    uri = os.environ.get("GROCERY_WS", "").strip()
    if not uri:
        raise SystemExit("Set GROCERY_WS")
    token = extract_token_from_uri(uri)
    for origin in ORIGINS:
        try:
            await run_bot_loop(uri, origin=origin, headers=None)
            return
        except Exception:
            pass
    if token:
        base = uri.split("?", 1)[0]
        headers = {"Authorization": f"Bearer {token}"}
        for origin in ORIGINS:
            try:
                await run_bot_loop(base, origin=origin, headers=headers)
                return
            except Exception:
                pass
    raise SystemExit("No connection variant worked")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
