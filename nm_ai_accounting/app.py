from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from models import SolveRequest, SolveResponse
from solver import solve_task
from tripletex_client import TripletexClient

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("nm-ai-accounting")

app = FastAPI(title="NM AI Accounting Agent", version="0.1.0")
solve_events: deque[dict[str, Any]] = deque(maxlen=300)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/solve", response_model=SolveResponse)
async def solve(
    payload: SolveRequest,
    authorization: str | None = Header(default=None),
) -> SolveResponse:
    started = time.perf_counter()
    expected_api_key = os.getenv("SOLVE_API_KEY")
    if expected_api_key:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        provided = authorization.removeprefix("Bearer ").strip()
        if provided != expected_api_key:
            raise HTTPException(status_code=403, detail="Invalid bearer token")

    logger.info(
        "solve_request prompt_len=%d files=%d prompt_preview=%r",
        len(payload.prompt),
        len(payload.files),
        payload.prompt[:180],
    )
    result: dict[str, Any] = {"action": "unknown"}
    error_text: str | None = None
    try:
        result = await solve_task(payload)
        logger.info("solve_result %s", result)
    except Exception as exc:
        # Competition contract requires 200 + {"status":"completed"}.
        error_text = str(exc)
        logger.exception("solve_error %s", exc)
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        solve_events.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "prompt_len": len(payload.prompt),
                "files": len(payload.files),
                "action": result.get("action", "unknown"),
                "status": "error" if error_text else "ok",
                "duration_ms": duration_ms,
                "error": error_text,
            }
        )
    return SolveResponse(status="completed")


@app.get("/tripletex/ping")
async def tripletex_ping() -> dict[str, Any]:
    """
    Debug endpoint for local verification of API credentials.
    """
    client = TripletexClient()
    try:
        data = await client.get("/token/session/>whoAmI")
        return {"ok": True, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/metrics")
async def metrics() -> JSONResponse:
    items = list(solve_events)
    by_action: dict[str, int] = {}
    errors = 0
    total_duration = 0
    for item in items:
        by_action[item["action"]] = by_action.get(item["action"], 0) + 1
        if item["status"] == "error":
            errors += 1
        total_duration += int(item["duration_ms"])
    avg_duration = int(total_duration / len(items)) if items else 0
    return JSONResponse(
        {
            "count": len(items),
            "errors": errors,
            "error_rate": round(errors / len(items), 3) if items else 0,
            "avg_duration_ms": avg_duration,
            "actions": by_action,
            "events": items,
        }
    )


@app.get("/dashboard")
async def dashboard() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>NM AI Accounting Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: Arial, sans-serif; background: #0b1220; color: #e5e7eb; margin: 24px; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
    .card { background: #111827; border: 1px solid #374151; border-radius: 10px; padding: 12px; }
    .kpi { font-size: 24px; font-weight: 700; }
    h1 { margin-top: 0; }
    canvas { background: #111827; border: 1px solid #374151; border-radius: 10px; padding: 8px; margin-top: 12px; }
    table { width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 12px; }
    th, td { border-bottom: 1px solid #374151; padding: 8px; text-align: left; }
    .err { color: #fca5a5; }
    .ok { color: #86efac; }
  </style>
</head>
<body>
  <h1>NM AI Accounting - Live Dashboard</h1>
  <div class="grid">
    <div class="card"><div>Total requests</div><div id="k_count" class="kpi">0</div></div>
    <div class="card"><div>Error rate</div><div id="k_err_rate" class="kpi">0%</div></div>
    <div class="card"><div>Avg duration</div><div id="k_avg" class="kpi">0 ms</div></div>
    <div class="card"><div>Last action</div><div id="k_last" class="kpi">-</div></div>
  </div>
  <canvas id="actionsChart" height="120"></canvas>
  <canvas id="latencyChart" height="120"></canvas>
  <table>
    <thead><tr><th>Time (UTC)</th><th>Action</th><th>Status</th><th>Duration</th><th>Error</th></tr></thead>
    <tbody id="events"></tbody>
  </table>
  <script>
    let actionsChart, latencyChart;
    function render(data) {
      document.getElementById("k_count").textContent = data.count;
      document.getElementById("k_err_rate").textContent = Math.round(data.error_rate * 100) + "%";
      document.getElementById("k_avg").textContent = data.avg_duration_ms + " ms";
      const last = data.events.length ? data.events[data.events.length - 1].action : "-";
      document.getElementById("k_last").textContent = last;

      const labels = Object.keys(data.actions);
      const values = Object.values(data.actions);
      const ctxA = document.getElementById("actionsChart");
      if (actionsChart) actionsChart.destroy();
      actionsChart = new Chart(ctxA, {
        type: "bar",
        data: { labels, datasets: [{ label: "Actions", data: values }] },
        options: { plugins: { legend: { labels: { color: "#e5e7eb" } } }, scales: { x: { ticks: { color: "#e5e7eb" } }, y: { ticks: { color: "#e5e7eb" } } } }
      });

      const ev = data.events.slice(-50);
      const ctxL = document.getElementById("latencyChart");
      if (latencyChart) latencyChart.destroy();
      latencyChart = new Chart(ctxL, {
        type: "line",
        data: {
          labels: ev.map((_, i) => i + 1),
          datasets: [{ label: "Duration ms", data: ev.map(x => x.duration_ms), tension: 0.2 }]
        },
        options: { plugins: { legend: { labels: { color: "#e5e7eb" } } }, scales: { x: { ticks: { color: "#e5e7eb" } }, y: { ticks: { color: "#e5e7eb" } } } }
      });

      const tbody = document.getElementById("events");
      tbody.innerHTML = "";
      data.events.slice().reverse().slice(0, 30).forEach(e => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${e.ts}</td><td>${e.action}</td><td class="${e.status === "error" ? "err" : "ok"}">${e.status}</td><td>${e.duration_ms} ms</td><td>${e.error || ""}</td>`;
        tbody.appendChild(tr);
      });
    }
    async function load() {
      const res = await fetch("/metrics");
      const data = await res.json();
      render(data);
    }
    load();
    setInterval(load, 8000);
  </script>
</body>
</html>
"""
    )
