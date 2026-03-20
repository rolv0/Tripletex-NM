from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from models import SolveRequest, SolveResponse
from solver import solve_task
from tripletex_client import TripletexClient

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("nm-ai-accounting")

app = FastAPI(title="NM AI Accounting Agent", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/solve", response_model=SolveResponse)
async def solve(
    payload: SolveRequest,
    authorization: str | None = Header(default=None),
) -> SolveResponse:
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
    try:
        result = await solve_task(payload)
        logger.info("solve_result %s", result)
    except Exception as exc:
        # Competition contract requires 200 + {"status":"completed"}.
        logger.exception("solve_error %s", exc)
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
