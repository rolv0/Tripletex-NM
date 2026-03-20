from __future__ import annotations

import re
from datetime import date, timedelta
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


def _is_register_payment_intent(prompt_l: str) -> bool:
    payment_words = {"betaling", "betal", "register payment", "registere betaling", "registrer full betaling"}
    invoice_words = {"faktura", "invoice"}
    return _contains_any(prompt_l, payment_words) and _contains_any(prompt_l, invoice_words)


def _extract_amount(text: str) -> float | None:
    match = re.search(r"(\d[\d\s.,]*)\s*kr", text, re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    if raw.count(",") == 1 and raw.count(".") == 0:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_customer_name(text: str) -> str | None:
    patterns = [
        r"(?:kunden|customer)\s+(.+?)\s*(?:\(|har|has)",
        r"(?:for|to)\s+customer\s+(.+?)(?:,|\.|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'")
    return None


def _pick_customer(customers: list[dict[str, Any]], customer_name: str) -> dict[str, Any] | None:
    target = customer_name.lower().strip()
    exact = [c for c in customers if str(c.get("name", "")).lower().strip() == target]
    if exact:
        return exact[0]
    contains = [c for c in customers if target in str(c.get("name", "")).lower()]
    if contains:
        return contains[0]
    return customers[0] if customers else None


def _invoice_outstanding_amount(inv: dict[str, Any]) -> float:
    if inv.get("amountOutstanding") is not None:
        try:
            return float(inv["amountOutstanding"])
        except Exception:
            return 0.0
    try:
        amount_incl = float(inv.get("amountInclVat") or 0)
        paid = float(inv.get("paidAmount") or 0)
        return max(amount_incl - paid, 0.0)
    except Exception:
        return 0.0


def _pick_invoice_for_payment(
    invoices: list[dict[str, Any]],
    amount_ex_vat: float | None,
) -> dict[str, Any] | None:
    candidates = [inv for inv in invoices if _invoice_outstanding_amount(inv) > 0]
    if not candidates:
        return None
    if amount_ex_vat is not None:
        close = [
            inv
            for inv in candidates
            if inv.get("amountExVat") is not None and abs(float(inv["amountExVat"]) - amount_ex_vat) < 0.01
        ]
        if close:
            return close[0]
    return sorted(candidates, key=lambda inv: str(inv.get("invoiceDate", "")), reverse=True)[0]


async def _create_employee_with_retry(
    client: TripletexClient,
    first_name: str,
    last_name: str,
    email: str | None,
) -> tuple[dict[str, Any], str]:
    base_payload: dict[str, Any] = {
        "firstName": first_name or "Auto",
        "lastName": last_name or "User",
    }
    if email:
        base_payload["email"] = email

    variants: list[tuple[str, dict[str, Any]]] = [
        ("no_user_type", dict(base_payload)),
        ("userType_string_employee", {**base_payload, "userType": "EMPLOYEE"}),
        ("userType_string_standard", {**base_payload, "userType": "STANDARD"}),
        ("userType_number_1", {**base_payload, "userType": 1}),
        ("userType_obj_id_1", {**base_payload, "userType": {"id": 1}}),
    ]

    last_error = ""
    for variant_name, payload in variants:
        try:
            await client.post("/employee", payload)
            return payload, variant_name
        except RuntimeError as exc:
            last_error = str(exc)
            if "Brukertype" not in last_error and "userType" not in last_error:
                raise
    raise RuntimeError(last_error or "Employee create failed")


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
        payload, variant = await _create_employee_with_retry(client, first_name, last_name, email)
        return {"action": "create_employee", "variant": variant, "payload": payload}

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

    if _is_register_payment_intent(prompt_l):
        customer_name = _extract_customer_name(req.prompt) or ""
        amount = _extract_amount(req.prompt)
        today = date.today()
        from_date = (today - timedelta(days=3650)).isoformat()
        to_date = (today + timedelta(days=1)).isoformat()

        customer_id: int | None = None
        if customer_name:
            customer_resp = await client.get(
                "/customer",
                params={"name": customer_name, "count": 20, "fields": "id,name,organizationNumber"},
            )
            customer = _pick_customer(customer_resp.get("values", []), customer_name)
            if customer:
                customer_id = int(customer["id"])

        invoice_params: dict[str, Any] = {
            "invoiceDateFrom": from_date,
            "invoiceDateTo": to_date,
            "count": 200,
            "sorting": "-invoiceDate",
            "fields": "id,customer,customerId,invoiceDate,invoiceNumber,amountExVat,amountInclVat,paidAmount,amountOutstanding,invoiceStatus",
        }
        if customer_id is not None:
            invoice_params["customerId"] = str(customer_id)

        invoice_resp = await client.get("/invoice", params=invoice_params)
        invoices = invoice_resp.get("values", [])
        invoice = _pick_invoice_for_payment(invoices, amount)
        if not invoice:
            return {"action": "register_invoice_payment", "status": "no_invoice_found", "customer": customer_name}

        payment_types = await client.get("/invoice/paymentType", params={"count": 20, "fields": "id,description"})
        payment_values = payment_types.get("values", [])
        if not payment_values:
            return {"action": "register_invoice_payment", "status": "no_payment_type_found", "invoiceId": invoice.get("id")}
        payment_type_id = int(payment_values[0]["id"])

        paid_amount = _invoice_outstanding_amount(invoice)
        await client.put(
            f"/invoice/{invoice['id']}/:payment",
            params={
                "paymentDate": today.isoformat(),
                "paymentTypeId": payment_type_id,
                "paidAmount": paid_amount,
            },
        )
        return {
            "action": "register_invoice_payment",
            "invoiceId": invoice["id"],
            "paymentTypeId": payment_type_id,
            "paidAmount": paid_amount,
        }

    return {"action": "no_op", "reason": "unclassified_prompt"}
