from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from parsing.entity_extractor import (
    extract_all_amounts,
    extract_customer_name,
    extract_email,
    extract_org_number,
    extract_person_name,
    extract_project_name,
    extract_quoted_items,
)
from tripletex import TripletexClient


def pick_first_value_id(response: dict[str, Any]) -> int | None:
    value = response.get("value")
    if isinstance(value, dict) and value.get("id") is not None:
        try:
            return int(value["id"])
        except Exception:
            return None
    values = response.get("values")
    if isinstance(values, list) and values:
        candidate = values[0]
        if isinstance(candidate, dict) and candidate.get("id") is not None:
            try:
                return int(candidate["id"])
            except Exception:
                return None
    return None


async def find_customer(client: TripletexClient, name: str, org_no: str | None = None) -> dict[str, Any] | None:
    params: dict[str, Any] = {"name": name, "count": 10, "fields": "id,name,organizationNumber"}
    if org_no:
        params["organizationNumber"] = org_no
    response = await client.get("/customer", params=params)
    values = response.get("values", [])
    return values[0] if values else None


async def find_supplier(client: TripletexClient, name: str, org_no: str | None = None) -> dict[str, Any] | None:
    params: dict[str, Any] = {"name": name, "count": 10, "fields": "id,name,organizationNumber"}
    if org_no:
        params["organizationNumber"] = org_no
    response = await client.get("/supplier", params=params)
    values = response.get("values", [])
    return values[0] if values else None


async def ensure_customer(client: TripletexClient, prompt: str) -> int | None:
    customer_name = extract_customer_name(prompt) or "Customer"
    org_no = extract_org_number(prompt)
    found = await find_customer(client, customer_name, org_no)
    if found:
        return int(found["id"])
    payload: dict[str, Any] = {"name": customer_name, "isCustomer": True}
    email = extract_email(prompt)
    if email:
        payload["email"] = email
    if org_no:
        payload["organizationNumber"] = org_no
    created = await client.post("/customer", payload)
    return pick_first_value_id(created)


async def find_employee_id(client: TripletexClient, prompt: str) -> int | None:
    email = extract_email(prompt)
    if email:
        response = await client.get("/employee", params={"email": email, "count": 10, "fields": "id,firstName,lastName,email,displayName"})
        values = response.get("values", [])
        if values:
            return int(values[0]["id"])
    name = extract_person_name(prompt)
    if name:
        response = await client.get("/employee", params={"count": 20, "fields": "id,firstName,lastName,displayName"})
        target = name.lower().strip()
        for value in response.get("values", []):
            display_name = str(value.get("displayName") or f"{value.get('firstName','')} {value.get('lastName','')}").lower().strip()
            if display_name == target:
                return int(value["id"])
    return None


async def find_or_create_project(client: TripletexClient, prompt: str, customer_id: int | None) -> int | None:
    project_name = extract_project_name(prompt) or "Project"
    existing = await client.get("/project", params={"name": project_name, "count": 10, "fields": "id,name"})
    values = existing.get("values", [])
    if values:
        return int(values[0]["id"])
    payload: dict[str, Any] = {"name": project_name}
    if customer_id is not None:
        payload["customer"] = {"id": customer_id}
    created = await client.post("/project", payload)
    return pick_first_value_id(created)


def parse_order_lines(prompt: str) -> list[dict[str, Any]]:
    quoted = extract_quoted_items(prompt)
    amounts = extract_all_amounts(prompt)
    lines: list[dict[str, Any]] = []
    for idx, item in enumerate(quoted):
        if idx < len(amounts):
            lines.append(
                {
                    "description": item,
                    "count": 1,
                    "unitPriceExcludingVatCurrency": amounts[idx],
                }
            )
    if not lines:
        amount = amounts[0] if amounts else 0
        lines.append({"description": "Invoice line", "count": 1, "unitPriceExcludingVatCurrency": amount})
    return lines


def today_iso() -> str:
    return date.today().isoformat()


def invoice_lookup_range() -> tuple[str, str]:
    now = date.today()
    return (now - timedelta(days=3650)).isoformat(), (now + timedelta(days=1)).isoformat()
