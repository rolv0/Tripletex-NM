from __future__ import annotations

from datetime import date, timedelta


def today_iso() -> str:
    return date.today().isoformat()


def date_range_days(days_back: int, days_forward: int = 1) -> tuple[str, str]:
    now = date.today()
    return (now - timedelta(days=days_back)).isoformat(), (now + timedelta(days=days_forward)).isoformat()

