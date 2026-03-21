from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tripletex.field_whitelist import ALLOWED_FIELDS, ALLOWED_QUERY_PARAMS


def canonical_endpoint(path: str) -> str:
    clean = path if path.startswith("/") else f"/{path}"
    clean = re.sub(r"/\d+", "/{id}", clean)
    if "/:" in clean:
        clean = clean.split("/:")[0]
    if clean.startswith("/invoice/paymentType"):
        return "/invoice/paymentType"
    if clean.startswith("/salary/type"):
        return "/salary/type"
    if clean.startswith("/travelExpense"):
        return "/travelExpense"
    if clean.startswith("/customer"):
        return "/customer"
    if clean.startswith("/employee"):
        return "/employee"
    if clean.startswith("/project"):
        return "/project"
    if clean.startswith("/activity"):
        return "/activity"
    if clean.startswith("/timesheet/entry"):
        return "/timesheet/entry"
    if clean.startswith("/product"):
        return "/product"
    if clean.startswith("/invoice"):
        return "/invoice"
    if clean.startswith("/order"):
        return "/order"
    return clean


@dataclass
class ValidationResult:
    params: dict[str, Any]
    payload: dict[str, Any] | None


def _sanitize_fields_param(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    if "fields" not in params:
        return params
    allowed_fields = ALLOWED_FIELDS.get(endpoint)
    if not allowed_fields:
        return params
    raw = str(params["fields"])
    requested = [item.strip() for item in raw.split(",") if item.strip()]
    filtered: list[str] = []
    for field in requested:
        if field == "*":
            filtered.append(field)
            continue
        base = field.split("(")[0].strip()
        if base in allowed_fields:
            filtered.append(field)
    copy = dict(params)
    if filtered:
        copy["fields"] = ",".join(filtered)
    else:
        copy.pop("fields", None)
    return copy


def validate_request(
    *,
    method: str,
    path: str,
    params: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    allowed_endpoints: set[str] | None,
) -> ValidationResult:
    endpoint = canonical_endpoint(path)
    if allowed_endpoints is not None and endpoint not in allowed_endpoints:
        raise ValueError(f"Endpoint {endpoint} is not allowed for selected workflow")

    cleaned_params = dict(params or {})
    if method.upper() == "GET":
        allowed_params = ALLOWED_QUERY_PARAMS.get(endpoint)
        if allowed_params is not None:
            cleaned_params = {k: v for k, v in cleaned_params.items() if k in allowed_params}
        cleaned_params = _sanitize_fields_param(endpoint, cleaned_params)

    # Keep payload untouched unless empty.
    cleaned_payload = payload if payload else payload
    return ValidationResult(params=cleaned_params, payload=cleaned_payload)
