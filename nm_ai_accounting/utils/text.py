from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    translit = text.translate(
        str.maketrans(
            {
                "\u00f8": "o",
                "\u00d8": "o",
                "\u00e5": "a",
                "\u00c5": "a",
                "\u00e6": "ae",
                "\u00c6": "ae",
            }
        )
    )
    normalized = unicodedata.normalize("NFKD", translit)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_marks.lower()


def as_word_string(normalized_text: str) -> str:
    return f" {re.sub(r'[^a-z0-9]+', ' ', normalized_text).strip()} "


def contains_any(normalized_text: str, keywords: set[str]) -> bool:
    haystack = as_word_string(normalized_text)
    for keyword in keywords:
        needle = re.sub(r"[^a-z0-9]+", " ", normalize_text(keyword)).strip()
        if needle and f" {needle} " in haystack:
            return True
    return False

