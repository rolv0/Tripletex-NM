from __future__ import annotations

import re


def parse_float(raw: str) -> float | None:
    candidate = raw.replace(" ", "")
    if candidate.count(",") == 1 and candidate.count(".") == 0:
        candidate = candidate.replace(",", ".")
    else:
        candidate = candidate.replace(",", "")
    try:
        return float(candidate)
    except ValueError:
        return None


def extract_all_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for match in re.finditer(r"(\d[\d\s.,]{0,20})\s*(?:nok|kr|eur|usd|nkr)?", text, re.IGNORECASE):
        val = parse_float(match.group(1))
        if val is not None and val > 0:
            amounts.append(val)
    return amounts


def extract_amount(text: str) -> float | None:
    values = extract_all_amounts(text)
    return values[0] if values else None

