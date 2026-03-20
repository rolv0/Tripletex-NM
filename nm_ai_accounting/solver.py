from __future__ import annotations

import re
from typing import Any

from models import SolveRequest
from tripletex_client import TripletexClient


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0) if match else None


def _extract_name(text: str) -> str | None:
    match = re.search(
        r"(?:navn|med navn|kunde(?:n)?|produkt(?:et)?|avdeling(?:en)?|prosjekt(?:et)?)\s+([A-ZÆØÅ][^,.\n]+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().strip("\"'")
    return None


async def solve_task(req: SolveRequest) -> None:
    prompt_l = req.prompt.lower()
    client = TripletexClient(
        base_url=req.tripletex_credentials.base_url,
        session_token=req.tripletex_credentials.session_token,
    )

    name = _extract_name(req.prompt) or "Auto Generated"
    email = _extract_email(req.prompt)

    if "opprett" in prompt_l and ("ansatt" in prompt_l or "employee" in prompt_l):
        first_name, _, last_name = name.partition(" ")
        payload: dict[str, Any] = {
            "firstName": first_name or "Auto",
            "lastName": last_name or "User",
        }
        if email:
            payload["email"] = email
        await client.post("/employee", payload)
        return

    if "opprett" in prompt_l and ("kunde" in prompt_l or "customer" in prompt_l):
        payload = {"name": name, "isCustomer": True}
        if email:
            payload["email"] = email
        await client.post("/customer", payload)
        return

    if "opprett" in prompt_l and ("produkt" in prompt_l or "product" in prompt_l):
        await client.post("/product", {"name": name, "isInactive": False})
        return

    if "opprett" in prompt_l and ("avdeling" in prompt_l or "department" in prompt_l):
        await client.post("/department", {"name": name})
        return

    if "opprett" in prompt_l and ("prosjekt" in prompt_l or "project" in prompt_l):
        await client.post("/project", {"name": name})
        return

    # If we cannot confidently classify the prompt yet, do no writes.
    return
