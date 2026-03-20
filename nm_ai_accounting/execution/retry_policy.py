from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 1

    def should_retry(self, attempt: int, error_text: str) -> bool:
        if attempt >= self.max_retries:
            return False
        normalized = error_text.lower()
        return any(token in normalized for token in ("validation", "required", "kan ikke være null", "missing"))

