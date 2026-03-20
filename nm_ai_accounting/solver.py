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
    for k in keywords:
        if " " in k:
            if k in text:
                return True
            continue
        if re.search(rf"\b{re.escape(k)}\b", text, re.IGNORECASE):
            return True
    return False


def _is_create_intent(prompt_l: str) -> bool:
    create_words = {
        "opprett",
        "lag",
        "create",
        "crea",
        "crear",
        "criar",
        "erstelle",
        "creer",
        "registrer",
        "register",
    }
    return _contains_any(prompt_l, create_words)


def _is_register_payment_intent(prompt_l: str) -> bool:
    payment_words = {"betaling", "betal", "register payment", "registere betaling", "registrer full betaling"}
    invoice_words = {"faktura", "invoice"}
    return _contains_any(prompt_l, payment_words) and _contains_any(prompt_l, invoice_words)


def _is_invoice_create_intent(prompt_l: str) -> bool:
    invoice_words = {"faktura", "invoice", "facture", "fatura", "rechnung"}
    return _is_create_intent(prompt_l) and _contains_any(prompt_l, invoice_words)


def _is_invoice_send_intent(prompt_l: str) -> bool:
    send_words = {"send", "envoyez", "envoyer", "enviar", "sendez", "sende"}
    return _contains_any(prompt_l, send_words)


def _is_travel_expense_intent(prompt_l: str) -> bool:
    words = {
        "reiserekning",
        "reiseregning",
        "travel expense",
        "expense report",
        "travel report",
    }
    return _contains_any(prompt_l, words)


def _is_credit_note_intent(prompt_l: str) -> bool:
    credit_words = {
        "kreditnota",
        "credit note",
        "creditnote",
        "gutschrift",
        "nota de credito",
        "nota de crédito",
        "avoir",
    }
    invoice_words = {"faktura", "invoice", "rechnung"}
    return _contains_any(prompt_l, credit_words) and _contains_any(prompt_l, invoice_words)


def _is_timesheet_intent(prompt_l: str) -> bool:
    hour_words = {"stunden", "hours", "timer", "timar", "timesheet", "timeregistrering"}
    context_words = {"aktivitet", "aktivität", "activity", "prosjekt", "project", "stundensatz", "hourly rate"}
    return _contains_any(prompt_l, hour_words) and _contains_any(prompt_l, context_words)


def _is_payroll_intent(prompt_l: str) -> bool:
    payroll_words = {"paie", "salary", "lonn", "lønn", "payroll", "salario"}
    return _contains_any(prompt_l, payroll_words)


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


def _extract_all_amounts(text: str) -> list[float]:
    values: list[float] = []
    for m in re.findall(r"(\d[\d\s.,]*)\s*(?:kr|nok)", text, flags=re.IGNORECASE):
        raw = m.replace(" ", "")
        if raw.count(",") == 1 and raw.count(".") == 0:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
        try:
            values.append(float(raw))
        except ValueError:
            pass
    return values


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


def _extract_person_name_and_email(text: str) -> tuple[str | None, str | None]:
    email = _extract_email(text)
    m = re.search(r"(?:for|til)\s+([A-ZÆØÅ][^,(]+)", text)
    name = m.group(1).strip() if m else None
    return name, email


def _extract_quoted_title(text: str) -> str | None:
    m = re.search(r"\"([^\"]+)\"", text)
    return m.group(1).strip() if m else None


def _extract_days_and_rate(text: str) -> tuple[int | None, float | None]:
    days = None
    day_rate = None
    m_days = re.search(r"(\d+)\s*(?:dagar|dager|days)", text, re.IGNORECASE)
    if m_days:
        days = int(m_days.group(1))
    m_rate = re.search(r"(?:dagssats|day rate)\s*(\d[\d\s.,]*)\s*kr", text, re.IGNORECASE)
    if m_rate:
        raw = m_rate.group(1).replace(" ", "").replace(",", ".")
        try:
            day_rate = float(raw)
        except ValueError:
            day_rate = None
    return days, day_rate


def _extract_expense_items(text: str) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for label, amount in re.findall(
        r"([A-Za-zÆØÅæøå \-]+?)\s+(\d[\d\s.,]*)\s*kr",
        text,
        flags=re.IGNORECASE,
    ):
        l = label.strip().lower()
        if any(token in l for token in ["dagssats", "day rate", "diett"]):
            continue
        raw = amount.replace(" ", "").replace(",", ".")
        try:
            items.append((label.strip(), float(raw)))
        except ValueError:
            continue
    return items


def _extract_hours(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:stunden|hours|timer|timar)", text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def _extract_hourly_rate(text: str) -> float | None:
    m = re.search(r"(?:stundensatz|hourly rate|timesats)\s*[:=]?\s*(\d[\d\s.,]*)", text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_quoted_values(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r'"([^"]+)"', text)]


def _extract_company_name(text: str) -> str | None:
    m = re.search(r"(?:for|für|til)\s+([A-ZÆØÅ][^,(]+?)(?:\s*\(|$)", text)
    return m.group(1).strip() if m else None


def _extract_project_manager(text: str) -> tuple[str | None, str | None]:
    patterns = [
        r"(?:prosjektleder|project manager|director del proyecto|diretor do projeto)\s*(?:er|is)?\s*([A-ZÆØÅ][^,(]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            name = m.group(1).strip().strip(".")
            email = _extract_email(text)
            return name, email
    return None, _extract_email(text)


def _extract_project_name(text: str) -> str | None:
    quoted = _extract_quoted_values(text)
    return quoted[0] if quoted else None


def _extract_quoted_items(text: str) -> list[str]:
    return [x.strip() for x in re.findall(r'"([^"]+)"', text)]


def _extract_invoice_lines(text: str) -> list[tuple[str, float]]:
    lines: list[tuple[str, float]] = []
    pattern = r'([A-Za-zÀ-ÿ0-9 \-]+?)\s*\((\d+)\)\s*(?:a|à)\s*(\d[\d\s.,]*)\s*(?:kr|nok)'
    for name, _number, amount in re.findall(pattern, text, flags=re.IGNORECASE):
        raw = amount.replace(" ", "").replace(",", ".")
        try:
            lines.append((name.strip(), float(raw)))
        except ValueError:
            continue
    if lines:
        return lines
    # fallback: one service in quotes + one amount
    quoted = _extract_quoted_items(text)
    amounts = _extract_all_amounts(text)
    if quoted and amounts:
        return [(quoted[0], amounts[0])]
    return []


def _extract_org_number(text: str) -> str | None:
    m = re.search(r"(?:org\.?-?nr\.?|org\.?-?no\.?)\s*([0-9]{9})", text, re.IGNORECASE)
    return m.group(1) if m else None


def _pick_invoice_for_credit(
    invoices: list[dict[str, Any]],
    amount_ex_vat: float | None,
    hint_text: str | None,
) -> dict[str, Any] | None:
    if not invoices:
        return None
    candidates = invoices[:]
    if amount_ex_vat is not None:
        amount_matches = [
            inv
            for inv in candidates
            if inv.get("amountExVat") is not None and abs(float(inv["amountExVat"]) - amount_ex_vat) < 0.01
        ]
        if amount_matches:
            candidates = amount_matches
    if hint_text:
        hint = hint_text.lower().strip()
        text_matches = []
        for inv in candidates:
            blob = f"{inv.get('comment','')} {inv.get('reference','')} {inv.get('invoiceNumber','')}".lower()
            if hint in blob:
                text_matches.append(inv)
        if text_matches:
            candidates = text_matches
    return sorted(candidates, key=lambda inv: str(inv.get("invoiceDate", "")), reverse=True)[0]


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    if not full_name:
        return None, None
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


async def _find_employee_id(client: TripletexClient, full_name: str | None, email: str | None) -> int | None:
    fields = "id,firstName,lastName,email,displayName"
    if email:
        res = await client.get("/employee", params={"email": email, "count": 20, "fields": fields})
        values = res.get("values", [])
        if values:
            return int(values[0]["id"])
    first, last = _split_name(full_name)
    params: dict[str, Any] = {"count": 50, "fields": fields}
    if first:
        params["firstName"] = first
    if last:
        params["lastName"] = last
    res = await client.get("/employee", params=params)
    values = res.get("values", [])
    if not values:
        return None
    if full_name:
        target = full_name.lower().strip()
        for v in values:
            display = str(v.get("displayName") or f"{v.get('firstName','')} {v.get('lastName','')}").lower().strip()
            if display == target:
                return int(v["id"])
    return int(values[0]["id"])


async def _find_or_create_customer(
    client: TripletexClient,
    customer_name: str,
    org_no: str | None = None,
) -> int | None:
    params: dict[str, Any] = {"customerName": customer_name, "count": 20, "fields": "id,name,organizationNumber"}
    if org_no:
        params["organizationNumber"] = org_no
    existing = await client.get("/customer", params=params)
    values = existing.get("values", [])
    if values:
        return int(values[0]["id"])
    payload: dict[str, Any] = {"name": customer_name, "isCustomer": True}
    if org_no:
        payload["organizationNumber"] = org_no
    created = await client.post("/customer", payload)
    return _extract_value_id(created)


async def _find_salary_type_id(client: TripletexClient, query: str) -> int | None:
    res = await client.get("/salary/type", params={"name": query, "count": 20, "fields": "id,name,number"})
    values = res.get("values", [])
    if values:
        return int(values[0]["id"])
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


def _extract_value_id(resp: dict[str, Any]) -> int | None:
    value = resp.get("value") if isinstance(resp, dict) else None
    if isinstance(value, dict) and value.get("id") is not None:
        try:
            return int(value["id"])
        except Exception:
            return None
    return None


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

    if _is_timesheet_intent(prompt_l):
        person_name, person_email = _extract_person_name_and_email(req.prompt)
        employee_id = await _find_employee_id(client, person_name, person_email or email)
        if employee_id is None:
            return {"action": "timesheet_entry", "status": "employee_not_found"}

        quoted = _extract_quoted_values(req.prompt)
        activity_name = quoted[0] if len(quoted) >= 1 else "Work"
        project_name = quoted[1] if len(quoted) >= 2 else "Project"
        company_name = _extract_company_name(req.prompt)
        hours = _extract_hours(req.prompt) or 0.0
        hourly_rate = _extract_hourly_rate(req.prompt)
        today = date.today().isoformat()

        customer_id: int | None = None
        if company_name:
            customers = await client.get("/customer", params={"name": company_name, "count": 10, "fields": "id,name"})
            cvals = customers.get("values", [])
            if cvals:
                customer_id = int(cvals[0]["id"])
            else:
                created_customer = await client.post("/customer", {"name": company_name, "isCustomer": True})
                customer_id = _extract_value_id(created_customer)

        projects = await client.get("/project", params={"name": project_name, "count": 20, "fields": "id,name,customer"})
        pvals = projects.get("values", [])
        project_id: int | None = int(pvals[0]["id"]) if pvals else None
        if project_id is None:
            project_payload: dict[str, Any] = {"name": project_name}
            if customer_id is not None:
                project_payload["customer"] = {"id": customer_id}
            created_project = await client.post("/project", project_payload)
            project_id = _extract_value_id(created_project)
        if project_id is None:
            return {"action": "timesheet_entry", "status": "project_not_found_or_created"}

        activities = await client.get("/activity", params={"name": activity_name, "count": 20, "fields": "id,name,isProjectActivity"})
        avals = activities.get("values", [])
        activity_id: int | None = int(avals[0]["id"]) if avals else None
        if activity_id is None:
            created_activity = await client.post("/activity", {"name": activity_name, "isProjectActivity": True})
            activity_id = _extract_value_id(created_activity)
        if activity_id is None:
            return {"action": "timesheet_entry", "status": "activity_not_found_or_created"}

        entry_payload: dict[str, Any] = {
            "employee": {"id": employee_id},
            "project": {"id": project_id},
            "activity": {"id": activity_id},
            "date": today,
            "hours": hours,
        }
        if hourly_rate is not None:
            entry_payload["hourlyRate"] = hourly_rate
        await client.post("/timesheet/entry", entry_payload)
        return {
            "action": "timesheet_entry",
            "employeeId": employee_id,
            "projectId": project_id,
            "activityId": activity_id,
            "hours": hours,
            "hourlyRate": hourly_rate,
        }

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

    if _is_create_intent(prompt_l) and _contains_any(
        prompt_l, {"avdeling", "avdelinger", "department", "departments", "departamento", "departamentos"}
    ):
        names = _extract_quoted_items(req.prompt)
        if not names:
            names = [name]
        created: list[str] = []
        for dep_name in names:
            payload = {"name": dep_name}
            await client.post("/department", payload)
            created.append(dep_name)
        return {"action": "create_department", "count": len(created), "names": created}

    if _is_create_intent(prompt_l) and _contains_any(
        prompt_l, {"prosjekt", "project", "proyecto", "proyectos", "projekt"}
    ):
        project_name = _extract_project_name(req.prompt) or name
        company_name = _extract_customer_name(req.prompt) or _extract_company_name(req.prompt) or ""
        org_no = _extract_org_number(req.prompt)
        manager_name, manager_email = _extract_project_manager(req.prompt)

        customer_id: int | None = None
        if company_name:
            params: dict[str, Any] = {"name": company_name, "count": 20, "fields": "id,name,organizationNumber"}
            if org_no:
                params["organizationNumber"] = org_no
            customers = await client.get("/customer", params=params)
            cvals = customers.get("values", [])
            if cvals:
                customer_id = int(cvals[0]["id"])
            else:
                created = await client.post("/customer", {"name": company_name, "isCustomer": True, "organizationNumber": org_no})
                customer_id = _extract_value_id(created)

        project_payload: dict[str, Any] = {"name": project_name}
        if customer_id is not None:
            project_payload["customer"] = {"id": customer_id}

        manager_id = await _find_employee_id(client, manager_name, manager_email)
        if manager_id is not None:
            project_payload["projectManager"] = {"id": manager_id}

        created_project = await client.post("/project", project_payload)
        return {
            "action": "create_project",
            "projectId": _extract_value_id(created_project),
            "payload": project_payload,
        }

    if _is_invoice_create_intent(prompt_l):
        customer_name = _extract_customer_name(req.prompt) or _extract_company_name(req.prompt) or "Customer"
        org_no = _extract_org_number(req.prompt)
        customer_id = await _find_or_create_customer(client, customer_name, org_no)
        if customer_id is None:
            return {"action": "create_invoice", "status": "customer_not_found_or_created"}

        today = date.today().isoformat()
        lines = _extract_invoice_lines(req.prompt)
        if not lines:
            amounts = _extract_all_amounts(req.prompt)
            amount = amounts[0] if amounts else 0.0
            title = (_extract_quoted_items(req.prompt) or ["Invoice line"])[0]
            lines = [(title, amount)]

        order_lines = [
            {
                "description": line_name,
                "count": 1,
                "unitPriceExcludingVatCurrency": amount,
            }
            for line_name, amount in lines
        ]
        inv_payload: dict[str, Any] = {
            "customer": {"id": customer_id},
            "invoiceDate": today,
            "invoiceDueDate": today,
            "orderLines": order_lines,
        }
        created_invoice = await client.post("/invoice", inv_payload)
        invoice_id = _extract_value_id(created_invoice)

        if invoice_id and _is_invoice_send_intent(prompt_l):
            await client.put(f"/invoice/{invoice_id}/:send", params={"sendType": "EMAIL"})
            return {"action": "create_and_send_invoice", "invoiceId": invoice_id, "lineCount": len(order_lines)}
        return {"action": "create_invoice", "invoiceId": invoice_id, "lineCount": len(order_lines)}

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

    if _is_credit_note_intent(prompt_l):
        customer_name = _extract_customer_name(req.prompt) or _extract_company_name(req.prompt) or ""
        amount = _extract_amount(req.prompt)
        org_no = _extract_org_number(req.prompt)
        quoted = _extract_quoted_values(req.prompt)
        hint = quoted[0] if quoted else None
        today = date.today()
        from_date = (today - timedelta(days=3650)).isoformat()
        to_date = (today + timedelta(days=1)).isoformat()

        customer_id: int | None = None
        if customer_name:
            params: dict[str, Any] = {"name": customer_name, "count": 20, "fields": "id,name,organizationNumber"}
            if org_no:
                params["organizationNumber"] = org_no
            customer_resp = await client.get("/customer", params=params)
            customer = _pick_customer(customer_resp.get("values", []), customer_name)
            if customer:
                customer_id = int(customer["id"])

        invoice_params: dict[str, Any] = {
            "invoiceDateFrom": from_date,
            "invoiceDateTo": to_date,
            "count": 200,
            "sorting": "-invoiceDate",
            "fields": "id,customer,customerId,invoiceDate,invoiceNumber,amountExVat,amountInclVat,paidAmount,amountOutstanding,comment,reference",
        }
        if customer_id is not None:
            invoice_params["customerId"] = str(customer_id)
        invoices_resp = await client.get("/invoice", params=invoice_params)
        invoice = _pick_invoice_for_credit(invoices_resp.get("values", []), amount, hint)
        if not invoice:
            return {"action": "create_credit_note", "status": "no_invoice_found", "customer": customer_name}

        await client.put(f"/invoice/{invoice['id']}/:createCreditNote")
        return {"action": "create_credit_note", "invoiceId": invoice["id"]}

    if _is_payroll_intent(prompt_l):
        person_name, person_email = _extract_person_name_and_email(req.prompt)
        employee_id = await _find_employee_id(client, person_name, person_email or email)
        if employee_id is None:
            return {"action": "run_payroll", "status": "employee_not_found"}

        amounts = _extract_all_amounts(req.prompt)
        base_amount = amounts[0] if amounts else 0.0
        bonus_amount = amounts[1] if len(amounts) > 1 else 0.0
        today_dt = date.today()

        base_type = await _find_salary_type_id(client, "lonn") or await _find_salary_type_id(client, "salary")
        bonus_type = await _find_salary_type_id(client, "bonus") or base_type
        if base_type is None:
            salary_types = await client.get("/salary/type", params={"count": 5, "fields": "id,name,number"})
            vals = salary_types.get("values", [])
            if vals:
                base_type = int(vals[0]["id"])
                bonus_type = int(vals[1]["id"]) if len(vals) > 1 else base_type
        if base_type is None:
            return {"action": "run_payroll", "status": "salary_type_not_found"}

        specs = [
            {"employee": {"id": employee_id}, "salaryType": {"id": base_type}, "amount": base_amount, "description": "Base salary"},
        ]
        if bonus_amount > 0:
            specs.append({"employee": {"id": employee_id}, "salaryType": {"id": bonus_type}, "amount": bonus_amount, "description": "Bonus"})

        payload = {
            "date": today_dt.isoformat(),
            "year": today_dt.year,
            "month": today_dt.month,
            "payslips": [
                {
                    "employee": {"id": employee_id},
                    "year": today_dt.year,
                    "month": today_dt.month,
                    "specifications": specs,
                }
            ],
        }
        created = await client.post("/salary/transaction", payload)
        return {"action": "run_payroll", "transactionId": _extract_value_id(created), "specCount": len(specs)}

    if _is_travel_expense_intent(prompt_l):
        person_name, person_email = _extract_person_name_and_email(req.prompt)
        title = _extract_quoted_title(req.prompt) or "Travel Expense"
        days, day_rate = _extract_days_and_rate(req.prompt)
        expenses = _extract_expense_items(req.prompt)
        today = date.today().isoformat()

        employee_id = await _find_employee_id(client, person_name, person_email)
        if employee_id is None:
            return {"action": "travel_expense", "status": "employee_not_found", "name": person_name, "email": person_email}

        travel_payload: dict[str, Any] = {
            "employee": {"id": employee_id},
            "title": title,
            "date": today,
            "travelDetails": {
                "departureDate": today,
                "returnDate": today,
                "purpose": title,
                "isCompensationFromRates": True,
            },
        }
        tr = await client.post("/travelExpense", travel_payload)
        travel_id = tr.get("value", {}).get("id")
        if not travel_id:
            return {"action": "travel_expense", "status": "travel_create_failed"}

        created_costs = 0
        cost_categories = await client.get("/travelExpense/costCategory", params={"count": 20, "fields": "id,description"})
        cc_values = cost_categories.get("values", [])
        cost_category_id = int(cc_values[0]["id"]) if cc_values else None

        for label, amount in expenses:
            cost_payload: dict[str, Any] = {
                "travelExpense": {"id": travel_id},
                "comments": label,
                "amountCurrencyIncVat": amount,
                "date": today,
                "isPaidByEmployee": True,
            }
            if cost_category_id is not None:
                cost_payload["costCategory"] = {"id": cost_category_id}
            try:
                await client.post("/travelExpense/cost", cost_payload)
                created_costs += 1
            except Exception:
                pass

        per_diem_created = False
        if days and day_rate:
            per_diem_payload: dict[str, Any] = {
                "travelExpense": {"id": travel_id},
                "count": days,
                "rate": day_rate,
            }
            try:
                await client.post("/travelExpense/perDiemCompensation", per_diem_payload)
                per_diem_created = True
            except Exception:
                pass

        return {
            "action": "travel_expense",
            "travelExpenseId": travel_id,
            "costItemsCreated": created_costs,
            "perDiemCreated": per_diem_created,
        }

    return {"action": "no_op", "reason": "unclassified_prompt"}
