from __future__ import annotations

import json
import logging
from typing import Any


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")


def json_log(logger: logging.Logger, event: str, **kwargs: Any) -> None:
    payload = {"event": event, **kwargs}
    logger.info(json.dumps(payload, ensure_ascii=False))

