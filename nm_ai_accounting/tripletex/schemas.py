from __future__ import annotations

from typing import Any


def require_fields(payload: dict[str, Any], required: set[str], context: str) -> None:
    missing = [key for key in required if payload.get(key) in (None, "", [], {})]
    if missing:
        raise ValueError(f"{context}: missing required fields: {', '.join(sorted(missing))}")

