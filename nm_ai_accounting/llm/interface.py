from __future__ import annotations

from typing import Protocol


class StructuredParser(Protocol):
    def parse(self, prompt: str) -> dict[str, object]:
        ...

