from __future__ import annotations

import re
from typing import Any

from models import SolveRequest
from tripletex_client import TripletexClient


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0) if match else None


def _extract_name(text: str) -> str | None:
    patterns = [
        r"(?:navn|name|nombre|nome|nom)\s*[:=]?\s*['\"]?([^,.\n\"']+)",
        r"(?:med navn|with name|con nombre|com nome|mit name)\s+['\"]?([^,.\n\"']+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    return match.group(1).strip() if match else None


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(k in text for k in keywords)


def _is_create_intent(prompt_l: str) -> bool:
    create_words = {
        "opprett",
        "lag",
        "laga",
        "create",
        "crear",
        "criar",
        "erstelle",
        "creer",
    }
    return _contains_any(prompt_l, create_words)


async def solve_task(req: SolveRequest) -> dict[str, Any]:
    prompt_l = req.prompt.lower()
    client = TripletexClient(
        base_url=req.tripletex_credentials.base_url,
        session_token=req.tripletex_credentials.session_token,
    )

    name = _extract_name(req.prompt) or "Auto Generated"
    email = _extract_email(req.prompt)

    if _is_create_intent(prompt_l) and _contains_any(prompt_l, {"ansatt", "employee", "empleado", "funcionario"}):
        first_name, _, last_name = name.partition(" ")
        payload: dict[str, Any] = {
            "firstName": first_name or "Auto",
            "lastName": last_name or "User",
        }
        if email:
            payload["email"] = email
        await client.post("/employee", payload)
        return {"action": "create_employee", "payload": payload}

    if _is_create_intent(prompt_l) and _contains_any(prompt_l, {"kunde", "customer", "cliente", "client"}):
        payload = {"name": name, "isCustomer": True}
        if email:
            payload["email"] = email
        await client.post("/customer", payload)
        return {"action": "create_customer", "payload": payload}

    if _is_create_intent(prompt_l) and _contains_any(prompt_l, {"produkt", "product", "producto"}):
        payload = {"name": name, "isInactive": False}
        await client.post("/product", payload)
        return {"action": "create_product", "payload": payload}

    if _is_create_intent(prompt_l) and _contains_any(prompt_l, {"avdeling", "department", "departamento"}):
        payload = {"name": name}
        await client.post("/department", payload)
        return {"action": "create_department", "payload": payload}

    if _is_create_intent(prompt_l) and _contains_any(prompt_l, {"prosjekt", "project", "proyecto"}):
        payload = {"name": name}
        await client.post("/project", payload)
        return {"action": "create_project", "payload": payload}

    return {"action": "no_op", "reason": "unclassified_prompt"}
