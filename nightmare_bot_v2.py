import asyncio
import json
import logging
import os
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import websockets

logging.basicConfig(
    level=os.environ.get("GROCERY_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("nightmare-bot-v2")
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

MAX_MOVERS = int(os.environ.get("NM2_MAX_MOVERS", "10"))
COURIER_FRAC = float(os.environ.get("NM2_COURIER_FRAC", "0.2"))
STUCK_WINDOW = int(os.environ.get("NM2_STUCK_WINDOW", "10"))


def extract_token_from_uri(uri: str) -> Optional[str]:
    if "token=" not in uri:
        return None
    return uri.split("token=", 1)[1].split("&", 1)[0]


def norm_item(v: Any) -> str:
    return str(v)


@dataclass
class BotState:
    last_direction: Optional[str] = None
    recent_positions: deque = field(default_factory=lambda: deque(maxlen=12))


@dataclass
class PlannerState:
    tick: int = 0


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
    return {
        "up": "move_up",
        "right": "move_right",
        "down": "move_down",
        "left": "move_left",
    }[direction]


def reverse_of(direction: str) -> str:
    return {"up": "down", "down": "up", "left": "right", "right": "left"}[direction]


def is_stuck(bs: BotState) -> bool:
    vals = list(bs.recent_positions)
    if len(vals) < STUCK_WINDOW:
        return False
    recent = vals[-STUCK_WINDOW:]
    return len(set(recent)) <= 3


def get_active_order(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    orders = data.get("orders", [])
    if not orders:
        return None
    active = next((o for o in orders if o.get("status") == "active"), None)
    if active is not None:
        return active
    idx = min(max(int(data.get("active_order_index", 0)), 0), len(orders) - 1)
    return orders[idx]


def choose_goals_for_bot(
    bid: int,
    pos: Tuple[int, int],
    inv: List[str],
    items: List[Dict[str, Any]],
    remaining: Counter,
    missing: Counter,
    drop: Tuple[int, int],
    bounds: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
    couriers: Set[int],
    claimed_items: Set[str],
) -> Tuple[str, Set[Tuple[int, int]], Optional[str]]:
    has_del = any(remaining.get(norm_item(it), 0) > 0 for it in inv)
    if has_del:
        return "deliver", {drop}, None

    # Courier without load: only stage when active demand is almost covered.
    if bid in couriers and sum(missing.values()) <= 1:
        st = set(neighbors(drop, bounds, blocked))
        return "staging", (st if st else {drop}), None

    # Picker: closest useful active item
    best: Optional[Tuple[int, str, Set[Tuple[int, int]]]] = None
    for item in items:
        iid = item.get("id")
        itype_raw = item.get("type")
        ipos = parse_xy(item.get("position"))
        if iid is None or itype_raw is None or ipos is None:
            continue
        itype = norm_item(itype_raw)
        iid_s = str(iid)
        if iid_s in claimed_items:
            continue
        if missing.get(itype, 0) <= 0:
            continue
        goals = set(neighbors(ipos, bounds, blocked))
        if not goals:
            continue
        d = bfs_distance(pos, goals, bounds, blocked)
        if d >= 10**9:
            continue
        bonus = 15 if missing.get(itype, 0) == 1 else 0
        score = d * 10 - bonus
        if best is None or score < best[0]:
            best = (score, iid_s, goals)

    if best is not None:
        _, iid_s, goals = best
        claimed_items.add(iid_s)
        return "pickup", goals, iid_s

    # Patrol fallback
    best_patrol: Optional[Tuple[int, Set[Tuple[int, int]]]] = None
    for item in items:
        ipos = parse_xy(item.get("position"))
        if ipos is None:
            continue
        goals = set(neighbors(ipos, bounds, blocked))
        if not goals:
            continue
        d = bfs_distance(pos, goals, bounds, blocked)
        if d < 10**9 and (best_patrol is None or d < best_patrol[0]):
            best_patrol = (d, goals)

    if best_patrol is not None:
        return "patrol", best_patrol[1], None

    return "wait", set(), None


def decide_actions(data: Dict[str, Any], bot_states: Dict[int, BotState], planner_state: PlannerState) -> List[Dict[str, Any]]:
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
            bs = bot_states.setdefault(bid, BotState())
            bs.recent_positions.append(p)
        bot_inv[bid] = list(b.get("inventory", []))

    blocked = extract_blocked(data)
    active = get_active_order(data)
    if active is None:
        return [{"bot": bid, "action": "wait"} for bid in bot_ids]

    required = Counter(norm_item(x) for x in active.get("items_required", []))
    delivered = Counter(norm_item(x) for x in active.get("items_delivered", []))
    remaining = +(required - delivered)

    carrying = Counter()
    for bid in bot_ids:
        for it_raw in bot_inv.get(bid, []):
            it = norm_item(it_raw)
            if remaining.get(it, 0) > carrying[it]:
                carrying[it] += 1
    missing = +(remaining - carrying)

    drop = parse_xy(data.get("drop_off")) or (1, 1)
    items = data.get("items", [])

    courier_count = max(2, int(len(bot_ids) * COURIER_FRAC))
    couriers = set(sorted(bot_ids)[-courier_count:])

    # Build goals/tasks
    goals_by_bot: Dict[int, Set[Tuple[int, int]]] = {}
    kind_by_bot: Dict[int, str] = {}
    pick_item_by_bot: Dict[int, Optional[str]] = {}
    claimed_items: Set[str] = set()

    for bid in bot_ids:
        pos = bot_pos.get(bid)
        if pos is None:
            kind_by_bot[bid] = "wait"
            goals_by_bot[bid] = set()
            pick_item_by_bot[bid] = None
            continue
        kind, goals, iid = choose_goals_for_bot(
            bid, pos, bot_inv.get(bid, []), items, remaining, missing, drop, bounds, blocked, couriers, claimed_items
        )
        kind_by_bot[bid] = kind
        goals_by_bot[bid] = goals
        pick_item_by_bot[bid] = iid

    # Immediate actions
    item_lookup = {str(it.get("id")): it for it in items if it.get("id") is not None}
    actions: Dict[int, Dict[str, Any]] = {bid: {"bot": bid, "action": "wait"} for bid in bot_ids}
    reserved_pick: Set[str] = set()

    for bid in bot_ids:
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        if kind_by_bot.get(bid) == "deliver" and pos == drop:
            actions[bid] = {"bot": bid, "action": "drop_off"}
            continue
        if kind_by_bot.get(bid) == "pickup":
            iid = pick_item_by_bot.get(bid)
            if iid:
                item = item_lookup.get(iid)
                ip = parse_xy(item.get("position")) if item else None
                if ip is not None and abs(ip[0] - pos[0]) + abs(ip[1] - pos[1]) == 1 and iid not in reserved_pick:
                    reserved_pick.add(iid)
                    actions[bid] = {"bot": bid, "action": "pick_up", "item_id": iid}

    # Choose mover set (central scheduler)
    movable: List[Tuple[int, int]] = []
    for bid in bot_ids:
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        goals = goals_by_bot.get(bid, set())
        d = bfs_distance(pos, goals, bounds, blocked) if goals else 10**9
        pr = 0 if kind_by_bot.get(bid) == "deliver" else (1 if kind_by_bot.get(bid) == "pickup" else 2)
        movable.append((pr * 1000 + d, bid))

    # Rotate movers to avoid starvation/lock.
    movable.sort(key=lambda x: (x[0], x[1]))
    planner_state.tick += 1
    if movable:
        shift = planner_state.tick % len(movable)
        movable = movable[shift:] + movable[:shift]

    movers = {bid for _, bid in movable[:MAX_MOVERS]}

    # One-step reservations for movers
    reserved_next: Set[Tuple[int, int]] = set()
    stationary_cells = set(bot_pos.values())

    for bid in bot_ids:
        if actions[bid]["action"] != "wait" or bid not in movers:
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        goals = goals_by_bot.get(bid, set())

        local_blocked = set(blocked)
        local_blocked.update(reserved_next)
        for s in stationary_cells:
            if s != pos:
                local_blocked.add(s)

        d = bfs_first_direction(pos, goals, bounds, local_blocked) if goals else None
        bs = bot_states.setdefault(bid, BotState())
        if d and bs.last_direction and d == reverse_of(bs.last_direction) and is_stuck(bs):
            d = None

        if d:
            nx, ny = pos[0] + DIRECTIONS[d][0], pos[1] + DIRECTIONS[d][1]
            np = (nx, ny)
            if np not in reserved_next:
                reserved_next.add(np)
                bs.last_direction = d
                actions[bid] = {"bot": bid, "action": direction_to_action(d)}

    # Anti-corner final fallback for waiting movers
    for bid in bot_ids:
        if bid not in movers:
            continue
        if actions[bid]["action"] != "wait":
            continue
        pos = bot_pos.get(bid)
        if pos is None:
            continue
        bs = bot_states.setdefault(bid, BotState())
        best_dir: Optional[str] = None
        best_score = 10**9
        for name, (dx, dy) in DIRECTIONS.items():
            np = (pos[0] + dx, pos[1] + dy)
            if np in blocked or np in reserved_next:
                continue
            if np in stationary_cells and np != pos:
                continue
            score = sum(1 for p in bs.recent_positions if p == np)
            if bs.last_direction and name == reverse_of(bs.last_direction):
                score += 2
            if score < best_score:
                best_score = score
                best_dir = name
        if best_dir:
            nx, ny = pos[0] + DIRECTIONS[best_dir][0], pos[1] + DIRECTIONS[best_dir][1]
            np = (nx, ny)
            if np not in reserved_next:
                reserved_next.add(np)
                bs.last_direction = best_dir
                actions[bid] = {"bot": bid, "action": direction_to_action(best_dir)}

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
            log.info("CONNECTED kwargs=%s", list(kwargs.keys()))
            break
        except Exception as exc:
            last_exc = exc

    if ws is None:
        raise RuntimeError(f"Unable to connect: {last_exc!r}")

    bot_states: Dict[int, BotState] = {}
    planner_state = PlannerState()

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

            actions = decide_actions(data, bot_states, planner_state)
            await ws.send(json.dumps({"actions": actions}))
    finally:
        try:
            await ws.close()
        except Exception:
            pass


async def main() -> None:
    uri = os.environ.get("GROCERY_WS", "").strip()
    if not uri:
        raise SystemExit("Set GROCERY_WS in environment")
    token = extract_token_from_uri(uri)

    for origin in ORIGINS:
        try:
            await run_bot_loop(uri, origin=origin, headers=None)
            return
        except Exception as exc:
            log.debug("query connect failed origin=%s: %r", origin, exc)

    if token:
        base = uri.split("?", 1)[0]
        headers = {"Authorization": f"Bearer {token}"}
        for origin in ORIGINS:
            try:
                await run_bot_loop(base, origin=origin, headers=headers)
                return
            except Exception as exc:
                log.debug("bearer connect failed origin=%s: %r", origin, exc)

    raise SystemExit("No connection variant worked")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
