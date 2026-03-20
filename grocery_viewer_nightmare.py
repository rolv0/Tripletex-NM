import asyncio
import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import websockets
from websockets.exceptions import ConnectionClosed

try:
    import pygame
except Exception:
    raise SystemExit("Pygame mangler. Installer med: .\\.venv\\Scripts\\python.exe -m pip install pygame")

import nightmare_bot_pipeline as bot

CELL = 28
MARGIN = 2
FPS = 30

COLORS = {
    "bg": (18, 22, 28),
    "grid": (40, 46, 56),
    "wall": (72, 82, 98),
    "shelf": (150, 120, 60),
    "drop": (80, 180, 120),
    "text": (230, 235, 245),
}

BOT_COLORS = [
    (255, 99, 132),
    (54, 162, 235),
    (255, 206, 86),
    (75, 192, 192),
    (153, 102, 255),
    (255, 159, 64),
    (46, 204, 113),
    (231, 76, 60),
    (52, 152, 219),
    (241, 196, 15),
]

latest_state: Dict[str, Any] = {}
last_game_over: Dict[str, Any] = {}
status_line = "Venter pa data..."
lock = threading.Lock()


def safe_parse_bounds(state: Dict[str, Any]) -> Tuple[int, int]:
    b = bot.extract_bounds(state)
    return b if b else (16, 12)


def draw_state(screen: pygame.Surface, font: pygame.font.Font) -> None:
    with lock:
        state = dict(latest_state)
        game_over = dict(last_game_over)
        status = status_line

    w, h = safe_parse_bounds(state)
    screen.fill(COLORS["bg"])

    # Grid background
    for y in range(h):
        for x in range(w):
            rx = x * CELL + MARGIN
            ry = y * CELL + MARGIN + 64
            pygame.draw.rect(screen, COLORS["grid"], (rx, ry, CELL - 2 * MARGIN, CELL - 2 * MARGIN), border_radius=4)

    # Walls
    grid = state.get("grid", {}) if isinstance(state, dict) else {}
    if isinstance(grid, dict):
        for wall in grid.get("walls", []):
            p = bot.parse_xy(wall)
            if p is None:
                continue
            x, y = p
            pygame.draw.rect(
                screen,
                COLORS["wall"],
                (x * CELL + MARGIN, y * CELL + MARGIN + 64, CELL - 2 * MARGIN, CELL - 2 * MARGIN),
                border_radius=4,
            )

    # Shelves/items
    for item in state.get("items", []):
        p = bot.parse_xy(item.get("position"))
        if p is None:
            continue
        x, y = p
        pygame.draw.rect(
            screen,
            COLORS["shelf"],
            (x * CELL + MARGIN + 4, y * CELL + MARGIN + 68, CELL - 2 * MARGIN - 8, CELL - 2 * MARGIN - 8),
            border_radius=3,
        )

    # Drop-off
    drop = bot.parse_xy(state.get("drop_off"))
    if drop:
        x, y = drop
        pygame.draw.rect(
            screen,
            COLORS["drop"],
            (x * CELL + MARGIN + 2, y * CELL + MARGIN + 66, CELL - 2 * MARGIN - 4, CELL - 2 * MARGIN - 4),
            border_radius=4,
        )

    # Bots
    for i, b in enumerate(state.get("bots", [])):
        p = bot.parse_xy(b.get("position"))
        if p is None:
            continue
        x, y = p
        color = BOT_COLORS[i % len(BOT_COLORS)]
        cx = x * CELL + CELL // 2
        cy = y * CELL + 64 + CELL // 2
        pygame.draw.circle(screen, color, (cx, cy), CELL // 3)

    score = state.get("score")
    rnd = state.get("round")
    top = f"Round: {rnd}   Score: {score}   {status}"
    if game_over:
        top = f"GAME OVER  score={game_over.get('score')} orders={game_over.get('orders_completed')} items={game_over.get('items_delivered')}"

    txt = font.render(top, True, COLORS["text"])
    screen.blit(txt, (12, 18))


def run_visual() -> None:
    pygame.init()
    font = pygame.font.SysFont("consolas", 20)

    w, h = 16, 12
    screen = pygame.display.set_mode((w * CELL + 2, h * CELL + 70))
    pygame.display.set_caption("Grocery Bot Live Viewer")

    clock = pygame.time.Clock()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        with lock:
            state = dict(latest_state)
        bw, bh = safe_parse_bounds(state)
        desired_size = (bw * CELL + 2, bh * CELL + 70)
        if screen.get_size() != desired_size:
            screen = pygame.display.set_mode(desired_size)

        draw_state(screen, font)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


async def run_ws(uri: str) -> None:
    token = bot.extract_token_from_uri(uri)
    state_map: Dict[int, bot.BotState] = {}

    async def connect_and_play(target_uri: str, headers: Optional[Dict[str, str]]) -> None:
        for origin in bot.ORIGINS:
            kwargs: Dict[str, Any] = {"origin": origin}
            if headers:
                kwargs["additional_headers"] = headers
            try:
                async with websockets.connect(target_uri, **kwargs) as ws:
                    with lock:
                        global status_line
                        status_line = f"Connected ({origin})"
                    print(f"[WS] connected origin={origin} uri={target_uri.split('?')[0]}")

                    async for raw in ws:
                        t0 = time.perf_counter()
                        data = json.loads(raw)
                        msg_type = data.get("type")
                        if msg_type == "game_over":
                            with lock:
                                global last_game_over
                                last_game_over = data
                                status_line = "Game over"
                            return
                        if msg_type != "game_state":
                            continue

                        with lock:
                            global latest_state
                            latest_state = data

                        # Keep persistent per-bot state across ticks.
                        actions = bot.decide_actions(data, state_map)
                        await ws.send(json.dumps({"actions": actions}))
                        dt_ms = (time.perf_counter() - t0) * 1000.0
                        rnd = data.get("round")
                        if isinstance(rnd, int) and rnd % 10 == 0:
                            print(f"[TICK] round={rnd} dt_ms={dt_ms:.1f}")
                        if dt_ms > 1500:
                            print(f"[WARN] slow tick round={rnd} dt_ms={dt_ms:.1f}")
                return
            except ConnectionClosed as exc:
                with lock:
                    status_line = f"Closed: code={exc.code} reason={exc.reason}"
                print(f"[WS] closed origin={origin} code={exc.code} reason={exc.reason!r}")
            except Exception as exc:
                with lock:
                    status_line = f"Retry: {type(exc).__name__}: {exc}"
                print(f"[WS] connect failed origin={origin} error={type(exc).__name__}: {exc}")
                continue

        raise RuntimeError("Could not connect")

    try:
        await connect_and_play(uri, headers=None)
        return
    except Exception:
        pass

    if token:
        try:
            base = uri.split("?", 1)[0]
            await connect_and_play(base, headers={"Authorization": f"Bearer {token}"})
            return
        except Exception:
            pass

    with lock:
        global status_line
        status_line = "Could not connect (token expired/invalid)"


def run_ws_safe(uri: str) -> None:
    try:
        asyncio.run(run_ws(uri))
    except Exception:
        with lock:
            global status_line
            status_line = "WebSocket error"


def main() -> None:
    uri = os.environ.get("GROCERY_WS", "").strip()
    if not uri:
        uri = input("Lim inn wss-token URL og trykk Enter: ").strip()
    if not uri:
        raise SystemExit("Ingen token URL oppgitt.")

    t = threading.Thread(target=lambda: run_ws_safe(uri), daemon=True)
    t.start()
    run_visual()


if __name__ == "__main__":
    main()
