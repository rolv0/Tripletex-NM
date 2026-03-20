from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    solve_api_key: str = os.getenv("SOLVE_API_KEY", "").strip()
    request_timeout_seconds: float = float(os.getenv("TRIPLETEX_TIMEOUT_SECONDS", "20"))
    max_intelligent_retries: int = int(os.getenv("MAX_INTELLIGENT_RETRIES", "1"))


def get_settings() -> Settings:
    return Settings()

