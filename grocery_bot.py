import asyncio
import json
import logging
import os
from collections import Counter, defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple

import websockets

logging.basicConfig(
    level=os.environ.get("GROCERY_LOG_LEVEL", "WARNING"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("grocery-bot")
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

ROUND_LOG_EVERY = int(os.environ.get("GROCERY_ROUND_LOG_EVERY", "25"))
MAX_DROP_TRAVELERS = int(os.environ.get("GROCERY_MAX_DROP_TRAVELERS", "2"))
ACTIVE_DROP_WEIGHT = float(os.environ.get("GROCERY_ACTIVE_DROP_WEIGHT", "0.35"))
PREVIEW_DROP_WEIGHT = float(os.environ.get("GROCERY_PREVIEW_DROP_WEIGHT", "0.55"))
NIGHTMARE_MIN_BOTS = int(os.environ.get("GROCERY_NIGHTMARE_MIN_BOTS", "8"))
NIGHTMARE_STRICT = os.environ.get("GROCERY_NIGHTMARE_STRICT", "1").strip() not in ("0", "false", "False")
NIGHTMARE_MAX_MOVERS = int(os.environ.get("GROCERY_NIGHTMARE_MAX_MOVERS", "5"))


class GameStats:
    def __init__(self) -> None:
        self.total_actions = 0
        self.wait_actions = 0
        self.pickup_actions = 0
        self.drop_actions = 0
        self.stuck_events = 0
        self.carry_start_round: Dict[int, int] = {}
        self.carry_durations: List[int] = []


class BotState:
    def __init__(self) -> None:
        self.visits: Dict[Tuple[int, int], int] = defaultdict(int)
        self.last_direction: Optional[str] = None
        self.target_item_id: Optional[str] = None
        self.target_item_type: Optional[str] = None
        self.target_lock_ticks: int = 0
        self.recent_positions: deque[Tuple[int, int]] = deque(maxlen=8)
        self.carry_rounds: int = 0


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


def extract_token_from_uri(uri: str) -> Optional[str]:
    if "token=" not in uri:
        return None
    token = uri.split("token=", 1)[1]
    if "&" in token:
        token = token.split("&", 1)[0]
    return token


def parse_xy(node: Any) -> Optional[Tuple[int, int]]:
    if isinstance(node, dict):
        if "x" in node and "y" in node:
            return int(node["x"]), int(node["y"])
        if "position" in node:
            return parse_xy(node["position"])
    if isinstance(node, (list, tuple)) and len(node) >= 2:
        return int(node[0]), int(node[1])
    return None


def extract_bounds(data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    grid = data.get("grid", {})
    if isinstance(grid, dict) and "width" in grid and "height" in grid:
        return int(grid["width"]), int(grid["height"])
    for width_key, height_key in (("width", "height"), ("grid_width", "grid_height"), ("cols", "rows")):
        if width_key in data and height_key in data:
            return int(data[width_key]), int(data[height_key])
    return None


def extract_walls_and_shelves(data: Dict[str, Any]) -> Set[Tuple[int, int]]:
    blocked: Set[Tuple[int, int]] = set()
    grid = data.get("grid", {})
    if isinstance(grid, dict):
        for wall in grid.get("walls", []):
            xy = parse_xy(wall)
            if xy is not None:
                blocked.add(xy)
    for item in data.get("items", []):
        xy = parse_xy(item.get("position"))
        if xy is not None:
            blocked.add(xy)
    return blocked


def reverse_of(direction: str) -> str:
    return {"up": "down", "down": "up", "left": "right", "right": "left"}[direction]


def direction_to_action(direction: str) -> str:
    return {"up": "move_up", "down": "move_down", "left": "move_left", "right": "move_right"}[direction]


def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def is_oscillating(state: BotState) -> bool:
    rp = list(state.recent_positions)
    if len(rp) < 6:
        return False
    if rp[-1] == rp[-3] == rp[-5] and rp[-2] == rp[-4] == rp[-6] and rp[-1] != rp[-2]:
        return True
    return len(set(rp[-6:])) <= 2


def adjacent_walkable_cells(
    target: Tuple[int, int], blocked: Set[Tuple[int, int]], bounds: Tuple[int, int]
) -> Set[Tuple[int, int]]:
    width, height = bounds
    cells: Set[Tuple[int, int]] = set()
    for dx, dy in DIRECTIONS.values():
        x, y = target[0] + dx, target[1] + dy
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        if (x, y) in blocked:
            continue
        cells.add((x, y))
    return cells


def bfs_first_direction(
    start: Tuple[int, int], goals: Set[Tuple[int, int]], blocked: Set[Tuple[int, int]], bounds: Tuple[int, int]
) -> Optional[str]:
    if not goals or start in goals:
        return None

    width, height = bounds
    q = deque([start])
    prev: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {start: None}

    while q:
        x, y = q.popleft()
        cur = (x, y)
        if cur in goals:
            cursor = cur
            while prev[cursor] is not None and prev[cursor] != start:
                cursor = prev[cursor]
            parent = prev[cursor]
            if parent is None:
                return None
            dx, dy = cursor[0] - parent[0], cursor[1] - parent[1]
            for name, (mx, my) in DIRECTIONS.items():
                if (dx, dy) == (mx, my):
                    return name
            return None

        for name in ("up", "right", "down", "left"):
            dx, dy = DIRECTIONS[name]
            nx, ny = x + dx, y + dy
            np = (nx, ny)
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if np in blocked or np in prev:
                continue
            prev[np] = cur
            q.append(np)

    return None


def bfs_shortest_distance(
    start: Tuple[int, int], goals: Set[Tuple[int, int]], blocked: Set[Tuple[int, int]], bounds: Tuple[int, int]
) -> Optional[int]:
    if not goals:
        return None
    if start in goals:
        return 0
    width, height = bounds
    q = deque([(start, 0)])
    seen = {start}
    while q:
        (x, y), d = q.popleft()
        for dx, dy in DIRECTIONS.values():
            nx, ny = x + dx, y + dy
            np = (nx, ny)
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if np in blocked or np in seen:
                continue
            if np in goals:
                return d + 1
            seen.add(np)
            q.append((np, d + 1))
    return None


def bfs_distance_map(
    start: Tuple[int, int], blocked: Set[Tuple[int, int]], bounds: Tuple[int, int]
) -> Dict[Tuple[int, int], int]:
    width, height = bounds
    q = deque([start])
    dist = {start: 0}
    while q:
        x, y = q.popleft()
        d = dist[(x, y)]
        for dx, dy in DIRECTIONS.values():
            nx, ny = x + dx, y + dy
            np = (nx, ny)
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if np in blocked or np in dist:
                continue
            dist[np] = d + 1
            q.append(np)
    return dist


def choose_fallback_direction(
    pos: Tuple[int, int], blocked: Set[Tuple[int, int]], bounds: Tuple[int, int], state: BotState
) -> Optional[str]:
    width, height = bounds
    candidates: List[Tuple[float, str]] = []
    for direction, (dx, dy) in DIRECTIONS.items():
        nx, ny = pos[0] + dx, pos[1] + dy
        if nx < 0 or ny < 0 or nx >= width or ny >= height:
            continue
        if (nx, ny) in blocked:
            continue
        score = float(state.visits[(nx, ny)])
        score += 0.9 * float(sum(1 for p in state.recent_positions if p == (nx, ny)))
        if state.last_direction and direction == reverse_of(state.last_direction):
            score += 0.5
        candidates.append((score, direction))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def find_item_by_id(items: List[Dict[str, Any]], item_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not item_id:
        return None
    for item in items:
        if str(item.get("id")) == str(item_id):
            return item
    return None


def get_bot_entry_by_id(data: Dict[str, Any], bot_id: int) -> Optional[Dict[str, Any]]:
    bots = data.get("bots", [])
    for i, b in enumerate(bots):
        if int(b.get("id", i)) == bot_id:
            return b
    return None


def next_cell(pos: Tuple[int, int], direction: str) -> Tuple[int, int]:
    dx, dy = DIRECTIONS[direction]
    return (pos[0] + dx, pos[1] + dy)


def active_order_from_state(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    orders = data.get("orders", [])
    if not orders:
        return None
    active_idx = int(data.get("active_order_index", 0))
    active = next((o for o in orders if o.get("status") == "active"), None)
    if active is not None:
        return active
    return orders[min(max(active_idx, 0), len(orders) - 1)]


def choose_active_item_for_bot(
    data: Dict[str, Any],
    bot_pos: Tuple[int, int],
    missing_active: Counter,
    reserved_item_ids: Set[str],
    blocked: Set[Tuple[int, int]],
    bounds: Tuple[int, int],
) -> Optional[Dict[str, Any]]:
    dist_from_pos = bfs_distance_map(bot_pos, blocked, bounds)
    drop = parse_xy(data.get("drop_off")) or (1, 1)
    dist_from_drop = bfs_distance_map(drop, blocked, bounds)
    # Completion bonus: strong preference for item types with count=1.
    item_bonus: Dict[str, float] = {}
    for t, c in missing_active.items():
        if c <= 0:
            continue
        b = 8.0 / float(c)
        if c == 1:
            b += 24.0
        item_bonus[str(t)] = b
    return select_best_item_target(
        items=data.get("items", []),
        needed_counter=missing_active,
        dist_from_pos=dist_from_pos,
        dist_from_drop=dist_from_drop,
        blocked=blocked,
        bounds=bounds,
        drop_weight=0.22,
        reserved_item_ids=reserved_item_ids,
        item_bonus=item_bonus,
    )


def plan_actions_nightmare_mapf(
    data: Dict[str, Any],
    bot_states: Dict[int, BotState],
    bot_ids: List[int],
    bot_id_to_index: Dict[int, int],
    horizon: int = 3,
) -> List[Dict[str, Any]]:
    bounds = extract_bounds(data)
    if bounds is None:
        return [{"bot": bid, "action": "wait"} for bid in bot_ids]
    blocked_static = extract_walls_and_shelves(data)
    active_order = active_order_from_state(data)
    if active_order is None:
        return [{"bot": bid, "action": "wait"} for bid in bot_ids]

    required = Counter(active_order.get("items_required", []))
    delivered = Counter(active_order.get("items_delivered", []))
    remaining = +(required - delivered)
    drop = parse_xy(data.get("drop_off")) or (1, 1)

    bot_pos: Dict[int, Tuple[int, int]] = {}
    bot_inv: Dict[int, List[str]] = {}
    for bid in bot_ids:
        b = get_bot_entry_by_id(data, bid)
        if b is None:
            continue
        p = parse_xy(b.get("position"))
        if p is None:
            continue
        bot_pos[bid] = p
        bot_inv[bid] = list(b.get("inventory", []))

    # Remaining missing after accounting for currently carried deliverables.
    carried = Counter()
    for bid in bot_ids:
        for it in bot_inv.get(bid, []):
            if remaining[it] > carried[it]:
                carried[it] += 1
    missing_active = +(remaining - carried)

    # Immediate actions first: drop/pick when possible.
    actions_by_bot: Dict[int, Dict[str, Any]] = {}
    item_claims: Set[str] = set()
    for bid in bot_ids:
        st = bot_states.setdefault(bid, BotState())
        pos = bot_pos.get(bid)
        inv = bot_inv.get(bid, [])
        if pos is None:
            actions_by_bot[bid] = {"bot": bid, "action": "wait"}
            continue
        has_deliverable = any(remaining[it] > 0 for it in inv)
        if has_deliverable and pos == drop:
            st.target_item_id = None
            st.target_item_type = None
            actions_by_bot[bid] = {"bot": bid, "action": "drop_off"}
            continue
        # If adjacent to existing active target, pick immediately.
        cur = find_item_by_id(data.get("items", []), st.target_item_id)
        if cur is not None and len(inv) < 3:
            ipos = parse_xy(cur.get("position"))
            iid = str(cur.get("id")) if cur.get("id") is not None else None
            itype = cur.get("type")
            if ipos is not None and iid and manhattan(pos, ipos) == 1 and iid not in item_claims and missing_active[itype] > 0:
                item_claims.add(iid)
                st.target_item_id = None
                st.target_item_type = None
                actions_by_bot[bid] = {"bot": bid, "action": "pick_up", "item_id": iid}
                continue

    # Task target per bot.
    goals_by_bot: Dict[int, Set[Tuple[int, int]]] = {}
    dist_to_goal: Dict[int, int] = {}
    reserved_item_ids: Set[str] = set(item_claims)

    for bid in bot_ids:
        if bid in actions_by_bot:
            continue
        pos = bot_pos.get(bid)
        inv = bot_inv.get(bid, [])
        if pos is None:
            actions_by_bot[bid] = {"bot": bid, "action": "wait"}
            continue

        has_deliverable = any(remaining[it] > 0 for it in inv)
        if has_deliverable:
            goals_by_bot[bid] = {drop}
            d = bfs_shortest_distance(pos, {drop}, blocked_static, bounds)
            dist_to_goal[bid] = d if d is not None else 10**9
            continue

        # Assign active pickup target.
        picked = choose_active_item_for_bot(
            data=data,
            bot_pos=pos,
            missing_active=missing_active,
            reserved_item_ids=reserved_item_ids,
            blocked=blocked_static,
            bounds=bounds,
        )
        if picked is None:
            actions_by_bot[bid] = {"bot": bid, "action": "wait"}
            continue
        st = bot_states.setdefault(bid, BotState())
        st.target_item_id = str(picked["id"])
        st.target_item_type = str(picked["type"])
        reserved_item_ids.add(str(picked["id"]))
        ipos = picked["position"]
        goals = adjacent_walkable_cells(ipos, blocked_static, bounds)
        if not goals:
            actions_by_bot[bid] = {"bot": bid, "action": "wait"}
            continue
        goals_by_bot[bid] = goals
        d = bfs_shortest_distance(pos, goals, blocked_static, bounds)
        dist_to_goal[bid] = d if d is not None else 10**9

    # 3-step reservation planning.
    reservations: Dict[int, Set[Tuple[int, int]]] = {t: set() for t in range(horizon + 1)}
    edge_res: Set[Tuple[Tuple[int, int], Tuple[int, int], int]] = set()
    for bid, p in bot_pos.items():
        reservations[0].add(p)

    order = sorted(
        [bid for bid in bot_ids if bid not in actions_by_bot],
        key=lambda b: (dist_to_goal.get(b, 10**9), b),
    )

    first_move: Dict[int, Optional[str]] = {}
    final_pos_at_t: Dict[int, Dict[int, Tuple[int, int]]] = {}

    for bid in order:
        start = bot_pos[bid]
        goals = goals_by_bot.get(bid, set())
        if not goals:
            first_move[bid] = None
            continue

        curr = start
        path_dirs: List[str] = []
        pos_by_t: Dict[int, Tuple[int, int]] = {}

        for t in range(1, horizon + 1):
            blocked_t = set(blocked_static)
            blocked_t.update(reservations[t])
            direction = bfs_first_direction(curr, goals, blocked_t, bounds)
            if direction is None:
                # Try wait.
                nxt = curr
            else:
                nxt = next_cell(curr, direction)
                # swap avoidance
                if (nxt, curr, t) in edge_res:
                    direction = None
                    nxt = curr

            # Reserve and continue.
            reservations[t].add(nxt)
            edge_res.add((curr, nxt, t))
            pos_by_t[t] = nxt
            if t == 1:
                path_dirs.append(direction if direction is not None else "")
            curr = nxt

        final_pos_at_t[bid] = pos_by_t
        first_move[bid] = path_dirs[0] if path_dirs else None

    for bid in bot_ids:
        if bid in actions_by_bot:
            continue
        d = first_move.get(bid)
        if d:
            actions_by_bot[bid] = {"bot": bid, "action": direction_to_action(d)}
        else:
            actions_by_bot[bid] = {"bot": bid, "action": "wait"}

    return [actions_by_bot.get(bid, {"bot": bid, "action": "wait"}) for bid in bot_ids]


def select_best_item_target(
    items: List[Dict[str, Any]],
    needed_counter: Counter,
    dist_from_pos: Dict[Tuple[int, int], int],
    dist_from_drop: Dict[Tuple[int, int], int],
    blocked: Set[Tuple[int, int]],
    bounds: Tuple[int, int],
    drop_weight: float,
    reserved_item_ids: Optional[Set[str]] = None,
    zone_min_x: Optional[int] = None,
    zone_max_x: Optional[int] = None,
    zone_penalty: float = 0.0,
    item_bonus: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    reserved_item_ids = reserved_item_ids or set()
    item_bonus = item_bonus or {}
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for item in items:
        item_type = item.get("type")
        item_id = str(item.get("id")) if item.get("id") is not None else None
        item_pos = parse_xy(item.get("position"))
        if not item_type or not item_id or item_pos is None:
            continue
        if item_id in reserved_item_ids:
            continue
        if needed_counter[item_type] <= 0:
            continue
        goals = adjacent_walkable_cells(item_pos, blocked, bounds)
        if not goals:
            continue
        pos_d = [dist_from_pos[g] for g in goals if g in dist_from_pos]
        if not pos_d:
            continue
        drop_d = [dist_from_drop[g] for g in goals if g in dist_from_drop]
        if not drop_d:
            continue
        d_pos = min(pos_d)
        d_drop = min(drop_d)
        score = d_pos + drop_weight * d_drop
        score -= float(item_bonus.get(str(item_type), 0.0))
        if zone_min_x is not None and zone_max_x is not None:
            if item_pos[0] < zone_min_x or item_pos[0] > zone_max_x:
                score += zone_penalty
        payload = {"id": item_id, "type": item_type, "position": item_pos}
        if best is None or score < best[0]:
            best = (score, payload)
    return best[1] if best else None


def compute_role_zone(
    bot_ids: List[int],
    bot_id: int,
    width: int,
    courier_count: int,
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    if not bot_ids:
        return False, None
    sorted_ids = sorted(bot_ids)
    courier_ids = set(sorted_ids[-courier_count:]) if courier_count > 0 else set()
    if bot_id in courier_ids:
        return True, None

    picker_ids = [bid for bid in sorted_ids if bid not in courier_ids]
    if not picker_ids:
        return False, None
    idx = picker_ids.index(bot_id)
    zone_count = len(picker_ids)
    start = int(idx * width / zone_count)
    end = int(((idx + 1) * width / zone_count) - 1)
    if idx == zone_count - 1:
        end = width - 1
    return False, (max(0, start), min(width - 1, end))


def choose_action(
    data: Dict[str, Any],
    state: BotState,
    bot_id: int,
    reserved_item_ids: Optional[Set[str]] = None,
    max_drop_travelers: int = MAX_DROP_TRAVELERS,
    active_drop_weight: float = ACTIVE_DROP_WEIGHT,
    preview_drop_weight: float = PREVIEW_DROP_WEIGHT,
    stats: Optional[GameStats] = None,
) -> Dict[str, Any]:
    reserved_item_ids = reserved_item_ids or set()
    bots = data.get("bots", [])
    if not bots:
        return {"bot": bot_id, "action": "wait"}

    bot_entry = next((b for i, b in enumerate(bots) if int(b.get("id", i)) == bot_id), None)
    if bot_entry is None:
        return {"bot": bot_id, "action": "wait"}

    pos = parse_xy(bot_entry.get("position"))
    if pos is None:
        return {"bot": bot_id, "action": "wait"}

    inv = list(bot_entry.get("inventory", []))
    bounds = extract_bounds(data)
    if bounds is None:
        return {"bot": bot_id, "action": "wait"}
    width, _height = bounds
    round_num = int(data.get("round", 0))
    bots = data.get("bots", [])
    bot_count = len(bots)
    endgame_mode = round_num >= 240

    blocked = extract_walls_and_shelves(data)
    for other in bots:
        oid = int(other.get("id", -1))
        if oid == bot_id:
            continue
        opos = parse_xy(other.get("position"))
        if opos is not None:
            blocked.add(opos)

    state.visits[pos] += 1
    state.recent_positions.append(pos)

    active_idx = int(data.get("active_order_index", 0))
    orders = data.get("orders", [])
    if not orders:
        direction = choose_fallback_direction(pos, blocked, bounds, state)
        return {"bot": bot_id, "action": direction_to_action(direction)} if direction else {"bot": bot_id, "action": "wait"}

    active_order = next((o for o in orders if o.get("status") == "active"), None)
    if active_order is None:
        active_order = orders[min(max(active_idx, 0), len(orders) - 1)]

    preview_order = next((o for o in orders if o.get("status") == "preview"), None)

    required = Counter(active_order.get("items_required", []))
    delivered = Counter(active_order.get("items_delivered", []))
    active_remaining = +(required - delivered)
    inv_counter = Counter(inv)
    missing_active = +(active_remaining - inv_counter)

    preview_missing = Counter(preview_order.get("items_required", [])) if preview_order else Counter()
    preview_missing = +(preview_missing - inv_counter)

    drop = parse_xy(data.get("drop_off")) or (1, 1)
    has_deliverable = any(active_remaining[it] > 0 for it in inv)
    if has_deliverable:
        state.carry_rounds += 1
    else:
        state.carry_rounds = 0
    items = data.get("items", [])

    # Dynamic profile for harder/high-bot modes.
    bot_ids = sorted(int(b.get("id", i)) for i, b in enumerate(bots))
    nightmare_profile = bot_count >= NIGHTMARE_MIN_BOTS or (width * _height) >= 360
    if nightmare_profile:
        courier_count = max(2, bot_count // 4)
        local_max_drop_travelers = max(max_drop_travelers, min(4, courier_count + 1))
        local_active_drop_weight = min(active_drop_weight, 0.28)
        local_preview_drop_weight = max(preview_drop_weight, 0.70)
        carry_force_rounds = 5
        zone_penalty_active = 2.8
        zone_penalty_preview = 2.0
        local_endgame_round = 220
        preview_enabled = False if NIGHTMARE_STRICT else True
    else:
        courier_count = 1 if bot_count >= 3 else 0
        local_max_drop_travelers = max_drop_travelers
        local_active_drop_weight = active_drop_weight
        local_preview_drop_weight = preview_drop_weight
        carry_force_rounds = 8
        zone_penalty_active = 2.0
        zone_penalty_preview = 1.2
        local_endgame_round = 240
        preview_enabled = True

    endgame_mode = round_num >= local_endgame_round
    is_courier, zone = compute_role_zone(bot_ids, bot_id, width, courier_count)
    zone_min_x = zone[0] if zone else None
    zone_max_x = zone[1] if zone else None

    # Do not hard-block drop area; it caused global deadlocks in nightmare.

    if pos == drop and has_deliverable:
        state.target_item_id = None
        state.target_item_type = None
        return {"bot": bot_id, "action": "drop_off"}

    dist_from_pos = bfs_distance_map(pos, blocked, bounds)
    dist_from_drop = bfs_distance_map(drop, blocked, bounds)

    deliverable_bots: List[Tuple[int, int]] = []
    for i, other in enumerate(bots):
        oid = int(other.get("id", i))
        opos = parse_xy(other.get("position"))
        if opos is None:
            continue
        oinv = list(other.get("inventory", []))
        if not any(active_remaining[it] > 0 for it in oinv):
            continue
        dmap = bfs_distance_map(opos, blocked, bounds)
        ddrop = dmap.get(drop, 10**9)
        deliverable_bots.append((ddrop, oid))
    deliverable_bots.sort(key=lambda x: (x[0], x[1]))
    allowed_drop_ids = {bid for _, bid in deliverable_bots[:local_max_drop_travelers]}

    current_target = find_item_by_id(items, state.target_item_id)
    target_valid = False
    if current_target is not None:
        t_id = str(current_target.get("id"))
        t_type = current_target.get("type")
        if t_id not in reserved_item_ids and isinstance(t_type, str):
            if missing_active[t_type] > 0:
                target_valid = True
            elif not missing_active and has_deliverable and len(inv) < 3 and preview_missing[t_type] > 0:
                target_valid = True

    oscillating = is_oscillating(state)
    if oscillating and stats is not None:
        stats.stuck_events += 1

    keep_locked_target = (
        nightmare_profile
        and NIGHTMARE_STRICT
        and current_target is not None
        and state.target_lock_ticks > 0
        and not oscillating
    )

    if (not target_valid or oscillating) and not keep_locked_target:
        state.target_item_id = None
        state.target_item_type = None
        state.target_lock_ticks = 0
        current_target = None

    if current_target is None and len(inv) < 3 and missing_active:
        # Push hard to close current active order quickly.
        item_bonus: Dict[str, float] = {}
        for t, c in missing_active.items():
            if c <= 0:
                continue
            bonus = 8.0 / float(c)
            if c == 1:
                bonus += 18.0
            item_bonus[str(t)] = bonus
        picked = select_best_item_target(
            items=items,
            needed_counter=missing_active,
            dist_from_pos=dist_from_pos,
            dist_from_drop=dist_from_drop,
            blocked=blocked,
            bounds=bounds,
            drop_weight=local_active_drop_weight,
            reserved_item_ids=reserved_item_ids,
            zone_min_x=zone_min_x,
            zone_max_x=zone_max_x,
            zone_penalty=zone_penalty_active if zone_min_x is not None else 0.0,
            item_bonus=item_bonus,
        )
        if picked:
            state.target_item_id = str(picked["id"])
            state.target_item_type = str(picked["type"])
            state.target_lock_ticks = 8 if nightmare_profile and NIGHTMARE_STRICT else 0
            current_target = find_item_by_id(items, state.target_item_id)

    # Only pre-pick preview when active is fully covered and inventory is light.
    if (
        current_target is None
        and len(inv) <= 1
        and has_deliverable
        and not missing_active
        and preview_missing
        and not endgame_mode
        and not is_courier
        and preview_enabled
    ):
        picked = select_best_item_target(
            items=items,
            needed_counter=preview_missing,
            dist_from_pos=dist_from_pos,
            dist_from_drop=dist_from_drop,
            blocked=blocked,
            bounds=bounds,
            drop_weight=local_preview_drop_weight,
            reserved_item_ids=reserved_item_ids,
            zone_min_x=zone_min_x,
            zone_max_x=zone_max_x,
            zone_penalty=zone_penalty_preview if zone_min_x is not None else 0.0,
        )
        if picked:
            state.target_item_id = str(picked["id"])
            state.target_item_type = str(picked["type"])
            state.target_lock_ticks = 4 if nightmare_profile and NIGHTMARE_STRICT else 0
            current_target = find_item_by_id(items, state.target_item_id)

    # In nightmare strict, do not keep switching targets every round.
    if nightmare_profile and NIGHTMARE_STRICT and state.target_lock_ticks > 0:
        state.target_lock_ticks -= 1

    active_goals: Set[Tuple[int, int]] = set()
    if missing_active:
        for item in items:
            itype = item.get("type")
            ipos = parse_xy(item.get("position"))
            if ipos is None or missing_active[itype] <= 0:
                continue
            active_goals.update(adjacent_walkable_cells(ipos, blocked, bounds))

    dist_drop = dist_from_pos.get(drop)
    dist_active = bfs_shortest_distance(pos, active_goals, blocked, bounds) if active_goals else None

    should_drop = False
    if has_deliverable:
        if bot_id not in allowed_drop_ids:
            should_drop = False
        elif is_courier:
            should_drop = True
        elif nightmare_profile and NIGHTMARE_STRICT and len(inv) >= 1:
            should_drop = True
        elif state.carry_rounds >= carry_force_rounds:
            should_drop = True
        elif oscillating:
            should_drop = True
        elif len(inv) >= 2:
            should_drop = True
        elif len(inv) >= 3:
            should_drop = True
        elif not missing_active:
            should_drop = True
        elif dist_drop is not None and dist_active is not None and dist_drop + 1 <= dist_active:
            should_drop = True
        elif dist_active is None:
            should_drop = True

    if current_target is not None and len(inv) < 3:
        tpos = parse_xy(current_target.get("position"))
        tid = str(current_target.get("id")) if current_target.get("id") is not None else None
        if tpos is not None and tid and manhattan(pos, tpos) == 1:
            t_type = current_target.get("type")
            if missing_active[t_type] > 0 or (not missing_active and has_deliverable and preview_missing[t_type] > 0):
                state.target_item_id = None
                state.target_item_type = None
                return {"bot": bot_id, "action": "pick_up", "item_id": tid}

    if current_target is not None and len(inv) < 3:
        tpos = parse_xy(current_target.get("position"))
        if tpos is not None:
            goals = adjacent_walkable_cells(tpos, blocked, bounds)
            direction = bfs_first_direction(pos, goals, blocked, bounds)
            if direction:
                state.last_direction = direction
                return {"bot": bot_id, "action": direction_to_action(direction)}

    # Nightmare strict: couriers deliver only, no extra pickup roaming.
    if nightmare_profile and NIGHTMARE_STRICT and is_courier:
        if has_deliverable:
            direction = bfs_first_direction(pos, {drop}, blocked, bounds)
            if direction:
                state.last_direction = direction
                return {"bot": bot_id, "action": direction_to_action(direction)}
        return {"bot": bot_id, "action": "wait"}

    if has_deliverable and oscillating:
        direction = bfs_first_direction(pos, {drop}, blocked, bounds)
        if direction:
            state.last_direction = direction
            return {"bot": bot_id, "action": direction_to_action(direction)}

    if len(inv) < 3 and missing_active and active_goals:
        direction = bfs_first_direction(pos, active_goals, blocked, bounds)
        if direction:
            state.last_direction = direction
            return {"bot": bot_id, "action": direction_to_action(direction)}

    if has_deliverable and should_drop:
        direction = bfs_first_direction(pos, {drop}, blocked, bounds)
        if direction:
            state.last_direction = direction
            return {"bot": bot_id, "action": direction_to_action(direction)}

    direction = choose_fallback_direction(pos, blocked, bounds, state)
    if direction:
        state.last_direction = direction
        return {"bot": bot_id, "action": direction_to_action(direction)}

    return {"bot": bot_id, "action": "wait"}


def apply_simulated_action(sim_data: Dict[str, Any], action: Dict[str, Any], bot_id_to_index: Dict[int, int]) -> None:
    bot_id = int(action.get("bot", -1))
    idx = bot_id_to_index.get(bot_id)
    if idx is None:
        return

    bots = sim_data.get("bots", [])
    if idx < 0 or idx >= len(bots):
        return
    bot = bots[idx]

    act = action.get("action")
    if isinstance(act, str) and act.startswith("move_"):
        direction = act.replace("move_", "", 1)
        if direction not in DIRECTIONS:
            return
        pos = parse_xy(bot.get("position"))
        bounds = extract_bounds(sim_data)
        if pos is None or bounds is None:
            return
        nx = pos[0] + DIRECTIONS[direction][0]
        ny = pos[1] + DIRECTIONS[direction][1]
        blocked = extract_walls_and_shelves(sim_data)
        occupied = {parse_xy(b.get("position")) for b in bots}
        occupied.discard(pos)
        if nx < 0 or ny < 0 or nx >= bounds[0] or ny >= bounds[1]:
            return
        if (nx, ny) in blocked or (nx, ny) in occupied:
            return
        bot["position"] = [nx, ny]
        return

    if act == "pick_up":
        item_id = action.get("item_id")
        if not item_id:
            return
        item_list = sim_data.get("items", [])
        item_idx = None
        item_type = None
        for i, item in enumerate(item_list):
            if str(item.get("id")) == str(item_id):
                item_idx = i
                item_type = item.get("type")
                break
        if item_idx is None or not item_type:
            return
        inv = bot.setdefault("inventory", [])
        if isinstance(inv, list) and len(inv) < 3:
            inv.append(item_type)
            item_list.pop(item_idx)


def throttle_nightmare_moves(data: Dict[str, Any], actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bounds = extract_bounds(data)
    bots = data.get("bots", [])
    if bounds is None or not bots:
        return actions

    width, height = bounds
    if len(bots) < NIGHTMARE_MIN_BOTS and (width * height) < 360:
        return actions

    active_idx = int(data.get("active_order_index", 0))
    orders = data.get("orders", [])
    active_order = next((o for o in orders if o.get("status") == "active"), None)
    if active_order is None and orders:
        active_order = orders[min(max(active_idx, 0), len(orders) - 1)]
    required = Counter(active_order.get("items_required", [])) if active_order else Counter()
    delivered = Counter(active_order.get("items_delivered", [])) if active_order else Counter()
    active_remaining = +(required - delivered)

    drop = parse_xy(data.get("drop_off")) or (1, 1)
    blocked = extract_walls_and_shelves(data)

    bot_by_id: Dict[int, Dict[str, Any]] = {}
    for i, b in enumerate(bots):
        bot_by_id[int(b.get("id", i))] = b

    scored: List[Tuple[int, int]] = []  # (priority, index)
    move_indices: List[int] = []
    for i, action in enumerate(actions):
        act = action.get("action", "")
        if not isinstance(act, str) or not act.startswith("move_"):
            continue
        move_indices.append(i)
        bid = int(action.get("bot", -1))
        b = bot_by_id.get(bid)
        if b is None:
            scored.append((1000, i))
            continue
        pos = parse_xy(b.get("position"))
        inv = list(b.get("inventory", []))
        ddrop = 999
        if pos is not None:
            dmap = bfs_distance_map(pos, blocked, bounds)
            ddrop = dmap.get(drop, 999)
        has_deliverable = any(active_remaining[it] > 0 for it in inv)
        if has_deliverable and ddrop <= 6:
            prio = 0
        elif has_deliverable:
            prio = 1
        elif len(inv) == 0:
            prio = 3
        else:
            prio = 2
        scored.append((prio * 100 + ddrop, i))

    if len(move_indices) <= NIGHTMARE_MAX_MOVERS:
        return actions

    scored.sort(key=lambda x: x[0])
    keep = {idx for _, idx in scored[:NIGHTMARE_MAX_MOVERS]}
    out: List[Dict[str, Any]] = []
    for i, action in enumerate(actions):
        act = action.get("action", "")
        if isinstance(act, str) and act.startswith("move_") and i not in keep:
            out.append({"bot": action.get("bot"), "action": "wait"})
        else:
            out.append(action)
    return out


async def run_bot_loop(uri: str, origin: Optional[str], headers: Optional[Dict[str, str]] = None) -> None:
    connect_variants = [
        {"origin": origin, "additional_headers": headers},
        {"origin": origin},
        {"additional_headers": headers},
        {},
    ]

    bot_states: Dict[int, BotState] = {}
    stats = GameStats()

    ws = None
    last_exc: Optional[Exception] = None
    for variant in connect_variants:
        kwargs: Dict[str, Any] = {}
        for key, value in variant.items():
            if value is None:
                continue
            if key == "additional_headers" and not value:
                continue
            kwargs[key] = value
        try:
            ws = await websockets.connect(uri, **kwargs)
            log.info("CONNECTED kwargs=%s", list(kwargs.keys()))
            break
        except Exception as exc:
            last_exc = exc

    if ws is None:
        raise RuntimeError(f"Unable to connect. last={last_exc!r}")

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except Exception:
                continue

            msg_type = data.get("type")
            if msg_type != "game_state":
                if msg_type == "game_over":
                    summary = {
                        "score": data.get("score"),
                        "rounds_used": data.get("rounds_used"),
                        "orders_completed": data.get("orders_completed"),
                        "items_delivered": data.get("items_delivered"),
                    }
                    log.info("GAME OVER: %s", summary)
                    idle_rate = (stats.wait_actions / stats.total_actions) if stats.total_actions else 0.0
                    avg_carry = (
                        sum(stats.carry_durations) / len(stats.carry_durations)
                        if stats.carry_durations
                        else 0.0
                    )
                    kpi = {
                        "idle_rate": round(idle_rate, 3),
                        "pickup_count": stats.pickup_actions,
                        "drop_count": stats.drop_actions,
                        "stuck_events": stats.stuck_events,
                        "avg_carry_rounds": round(avg_carry, 2),
                    }
                    log.info("KPI: %s", kpi)
                    return
                continue

            round_num = int(data.get("round", 0))
            if ROUND_LOG_EVERY > 0 and round_num % ROUND_LOG_EVERY == 0:
                log.info("Round %s score=%s", round_num, data.get("score"))

            sim_data = json.loads(json.dumps(data))
            sim_bots = sim_data.get("bots", [])
            bot_ids = sorted(int(b.get("id", i)) for i, b in enumerate(sim_bots))
            bot_id_to_index = {int(b.get("id", i)): i for i, b in enumerate(sim_bots)}

            actions = []
            reserved_item_ids: Set[str] = set()
            for bid in bot_ids:
                st = bot_states.setdefault(bid, BotState())
                idx = bot_id_to_index.get(bid)
                inv_before: List[str] = []
                if idx is not None:
                    bot_entry = sim_data.get("bots", [])[idx]
                    inv_before = list(bot_entry.get("inventory", []))

                action = choose_action(
                    sim_data,
                    st,
                    bot_id=bid,
                    reserved_item_ids=reserved_item_ids,
                    max_drop_travelers=MAX_DROP_TRAVELERS,
                    active_drop_weight=ACTIVE_DROP_WEIGHT,
                    preview_drop_weight=PREVIEW_DROP_WEIGHT,
                    stats=stats,
                )
                actions.append(action)
                if action.get("action") == "pick_up" and action.get("item_id"):
                    reserved_item_ids.add(str(action["item_id"]))
                elif st.target_item_id:
                    reserved_item_ids.add(str(st.target_item_id))
                apply_simulated_action(sim_data, action, bot_id_to_index)

            for bid, action in zip(bot_ids, actions):
                idx = bot_id_to_index.get(bid)
                inv_before: List[str] = []
                if idx is not None:
                    bot_entry = data.get("bots", [])[idx]
                    inv_before = list(bot_entry.get("inventory", []))
                stats.total_actions += 1
                act = action.get("action")
                if act == "wait":
                    stats.wait_actions += 1
                elif act == "pick_up":
                    stats.pickup_actions += 1
                    if len(inv_before) == 0:
                        stats.carry_start_round[bid] = round_num
                elif act == "drop_off":
                    stats.drop_actions += 1
                    start_round = stats.carry_start_round.pop(bid, None)
                    if start_round is not None and round_num >= start_round:
                        stats.carry_durations.append(round_num - start_round)

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
    run_until_403 = os.environ.get("GROCERY_RUN_UNTIL_403", "1").strip() not in ("0", "false", "False")

    async def run_once() -> None:
        for origin in ORIGINS:
            try:
                await run_bot_loop(uri, origin=origin, headers=None)
                return
            except Exception as exc:
                log.debug("Direct run failed query-token origin=%s: %r", origin, exc)

        if token:
            base_uri = uri.split("?", 1)[0]
            headers = {"Authorization": f"Bearer {token}"}
            for origin in ORIGINS:
                try:
                    await run_bot_loop(base_uri, origin=origin, headers=headers)
                    return
                except Exception as exc:
                    log.debug("Direct run failed bearer origin=%s: %r", origin, exc)

        raise RuntimeError("No connection variant worked. Check URL/token/origin")

    if not run_until_403:
        try:
            await run_once()
            return
        except Exception as exc:
            raise SystemExit(str(exc))

    # Keep running on same token until it expires (403 Forbidden).
    games = 0
    while True:
        try:
            await run_once()
            games += 1
            log.info("Completed game #%s on current token", games)
        except Exception as exc:
            msg = str(exc)
            if "403" in msg or "Forbidden" in msg:
                log.info("Stopping after %s game(s): token expired (403).", games)
                return
            raise SystemExit(f"Run stopped after {games} game(s): {exc!r}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
