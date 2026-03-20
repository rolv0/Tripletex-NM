from __future__ import annotations

from utils.text import normalize_text


def normalize_prompt(prompt: str) -> str:
    return normalize_text(prompt.strip())

