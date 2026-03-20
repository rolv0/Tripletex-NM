from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from models import SolveRequest
from tripletex_client import TripletexClient

logger = logging.getLogger("nm-ai-accounting.solver")

CREATE_WORDS = {
    "opprett",
    "lag",
    "registrer",
    "register",
    "create",
    "crear",
    "crea",
    "creer",
    "creez",
    "criar",
    "crie",
    "erstellen",
    "erstelle",
    "add",
    "define",
    "defina",
    "definir",
    "set",
    "sett",
}
SEND_WORDS = {"send", "sende", "envoyer", "envoyez", "enviar", "envoie"}
INVOICE_WORDS = {"invoice", "faktura", "facture", "fatura", "factura", "rechnung"}
CUSTOMER_WORDS = {"kunde", "kunden", "customer", "cliente", "client"}
EMPLOYEE_WORDS = {"employee", "ansatt", "empleado", "employe", "mitarbeiter", "funcionario"}
PRODUCT_WORDS = {"produkt", "product", "producto", "produit"}
PROJECT_WORDS = {"prosjekt", "project", "proyecto", "projet", "projekt"}
PROJECT_UPDATE_WORDS = {
    "fixed price",
    "fastpris",
    "preco fixo",
    "precofijo",
    "prix fixe",
    "festpreis",
    "vinculado",
    "linked",
}
DEPARTMENT_WORDS = {"avdeling", "avdelinger", "department", "departments", "departamento", "departamentos"}
PAYROLL_WORDS = {"payroll", "salary", "salario", "paie", "salaire", "lonn", "loenn", "paye"}
TRAVEL_WORDS = {
    "reiseregning",
    "reiserekning",
    "travel expense",
    "expense report",
    "travel report",
    "reiseutlegg",
    "reisekostnad",
    "per diem",
}
DELETE_WORDS = {"delete", "remove", "slett", "fjern", "supprimer", "eliminar", "apagar", "loeschen"}
PAYMENT_WORDS = {
    "betaling",
    "betal",
    "register payment",
    "full payment",
    "paiement",
    "zahlung",
    "pago",
    "delbetaling",
    "partial payment",
    "part payment",
}
CREDIT_NOTE_WORDS = {
    "kreditnota",
    "credit note",
    "creditnote",
    "gutschrift",
    "nota de credito",
    "avoir",
    "credit memo",
}
TIMESHEET_HOUR_WORDS = {"hours", "stunden", "timer", "timar", "heures", "horas", "timesheet", "timeregistrering"}
TIMESHEET_CONTEXT_WORDS = {
    "activity",
    "aktivitet",
    "project",
    "prosjekt",
    "proyecto",
    "projet",
    "projekt",
    "hourly rate",
    "timesats",
    "stundensatz",
    "taux horaire",
    "taxa horaria",
}
ADMIN_WORDS = {
    "administrator",
    "admin",
    "kontoadministrator",
    "account administrator",
    "administrateur",
    "administrador",
}

INVOICE_ACTION_WORDS = {
    "fakturer",
    "invoice",
    "facturer",
    "faturar",
    "invoicer",
    "billing",
}

CUSTOMER_CREATE_WORDS = {
    "opprett",
    "lag",
    "registrer",
    "register",
    "create",
    "crear",
    "crea",
    "creer",
    "creez",
    "criar",
    "crie",
    "erstellen",
    "erstelle",
    "add",
}

MONTH_MAP = {
    "january": 1,
    "jan": 1,
    "januar": 1,
    "janvier": 1,
    "enero": 1,
    "janeiro": 1,
    "february": 2,
    "feb": 2,
    "februar": 2,
    "fevrier": 2,
    "febrero": 2,
    "fevereiro": 2,
    "march": 3,
    "mar": 3,
    "mars": 3,
    "marzo": 3,
    "marco": 3,
    "april": 4,
    "apr": 4,
    "avril": 4,
    "abril": 4,
    "may": 5,
    "mai": 5,
    "mayo": 5,
    "maio": 5,
    "june": 6,
    "jun": 6,
    "juni": 6,
    "juin": 6,
    "junio": 6,
    "junho": 6,
    "july": 7,
    "jul": 7,
    "juli": 7,
    "juillet": 7,
    "julio": 7,
    "julho": 7,
    "august": 8,
    "aug": 8,
    "aout": 8,
    "agosto": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "septembre": 9,
    "septiembre": 9,
    "setembro": 9,
    "october": 10,
    "oct": 10,
    "oktober": 10,
    "octobre": 10,
    "octubre": 10,
    "outubro": 10,
    "november": 11,
    "nov": 11,
    "novembre": 11,
    "noviembre": 11,
    "december": 12,
    "dec": 12,
    "desember": 12,
    "decembre": 12,
    "diciembre": 12,
    "dezembro": 12,
}


def _normalize_text(text: str) -> str:
    translit = text.translate(
        str.maketrans(
            {
                "\u00f8": "o",
                "\u00d8": "O",
                "\u00e5": "a",
                "\u00c5": "A",
                "\u00e6": "ae",
                "\u00c6": "AE",
            }
        )
    )
    # Handle common mojibake sequences seen in logs/source copies.
    for bad, good in (
        ("Ã¸", "o"),
        ("Ã˜", "O"),
        ("Ã¥", "a"),
        ("Ã…", "A"),
        ("Ã¦", "ae"),
        ("Ã†", "AE"),
        ("ÃƒÂ¸", "o"),
        ("ÃƒËœ", "O"),
        ("ÃƒÂ¥", "a"),
        ("Ãƒâ€¦", "A"),
        ("ÃƒÂ¦", "ae"),
        ("Ãƒâ€ ", "AE"),
    ):
        translit = translit.replace(bad, good)

    normalized = unicodedata.normalize("NFKD", translit)
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_marks.lower()


def _as_word_string(normalized_text: str) -> str:
    return f" {re.sub(r'[^a-z0-9]+', ' ', normalized_text).strip()} "


def _contains_any(normalized_text: str, keywords: set[str]) -> bool:
    haystack = _as_word_string(normalized_text)
    for keyword in keywords:
        needle = re.sub(r"[^a-z0-9]+", " ", _normalize_text(keyword)).strip()
        if needle and f" {needle} " in haystack:
            return True
    return False


def _extract_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0) if match else None


def _extract_float(raw: str) -> float | None:
    candidate = raw.replace(" ", "")
    if candidate.count(",") == 1 and candidate.count(".") == 0:
        candidate = candidate.replace(",", ".")
    else:
        candidate = candidate.replace(",", "")
    try:
        return float(candidate)
    except ValueError:
        return None


def _extract_all_amounts(text: str) -> list[float]:
    values: list[float] = []
    for amount_text in re.findall(r"(\d[\d\s.,]*)\s*(?:kr|nok)", text, flags=re.IGNORECASE):
        value = _extract_float(amount_text)
        if value is not None:
            values.append(value)
    return values


def _extract_amount(text: str) -> float | None:
    amounts = _extract_all_amounts(text)
    return amounts[0] if amounts else None


def _extract_quoted_items(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r'"([^"]+)"', text)]


def _clean_entity(candidate: str | None) -> str | None:
    if not candidate:
        return None
    cleaned = candidate.strip().strip("\"' ")
    cleaned = re.split(
        r"\b(?:born|f[oa]dt|geboren|nacido|nato|email|e-post|start date|startdato|for this month|for denne m[ao]neden)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,.")
    return cleaned or None


def _extract_name(text: str) -> str | None:
    patterns = [
        r"(?:named|name|navn|nom|nome|llamado|llamada)\s+([A-Z][^,\n.]+)",
        r"(?:employee|ansatt|employe|empleado)\s+(?:named\s+)?([A-Z][^,\n.]+)",
        r"(?:ny ansatt|new employee)\s+([A-Z][^,\n.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            cleaned = _clean_entity(match.group(1))
            if cleaned:
                return cleaned
    fallback = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    return _clean_entity(fallback.group(1) if fallback else None)


def _extract_org_number(text: str) -> str | None:
    match = re.search(r"org[^0-9]{0,12}([0-9]{9})", text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_customer_name(text: str) -> str | None:
    def _is_valid_customer_candidate(value: str | None) -> bool:
        if not value:
            return False
        normalized = _normalize_text(value).strip()
        if not normalized:
            return False
        if normalized.startswith("for "):
            return False
        if "%" in value:
            return False
        if normalized.startswith("50 ") or normalized.startswith("100 "):
            return False
        return True

    patterns = [
        r"(?:kunde(?:n)?|customer|cliente|client)\s+([^,(.\n]+)",
        r"(?:for|til|pour|para|fuer)\s+([A-Z][^,(.\n]+?)(?:\s*\(|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            cleaned = _clean_entity(match.group(1))
            if _is_valid_customer_candidate(cleaned):
                return cleaned
    org_context = re.search(r"([A-Z][^,(.\n]+?)\s*\(\s*org", text, re.IGNORECASE)
    candidate = _clean_entity(org_context.group(1) if org_context else None)
    return candidate if _is_valid_customer_candidate(candidate) else None


def _extract_person_name_and_email(text: str) -> tuple[str | None, str | None]:
    email = _extract_email(text)
    patterns = [
        r"(?:for|til|pour|para|fuer)\s+([A-Z][^,(.\n]+)",
        r"(?:employee|ansatt|employe|empleado)\s+([A-Z][^,(.\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean_entity(match.group(1)), email
    return _extract_name(text), email


def _to_iso_date(day: int, month: int, year: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except Exception:
        return None


def _parse_date_value(candidate: str) -> str | None:
    simple = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", candidate)
    if simple:
        return _to_iso_date(int(simple.group(1)), int(simple.group(2)), int(simple.group(3)))

    words = _normalize_text(candidate)
    by_name = re.search(r"(\d{1,2})\.?\s+([a-z]+)\s+(\d{4})", words)
    if not by_name:
        return None
    day = int(by_name.group(1))
    month_name = by_name.group(2)
    year = int(by_name.group(3))
    month = MONTH_MAP.get(month_name)
    if month is None:
        return None
    return _to_iso_date(day, month, year)


def _extract_context_date(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        parsed = _parse_date_value(match.group(1))
        if parsed:
            return parsed
    fallback = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})", text)
    if fallback:
        return _parse_date_value(fallback.group(1))
    return None


def _extract_birth_date(text: str) -> str | None:
    patterns = [
        r"(?:born|f[oa]dt|geboren|nacido|nee|ne|nascido)\s+([^\.\n,]+)",
        r"(?:date of birth|fodselsdato|geburtsdatum|fecha de nacimiento|data de nascimento)\s*[:=]?\s*([^\.\n,]+)",
    ]
    return _extract_context_date(text, patterns)


def _extract_start_date(text: str) -> str | None:
    patterns = [
        r"(?:start date|startdato|startdatum|fecha de inicio|data de inicio|commence|debut)\s*[:=]?\s*([^\.\n,]+)",
        r"(?:starts|starter)\s+([^\.\n,]+)",
    ]
    return _extract_context_date(text, patterns)


def _is_admin_request(prompt_n: str) -> bool:
    return _contains_any(prompt_n, ADMIN_WORDS)


def _extract_hours(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:hours|stunden|timer|timar|heures|horas)", text, re.IGNORECASE)
    if not match:
        return None
    return _extract_float(match.group(1))


def _extract_hourly_rate(text: str) -> float | None:
    match = re.search(
        r"(?:hourly rate|timesats|stundensatz|taux horaire|taxa horaria)\s*[:=]?\s*(\d[\d\s.,]*)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _extract_float(match.group(1))


def _extract_days_and_rate(text: str) -> tuple[int | None, float | None]:
    days: int | None = None
    day_rate: float | None = None

    days_match = re.search(r"(\d+)\s*(?:day|days|dager|dagar|jours|dias)", text, re.IGNORECASE)
    if days_match:
        days = int(days_match.group(1))

    rate_match = re.search(r"(?:day rate|dagssats)\s*(\d[\d\s.,]*)\s*(?:kr|nok)", text, re.IGNORECASE)
    if rate_match:
        day_rate = _extract_float(rate_match.group(1))
    return days, day_rate


def _extract_expense_items(text: str) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for label, amount_text in re.findall(r"([A-Za-z0-9 .\\/-]+?)\s+(\d[\d\s.,]*)\s*(?:kr|nok)", text, flags=re.IGNORECASE):
        label_norm = _normalize_text(label)
        if any(skip in label_norm for skip in ["dagssats", "day rate", "diett", "iva", "mva", "mwst"]):
            continue
        amount = _extract_float(amount_text)
        if amount is None:
            continue
        items.append((label.strip(), amount))
    return items


def _extract_project_name(text: str) -> str | None:
    quoted = _extract_quoted_items(text)
    if quoted:
        return quoted[0]
    match = re.search(r"(?:project|prosjekt|proyecto|projet|projekt)\s+([^,(.\n]+)", text, re.IGNORECASE)
    return _clean_entity(match.group(1) if match else None)


def _extract_project_manager(text: str) -> tuple[str | None, str | None]:
    email = _extract_email(text)
    patterns = [
        r"(?:project manager|prosjektleder|prosjektleiar|director del proyecto|diretor do projeto|gestor de projeto|directeur du projet|projektleiter)\s*(?:is|er|es|est)?\s*([A-Z][^,(.\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _clean_entity(match.group(1)), email
    return None, email


def _extract_invoice_lines(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []

    detailed_pattern = (
        r"([^,(]+?)\s*\((\d+)\)\s*(?:a|\u00e0)\s*(\d[\d\s.,]*)\s*(?:kr|nok)(?:\s*(?:with|med|com)?\s*(\d{1,2}(?:[.,]\d+)?)\s*%)?"
    )
    for line_name, _line_no, amount_text, vat_text in re.findall(detailed_pattern, text, flags=re.IGNORECASE):
        amount = _extract_float(amount_text)
        if amount is None:
            continue
        vat_rate = _extract_float(vat_text) if vat_text else None
        lines.append({"description": line_name.strip(), "amount": amount, "vat_rate": vat_rate})
    if lines:
        return lines

    quoted = _extract_quoted_items(text)
    amounts = _extract_all_amounts(text)
    if quoted and amounts:
        return [{"description": quoted[0], "amount": amounts[0], "vat_rate": None}]
    return []


def _extract_product_payload(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"isInactive": False}

    quoted = _extract_quoted_items(text)
    name = quoted[0] if quoted else None
    if not name:
        match = re.search(r"(?:product|produkt|producto|produit)\s+([^,(.\n]+)", text, re.IGNORECASE)
        name = _clean_entity(match.group(1) if match else None)
    payload["name"] = name or "Product"

    number_match = re.search(
        r"(?:product(?:\s*number|nummer)?|produktnummer|numero de producto|numero do produto|numero)\s*[:=]?\s*([0-9]{2,})",
        text,
        re.IGNORECASE,
    )
    if number_match:
        payload["number"] = number_match.group(1)

    price = _extract_amount(text)
    if price is not None:
        payload["priceExcludingVatCurrency"] = price

    vat_match = re.search(r"(\d{1,2}(?:[.,]\d+)?)\s*%", text)
    if vat_match:
        vat_rate = _extract_float(vat_match.group(1))
        if vat_rate is not None:
            payload["_vat_rate_hint"] = vat_rate

    return payload


def _extract_fixed_price_amount(text: str) -> float | None:
    patterns = [
        r"(?:fixed price|fastpris|preco fixo|prix fixe|festpreis)\s*(?:de|of|pa|paa|von)?\s*(\d[\d\s.,]*)\s*(?:kr|nok)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _extract_float(match.group(1))
            if value is not None:
                return value
    return _extract_amount(text)


def _extract_percentage(text: str) -> float | None:
    match = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", text)
    if not match:
        return None
    return _extract_float(match.group(1))


def _split_name(full_name: str | None) -> tuple[str | None, str | None]:
    if not full_name:
        return None, None
    parts = [part for part in full_name.strip().split() if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _extract_value_id(response: dict[str, Any]) -> int | None:
    value = response.get("value") if isinstance(response, dict) else None
    if isinstance(value, dict) and value.get("id") is not None:
        try:
            return int(value["id"])
        except Exception:
            return None
    return None


def _invoice_outstanding_amount(invoice: dict[str, Any]) -> float:
    if invoice.get("amountOutstanding") is not None:
        try:
            return float(invoice["amountOutstanding"])
        except Exception:
            return 0.0
    try:
        amount_incl = float(invoice.get("amountIncludingVat") or invoice.get("amount") or 0)
        paid_amount = float(invoice.get("paidAmount") or 0)
        return max(amount_incl - paid_amount, 0.0)
    except Exception:
        return 0.0


def _invoice_amount_ex_vat(invoice: dict[str, Any]) -> float | None:
    for key in ("amountExVat", "amountExcludingVat", "amountExcludingVatCurrency"):
        if invoice.get(key) is None:
            continue
        try:
            return float(invoice[key])
        except Exception:
            continue
    return None


def _pick_customer(customers: list[dict[str, Any]], customer_name: str) -> dict[str, Any] | None:
    if not customers:
        return None
    target = customer_name.lower().strip()
    exact = [customer for customer in customers if str(customer.get("name", "")).lower().strip() == target]
    if exact:
        return exact[0]
    contained = [customer for customer in customers if target in str(customer.get("name", "")).lower()]
    if contained:
        return contained[0]
    return customers[0]


def _pick_invoice_for_payment(invoices: list[dict[str, Any]], amount_ex_vat: float | None) -> dict[str, Any] | None:
    candidates = [invoice for invoice in invoices if _invoice_outstanding_amount(invoice) > 0]
    if not candidates:
        return None
    if amount_ex_vat is not None:
        close_match = []
        for invoice in candidates:
            value = _invoice_amount_ex_vat(invoice)
            if value is not None and abs(value - amount_ex_vat) < 0.01:
                close_match.append(invoice)
        if close_match:
            candidates = close_match
    return sorted(candidates, key=lambda item: str(item.get("invoiceDate", "")), reverse=True)[0]


def _pick_invoice_for_credit(
    invoices: list[dict[str, Any]],
    amount_ex_vat: float | None,
    hint_text: str | None,
) -> dict[str, Any] | None:
    if not invoices:
        return None
    candidates = invoices[:]
    if amount_ex_vat is not None:
        amount_match = []
        for invoice in candidates:
            value = _invoice_amount_ex_vat(invoice)
            if value is not None and abs(value - amount_ex_vat) < 0.01:
                amount_match.append(invoice)
        if amount_match:
            candidates = amount_match

    if hint_text:
        normalized_hint = _normalize_text(hint_text)
        text_match: list[dict[str, Any]] = []
        for invoice in candidates:
            blob = _normalize_text(
                f"{invoice.get('comment', '')} {invoice.get('reference', '')} {invoice.get('invoiceNumber', '')}"
            )
            if normalized_hint in blob:
                text_match.append(invoice)
        if text_match:
            candidates = text_match

    return sorted(candidates, key=lambda item: str(item.get("invoiceDate", "")), reverse=True)[0]


async def _find_employee_id(client: TripletexClient, full_name: str | None, email: str | None) -> int | None:
    fields = "id,firstName,lastName,email,displayName"
    if email:
        response = await client.get("/employee", params={"email": email, "count": 20, "fields": fields})
        values = response.get("values", [])
        if values:
            return int(values[0]["id"])

    first_name, last_name = _split_name(full_name)
    params: dict[str, Any] = {"count": 50, "fields": fields}
    if first_name:
        params["firstName"] = first_name
    if last_name:
        params["lastName"] = last_name
    response = await client.get("/employee", params=params)
    values = response.get("values", [])
    if not values:
        return None
    if full_name:
        target = full_name.lower().strip()
        for value in values:
            display_name = str(value.get("displayName") or f"{value.get('firstName', '')} {value.get('lastName', '')}").lower().strip()
            if display_name == target:
                return int(value["id"])
    return int(values[0]["id"])


async def _find_or_create_customer(client: TripletexClient, customer_name: str, org_no: str | None = None) -> int | None:
    params: dict[str, Any] = {"name": customer_name, "count": 20, "fields": "id,name,organizationNumber"}
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
    response = await client.get("/salary/type", params={"name": query, "count": 20, "fields": "id,name,number"})
    values = response.get("values", [])
    if values:
        return int(values[0]["id"])
    return None


async def _find_vat_type_id(client: TripletexClient, target_rate: float | None) -> int | None:
    try:
        response = await client.get("/ledger/vatType", params={"count": 100})
    except Exception:
        return None
    values = response.get("values", [])
    if not values:
        return None

    best_id_non_incoming: int | None = None
    best_diff_non_incoming = 9999.0
    best_id_any: int | None = None
    best_diff_any = 9999.0
    normalized_target = target_rate if target_rate is not None else 25.0
    for value in values:
        rate_candidate = None
        for field in ("rate", "percent", "percentage"):
            if value.get(field) is not None:
                rate_candidate = _extract_float(str(value[field]))
                if rate_candidate is not None:
                    break
        name_norm = _normalize_text(str(value.get("name", "")))
        if rate_candidate is None:
            if "standard" in name_norm and "25" in name_norm and "inngaende" not in name_norm and "input" not in name_norm:
                return int(value["id"])
            continue
        is_incoming = any(
            token in name_norm
            for token in ("inngaende", "input", "fradrag", "purchase", "kjop", "kjoep", "kost")
        )
        diff = abs(rate_candidate - normalized_target)
        if diff < best_diff_any:
            best_diff_any = diff
            best_id_any = int(value["id"])
        if not is_incoming and diff < best_diff_non_incoming:
            best_diff_non_incoming = diff
            best_id_non_incoming = int(value["id"])
    return best_id_non_incoming if best_id_non_incoming is not None else best_id_any


def _is_missing_bank_account_error(err: Exception | str) -> bool:
    text_n = _normalize_text(str(err))
    return "bankkontonummer" in text_n or "bankkonto" in text_n or ("bank account" in text_n and "invoice" in text_n)


async def _create_employee_with_retry(
    client: TripletexClient,
    first_name: str,
    last_name: str,
    email: str | None,
    date_of_birth: str | None,
    prefer_extended: bool,
) -> tuple[dict[str, Any], str, int | None]:
    payload_base: dict[str, Any] = {"firstName": first_name or "Auto", "lastName": last_name or "User"}
    if email:
        payload_base["email"] = email
    if date_of_birth:
        payload_base["dateOfBirth"] = date_of_birth

    variants: list[tuple[str, dict[str, Any]]] = []
    if prefer_extended:
        variants.append(("userType_extended", {**payload_base, "userType": "EXTENDED"}))
    variants.extend(
        [
        ("userType_standard", {**payload_base, "userType": "STANDARD"}),
        ("userType_extended", {**payload_base, "userType": "EXTENDED"}),
        ("no_user_type", dict(payload_base)),
        ]
    )

    last_error = ""
    for variant_name, payload in variants:
        try:
            created = await client.post("/employee", payload)
            return payload, variant_name, _extract_value_id(created)
        except RuntimeError as exc:
            last_error = str(exc)
            if "Brukertype" not in last_error and "userType" not in last_error:
                raise
    raise RuntimeError(last_error or "Employee create failed")


def _is_register_payment_intent(prompt_n: str) -> bool:
    # Keep this strict to avoid routing "create invoice/order" prompts into payment flow.
    if not (_contains_any(prompt_n, PAYMENT_WORDS) and _contains_any(prompt_n, INVOICE_WORDS)):
        return False
    has_register_verb = _contains_any(
        prompt_n,
        {
            "register",
            "registrer",
            "registrer",
            "registrar",
            "enregistrer",
            "book",
            "bokfor",
            "bokforing",
        },
    )
    explicit_payment_phrase = any(
        phrase in prompt_n
        for phrase in ("full payment", "partial payment", "part payment", "delbetaling", "paiement complet")
    )
    return has_register_verb or explicit_payment_phrase


def _is_credit_note_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, CREDIT_NOTE_WORDS) and _contains_any(prompt_n, INVOICE_WORDS)


def _is_timesheet_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, TIMESHEET_HOUR_WORDS) and _contains_any(prompt_n, TIMESHEET_CONTEXT_WORDS)


def _is_payroll_intent(prompt_n: str) -> bool:
    if _contains_any(prompt_n, PAYROLL_WORDS):
        return True
    if re.search(r"\b(kjor|kjor|run|executez|executer|execute)\b.*\b(lonn|payroll|paie|salary|salario|salaire)\b", prompt_n):
        return True
    return ("engangsbonus" in prompt_n or "bonus" in prompt_n) and (
        "maned" in prompt_n or "mois" in prompt_n or "month" in prompt_n
    )


def _is_travel_expense_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, TRAVEL_WORDS)


def _is_delete_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, DELETE_WORDS)


def _is_invoice_create_or_send_intent(prompt_n: str) -> bool:
    invoice_mention = _contains_any(prompt_n, INVOICE_WORDS) or _contains_any(prompt_n, INVOICE_ACTION_WORDS)
    return invoice_mention and (
        _contains_any(prompt_n, CREATE_WORDS) or _contains_any(prompt_n, SEND_WORDS) or "line" in prompt_n
    )


def _is_project_intent(prompt_n: str) -> bool:
    if not (_contains_any(prompt_n, PROJECT_WORDS) or "prosjektet" in prompt_n):
        return False
    if _contains_any(prompt_n, CREATE_WORDS) or _contains_any(prompt_n, PROJECT_UPDATE_WORDS):
        return True
    return (
        "project manager" in prompt_n
        or "prosjektleder" in prompt_n
        or "prosjektleiar" in prompt_n
        or "gestor de projeto" in prompt_n
    )


def _is_department_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, DEPARTMENT_WORDS) and _contains_any(prompt_n, CREATE_WORDS)


def _is_product_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, PRODUCT_WORDS) and _contains_any(prompt_n, CREATE_WORDS)


def _is_employee_intent(prompt_n: str) -> bool:
    return _contains_any(prompt_n, EMPLOYEE_WORDS) and _contains_any(prompt_n, CREATE_WORDS)


def _is_customer_intent(prompt_n: str) -> bool:
    # Avoid false positives like "fakturer kunden for 50% ..."
    return _contains_any(prompt_n, CUSTOMER_WORDS) and _contains_any(prompt_n, CUSTOMER_CREATE_WORDS)


async def solve_task(req: SolveRequest) -> dict[str, Any]:
    prompt = req.prompt
    prompt_n = _normalize_text(prompt)

    client = TripletexClient(
        base_url=req.tripletex_credentials.base_url,
        session_token=req.tripletex_credentials.session_token,
    )

    logger.info(
        "intent_flags payment=%s credit=%s timesheet=%s payroll=%s travel=%s delete=%s invoice=%s project=%s department=%s product=%s employee=%s customer=%s",
        _is_register_payment_intent(prompt_n),
        _is_credit_note_intent(prompt_n),
        _is_timesheet_intent(prompt_n),
        _is_payroll_intent(prompt_n),
        _is_travel_expense_intent(prompt_n),
        _is_delete_intent(prompt_n),
        _is_invoice_create_or_send_intent(prompt_n),
        _is_project_intent(prompt_n),
        _is_department_intent(prompt_n),
        _is_product_intent(prompt_n),
        _is_employee_intent(prompt_n),
        _is_customer_intent(prompt_n),
    )

    name = _extract_name(prompt) or "Auto Generated"
    email = _extract_email(prompt)

    if _is_project_intent(prompt_n) and _is_invoice_create_or_send_intent(prompt_n):
        project_name = _extract_project_name(prompt) or name
        company_name = _extract_customer_name(prompt) or "Customer"
        org_no = _extract_org_number(prompt)
        manager_name, manager_email = _extract_project_manager(prompt)
        fixed_price_amount = _extract_fixed_price_amount(prompt)
        part_percent = _extract_percentage(prompt)

        customer_id = await _find_or_create_customer(client, company_name, org_no)
        if customer_id is None:
            return {"action": "project_invoice_combo", "status": "customer_not_found_or_created"}

        manager_id = await _find_employee_id(client, manager_name, manager_email)

        project_id: int | None = None
        existing = await client.get("/project", params={"name": project_name, "count": 20, "fields": "id,name"})
        existing_values = existing.get("values", [])
        if existing_values:
            project_id = int(existing_values[0]["id"])
        else:
            project_payload: dict[str, Any] = {"name": project_name, "customer": {"id": customer_id}}
            if manager_id is not None:
                project_payload["projectManager"] = {"id": manager_id}
            if fixed_price_amount is not None:
                project_payload["isFixedPrice"] = True
                project_payload["fixedPrice"] = fixed_price_amount
            created_project = await client.post("/project", project_payload)
            project_id = _extract_value_id(created_project)

        if project_id is not None and fixed_price_amount is not None:
            try:
                await client.put(
                    f"/project/{project_id}",
                    {"isFixedPrice": True, "fixedPrice": fixed_price_amount, **({"projectManager": {"id": manager_id}} if manager_id else {})},
                )
            except Exception:
                logger.warning("project_fixed_price_update_failed project_id=%s", project_id)

        invoice_amount = _extract_amount(prompt) or 0.0
        if fixed_price_amount is not None and part_percent is not None and 0 < part_percent <= 100:
            invoice_amount = round(fixed_price_amount * (part_percent / 100.0), 2)
        if invoice_amount <= 0 and fixed_price_amount is not None:
            invoice_amount = fixed_price_amount

        order_payload: dict[str, Any] = {
            "customer": {"id": customer_id},
            "orderDate": date.today().isoformat(),
            "deliveryDate": date.today().isoformat(),
            "orderLines": [
                {
                    "description": f"Delbetaling prosjekt {project_name}",
                    "count": 1,
                    "unitPriceExcludingVatCurrency": invoice_amount,
                }
            ],
        }
        if project_id is not None:
            order_payload["project"] = {"id": project_id}
        created_order = await client.post("/order", order_payload)
        order_id = _extract_value_id(created_order)
        if order_id is None:
            return {"action": "project_invoice_combo", "status": "order_create_failed", "projectId": project_id}

        try:
            created_invoice = await client.put(
                f"/order/{order_id}/:invoice",
                params={"invoiceDate": date.today().isoformat(), "sendToCustomer": False, "sendType": "MANUAL"},
            )
            invoice_id = _extract_value_id(created_invoice)
        except RuntimeError as exc:
            if _is_missing_bank_account_error(exc):
                return {
                    "action": "project_invoice_combo",
                    "status": "blocked_missing_bank_account",
                    "customerId": customer_id,
                    "projectId": project_id,
                    "orderId": order_id,
                    "invoiceAmount": invoice_amount,
                    "fixedPriceAmount": fixed_price_amount,
                    "partPercent": part_percent,
                }
            raise
        if invoice_id is None:
            latest = await client.get(
                "/invoice",
                params={
                    "customerId": str(customer_id),
                    "invoiceDateFrom": (date.today() - timedelta(days=30)).isoformat(),
                    "invoiceDateTo": (date.today() + timedelta(days=1)).isoformat(),
                    "count": 5,
                    "sorting": "-invoiceDate",
                    "fields": "id,invoiceDate,customer",
                },
            )
            latest_values = latest.get("values", [])
            if latest_values:
                invoice_id = int(latest_values[0]["id"])

        return {
            "action": "project_invoice_combo",
            "customerId": customer_id,
            "projectId": project_id,
            "orderId": order_id,
            "invoiceId": invoice_id,
            "invoiceAmount": invoice_amount,
            "fixedPriceAmount": fixed_price_amount,
            "partPercent": part_percent,
        }

    if _is_register_payment_intent(prompt_n):
        customer_name = _extract_customer_name(prompt) or ""
        amount_ex_vat = _extract_amount(prompt)
        org_no = _extract_org_number(prompt)
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
            "fields": "id,customer,invoiceDate,invoiceNumber,amountExcludingVat,amountExcludingVatCurrency,amountIncludingVat,paidAmount,amountOutstanding,invoiceStatus",
        }
        if customer_id is not None:
            invoice_params["customerId"] = str(customer_id)

        invoice_resp = await client.get("/invoice", params=invoice_params)
        invoice = _pick_invoice_for_payment(invoice_resp.get("values", []), amount_ex_vat)
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
            params={"paymentDate": today.isoformat(), "paymentTypeId": payment_type_id, "paidAmount": paid_amount},
        )
        return {
            "action": "register_invoice_payment",
            "invoiceId": invoice["id"],
            "paymentTypeId": payment_type_id,
            "paidAmount": paid_amount,
        }

    if _is_credit_note_intent(prompt_n):
        customer_name = _extract_customer_name(prompt) or ""
        amount_ex_vat = _extract_amount(prompt)
        org_no = _extract_org_number(prompt)
        quoted = _extract_quoted_items(prompt)
        hint_text = quoted[0] if quoted else None
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
            "fields": "id,customer,invoiceDate,invoiceNumber,amountExcludingVat,amountExcludingVatCurrency,amountIncludingVat,paidAmount,amountOutstanding,comment,reference",
        }
        if customer_id is not None:
            invoice_params["customerId"] = str(customer_id)
        invoices_resp = await client.get("/invoice", params=invoice_params)
        invoice = _pick_invoice_for_credit(invoices_resp.get("values", []), amount_ex_vat, hint_text)
        if not invoice:
            return {"action": "create_credit_note", "status": "no_invoice_found", "customer": customer_name}

        await client.put(f"/invoice/{invoice['id']}/:createCreditNote")
        return {"action": "create_credit_note", "invoiceId": invoice["id"]}

    if _is_timesheet_intent(prompt_n):
        person_name, person_email = _extract_person_name_and_email(prompt)
        employee_id = await _find_employee_id(client, person_name, person_email or email)
        if employee_id is None:
            return {"action": "timesheet_entry", "status": "employee_not_found"}

        quoted = _extract_quoted_items(prompt)
        activity_name = quoted[0] if len(quoted) >= 1 else "Work"
        project_name = quoted[1] if len(quoted) >= 2 else "Project"
        company_name = _extract_customer_name(prompt)
        org_no = _extract_org_number(prompt)
        hours = _extract_hours(prompt) or 0.0
        hourly_rate = _extract_hourly_rate(prompt)
        today_iso = date.today().isoformat()

        customer_id: int | None = None
        if company_name:
            customer_id = await _find_or_create_customer(client, company_name, org_no)

        projects = await client.get("/project", params={"name": project_name, "count": 20, "fields": "id,name,customer"})
        project_values = projects.get("values", [])
        project_id = int(project_values[0]["id"]) if project_values else None
        if project_id is None:
            project_payload: dict[str, Any] = {"name": project_name}
            if customer_id is not None:
                project_payload["customer"] = {"id": customer_id}
            created_project = await client.post("/project", project_payload)
            project_id = _extract_value_id(created_project)
        if project_id is None:
            return {"action": "timesheet_entry", "status": "project_not_found_or_created"}

        activities = await client.get("/activity", params={"name": activity_name, "count": 20, "fields": "id,name,isProjectActivity"})
        activity_values = activities.get("values", [])
        activity_id = int(activity_values[0]["id"]) if activity_values else None
        if activity_id is None:
            created_activity = await client.post("/activity", {"name": activity_name, "isProjectActivity": True})
            activity_id = _extract_value_id(created_activity)
        if activity_id is None:
            return {"action": "timesheet_entry", "status": "activity_not_found_or_created"}

        entry_payload: dict[str, Any] = {
            "employee": {"id": employee_id},
            "project": {"id": project_id},
            "activity": {"id": activity_id},
            "date": today_iso,
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

    if _is_payroll_intent(prompt_n):
        person_name, person_email = _extract_person_name_and_email(prompt)
        employee_id = await _find_employee_id(client, person_name, person_email or email)
        if employee_id is None:
            return {"action": "run_payroll", "status": "employee_not_found"}

        amounts = _extract_all_amounts(prompt)
        base_amount = amounts[0] if amounts else 0.0
        bonus_amount = amounts[1] if len(amounts) > 1 else 0.0
        today_dt = date.today()

        base_type = await _find_salary_type_id(client, "salary")
        if base_type is None:
            base_type = await _find_salary_type_id(client, "lonn")
        bonus_type = await _find_salary_type_id(client, "bonus") or base_type
        if base_type is None:
            salary_types = await client.get("/salary/type", params={"count": 5, "fields": "id,name,number"})
            values = salary_types.get("values", [])
            if values:
                base_type = int(values[0]["id"])
                bonus_type = int(values[1]["id"]) if len(values) > 1 else base_type
        if base_type is None:
            return {"action": "run_payroll", "status": "salary_type_not_found"}

        specifications = [
            {
                "employee": {"id": employee_id},
                "salaryType": {"id": base_type},
                "description": "Base salary",
                "count": 1,
                "rate": base_amount,
            }
        ]
        if bonus_amount > 0:
            specifications.append(
                {
                    "employee": {"id": employee_id},
                    "salaryType": {"id": bonus_type},
                    "description": "Bonus",
                    "count": 1,
                    "rate": bonus_amount,
                }
            )

        payload = {
            "date": today_dt.isoformat(),
            "year": today_dt.year,
            "month": today_dt.month,
            "payslips": [
                {
                    "employee": {"id": employee_id},
                    "year": today_dt.year,
                    "month": today_dt.month,
                    "specifications": specifications,
                }
            ],
        }
        try:
            created = await client.post("/salary/transaction", payload)
        except RuntimeError as exc:
            # Some environments accept amount-style specs better than rate/count.
            logger.warning("payroll_rate_count_failed retrying_amount error=%s", exc)
            fallback_specs = [
                {
                    "employee": {"id": employee_id},
                    "salaryType": {"id": base_type},
                    "amount": base_amount,
                    "description": "Base salary",
                }
            ]
            if bonus_amount > 0:
                fallback_specs.append(
                    {
                        "employee": {"id": employee_id},
                        "salaryType": {"id": bonus_type},
                        "amount": bonus_amount,
                        "description": "Bonus",
                    }
                )
            fallback_payload = {
                "date": today_dt.isoformat(),
                "year": today_dt.year,
                "month": today_dt.month,
                "payslips": [
                    {
                        "employee": {"id": employee_id},
                        "year": today_dt.year,
                        "month": today_dt.month,
                        "specifications": fallback_specs,
                    }
                ],
            }
            created = await client.post("/salary/transaction", fallback_payload)
        return {"action": "run_payroll", "transactionId": _extract_value_id(created), "specCount": len(specifications)}

    if _is_travel_expense_intent(prompt_n):
        person_name, person_email = _extract_person_name_and_email(prompt)
        title = (_extract_quoted_items(prompt) or ["Travel Expense"])[0]
        days, day_rate = _extract_days_and_rate(prompt)
        expenses = _extract_expense_items(prompt)
        today_iso = date.today().isoformat()

        employee_id = await _find_employee_id(client, person_name, person_email)
        if employee_id is None:
            return {"action": "travel_expense", "status": "employee_not_found", "name": person_name, "email": person_email}

        if _is_delete_intent(prompt_n):
            search_params: dict[str, Any] = {
                "employeeId": str(employee_id),
                "count": 50,
                "sorting": "-date",
                "fields": "id,title,date,employee",
            }
            travel_list = await client.get("/travelExpense", params=search_params)
            values = travel_list.get("values", [])
            title_norm = _normalize_text(title)
            target = None
            for travel in values:
                if title_norm and title_norm in _normalize_text(str(travel.get("title", ""))):
                    target = travel
                    break
            if target is None and values:
                target = values[0]
            if not target:
                return {"action": "delete_travel_expense", "status": "not_found"}
            await client.delete(f"/travelExpense/{target['id']}")
            return {"action": "delete_travel_expense", "travelExpenseId": target["id"]}

        travel_payload: dict[str, Any] = {
            "employee": {"id": employee_id},
            "title": title,
            "date": today_iso,
            "travelDetails": {
                "departureDate": today_iso,
                "returnDate": today_iso,
                "purpose": title,
                "isCompensationFromRates": True,
            },
        }
        travel_response = await client.post("/travelExpense", travel_payload)
        travel_id = _extract_value_id(travel_response)
        if not travel_id:
            return {"action": "travel_expense", "status": "travel_create_failed"}

        created_costs = 0
        cost_categories = await client.get("/travelExpense/costCategory", params={"count": 20, "fields": "id,description"})
        category_values = cost_categories.get("values", [])
        cost_category_id = int(category_values[0]["id"]) if category_values else None

        for label, amount in expenses:
            payload: dict[str, Any] = {
                "travelExpense": {"id": travel_id},
                "comments": label,
                "amountCurrencyIncVat": amount,
                "date": today_iso,
                "isPaidByEmployee": True,
            }
            if cost_category_id is not None:
                payload["costCategory"] = {"id": cost_category_id}
            try:
                await client.post("/travelExpense/cost", payload)
                created_costs += 1
            except Exception:
                logger.warning("travel_cost_create_failed label=%r amount=%s", label, amount)

        per_diem_created = False
        if days and day_rate:
            per_diem_payload: dict[str, Any] = {"travelExpense": {"id": travel_id}, "count": days, "rate": day_rate}
            try:
                await client.post("/travelExpense/perDiemCompensation", per_diem_payload)
                per_diem_created = True
            except Exception:
                logger.warning("travel_per_diem_create_failed days=%s day_rate=%s", days, day_rate)

        return {
            "action": "travel_expense",
            "travelExpenseId": travel_id,
            "costItemsCreated": created_costs,
            "perDiemCreated": per_diem_created,
        }

    if _is_invoice_create_or_send_intent(prompt_n):
        customer_name = _extract_customer_name(prompt) or "Customer"
        org_no = _extract_org_number(prompt)
        customer_id = await _find_or_create_customer(client, customer_name, org_no)
        if customer_id is None:
            return {"action": "create_invoice", "status": "customer_not_found_or_created"}

        today_iso = date.today().isoformat()
        lines = _extract_invoice_lines(prompt)
        if not lines:
            amount = _extract_amount(prompt) or 0.0
            title = (_extract_quoted_items(prompt) or ["Invoice line"])[0]
            lines = [{"description": title, "amount": amount, "vat_rate": None}]

        order_lines: list[dict[str, Any]] = []
        vat_id_cache: dict[float, int | None] = {}
        for line in lines:
            line_payload: dict[str, Any] = {
                "description": line["description"],
                "count": 1,
                "unitPriceExcludingVatCurrency": line["amount"],
            }
            line_vat_rate = line.get("vat_rate")
            vat_id: int | None = None
            if isinstance(line_vat_rate, float):
                if line_vat_rate not in vat_id_cache:
                    vat_id_cache[line_vat_rate] = await _find_vat_type_id(client, line_vat_rate)
                vat_id = vat_id_cache[line_vat_rate]
            if vat_id is not None:
                line_payload["vatType"] = {"id": vat_id}
            order_lines.append(line_payload)

        # Tripletex expects invoice creation from order in many setups.
        # Avoid direct /invoice with orderLines first because it often yields 422 (empty orders).
        invoice_id: int | None = None
        order_id: int | None = None
        order_payload = {
            "customer": {"id": customer_id},
            "orderDate": today_iso,
            "deliveryDate": today_iso,
            "orderLines": order_lines,
        }
        try:
            created_order = await client.post("/order", order_payload)
            order_id = _extract_value_id(created_order)
        except RuntimeError as exc:
            # Fallback: remove potential strict fields and retry with minimal line payload.
            logger.warning("order_create_failed retrying_minimal error=%s", exc)
            minimal_order_lines = []
            for line in order_lines:
                minimal_order_lines.append(
                    {
                        "description": line.get("description", "Invoice line"),
                        "count": line.get("count", 1),
                        "unitPriceExcludingVatCurrency": line.get("unitPriceExcludingVatCurrency", 0),
                    }
                )
            created_order = await client.post(
                "/order",
                {
                    "customer": {"id": customer_id},
                    "orderDate": today_iso,
                    "deliveryDate": today_iso,
                    "orderLines": minimal_order_lines,
                },
            )
            order_id = _extract_value_id(created_order)

        if order_id is not None:
            should_send = False
            try:
                created_invoice = await client.put(
                    f"/order/{order_id}/:invoice",
                    params={
                        "invoiceDate": today_iso,
                        "sendToCustomer": should_send,
                        "sendType": "EMAIL" if should_send else "MANUAL",
                    },
                )
                invoice_id = _extract_value_id(created_invoice)
            except RuntimeError as exc:
                if _is_missing_bank_account_error(exc):
                    return {
                        "action": "create_invoice",
                        "status": "blocked_missing_bank_account",
                        "orderId": order_id,
                        "lineCount": len(order_lines),
                    }
                # Secondary fallback to traditional /invoice with linked order.
                try:
                    created_invoice = await client.post(
                        "/invoice",
                        {
                            "customer": {"id": customer_id},
                            "invoiceDate": today_iso,
                            "invoiceDueDate": today_iso,
                            "orders": [{"id": order_id}],
                        },
                    )
                    invoice_id = _extract_value_id(created_invoice)
                except RuntimeError as post_exc:
                    if _is_missing_bank_account_error(post_exc):
                        return {
                            "action": "create_invoice",
                            "status": "blocked_missing_bank_account",
                            "orderId": order_id,
                            "lineCount": len(order_lines),
                        }
                    logger.warning("invoice_create_fallback_failed error=%s", post_exc)
            if invoice_id is None:
                # Some action endpoints may return sparse payloads; recover by searching latest invoice.
                latest = await client.get(
                    "/invoice",
                    params={
                        "customerId": str(customer_id),
                        "invoiceDateFrom": (date.today() - timedelta(days=30)).isoformat(),
                        "invoiceDateTo": (date.today() + timedelta(days=1)).isoformat(),
                        "count": 5,
                        "sorting": "-invoiceDate",
                        "fields": "id,invoiceDate,customer",
                    },
                )
                latest_values = latest.get("values", [])
                if latest_values:
                    invoice_id = int(latest_values[0]["id"])

        if invoice_id is None:
            return {"action": "create_invoice", "status": "invoice_create_failed"}

        if _contains_any(prompt_n, SEND_WORDS):
            try:
                await client.put(f"/invoice/{invoice_id}/:send", params={"sendType": "EMAIL"})
            except Exception:
                logger.warning("invoice_send_failed invoice_id=%s", invoice_id)
            return {
                "action": "create_and_send_invoice",
                "invoiceId": invoice_id,
                "orderId": order_id,
                "lineCount": len(order_lines),
            }

        return {
            "action": "create_invoice",
            "invoiceId": invoice_id,
            "orderId": order_id,
            "lineCount": len(order_lines),
        }

    if _is_project_intent(prompt_n):
        project_name = _extract_project_name(prompt) or name
        company_name = _extract_customer_name(prompt)
        org_no = _extract_org_number(prompt)
        manager_name, manager_email = _extract_project_manager(prompt)
        fixed_price_amount = _extract_fixed_price_amount(prompt) if _contains_any(prompt_n, PROJECT_UPDATE_WORDS) else None

        customer_id: int | None = None
        if company_name:
            customer_id = await _find_or_create_customer(client, company_name, org_no)

        manager_id = await _find_employee_id(client, manager_name, manager_email)
        project_id: int | None = None
        try:
            existing = await client.get(
                "/project",
                params={"name": project_name, "count": 20, "fields": "id,name,customer,projectManager,isFixedPrice,fixedPrice"},
            )
            values = existing.get("values", [])
            if values:
                project_id = int(values[0]["id"])
        except Exception:
            values = []

        if project_id is None:
            project_payload: dict[str, Any] = {"name": project_name}
            if customer_id is not None:
                project_payload["customer"] = {"id": customer_id}
            if manager_id is not None:
                project_payload["projectManager"] = {"id": manager_id}
            if fixed_price_amount is not None:
                project_payload["isFixedPrice"] = True
                project_payload["fixedPrice"] = fixed_price_amount
            created_project = await client.post("/project", project_payload)
            project_id = _extract_value_id(created_project)
            return {"action": "create_project", "projectId": project_id, "payload": project_payload}

        update_payload: dict[str, Any] = {}
        if manager_id is not None:
            update_payload["projectManager"] = {"id": manager_id}
        if fixed_price_amount is not None:
            update_payload["isFixedPrice"] = True
            update_payload["fixedPrice"] = fixed_price_amount

        if update_payload:
            updated = False
            attempts = [update_payload]
            if fixed_price_amount is not None:
                attempts.append({"isFixedPrice": True, "fixedPriceAmount": fixed_price_amount})
                attempts.append({"isFixedPrice": True})
            for payload in attempts:
                try:
                    await client.put(f"/project/{project_id}", payload)
                    updated = True
                    break
                except Exception:
                    continue
            return {"action": "update_project", "projectId": project_id, "updated": updated, "payload": update_payload}

        return {"action": "project_already_exists", "projectId": project_id}

    if _is_department_intent(prompt_n):
        names = _extract_quoted_items(prompt)
        if not names:
            normalized = _normalize_text(prompt)
            if ":" in normalized:
                tail = prompt.split(":", maxsplit=1)[1]
                parts = re.split(r",|\bog\b|\band\b|\bet\b|\by\b", tail, flags=re.IGNORECASE)
                names = [part.strip().strip("\"'. ") for part in parts if part.strip().strip("\"'. ")]
        if not names:
            names = [name]

        created: list[str] = []
        for department_name in names:
            await client.post("/department", {"name": department_name})
            created.append(department_name)
        return {"action": "create_department", "count": len(created), "names": created}

    if _is_product_intent(prompt_n):
        payload = _extract_product_payload(prompt)
        # Speed/robustness: avoid extra lookup calls for VAT type in product flow.
        # In many datasets default VAT setup is sufficient, while vatType lookup can add latency
        # and occasionally pick invalid codes for the company setup.
        payload.pop("_vat_rate_hint", None)

        try:
            created_product = await client.post("/product", payload)
        except RuntimeError:
            fallback_payload = {"name": payload["name"], "isInactive": False}
            if payload.get("number"):
                fallback_payload["number"] = payload["number"]
            created_product = await client.post("/product", fallback_payload)
            payload = fallback_payload

        return {"action": "create_product", "productId": _extract_value_id(created_product), "payload": payload}

    if _is_employee_intent(prompt_n):
        first_name, _, last_name = name.partition(" ")
        birth_date = _extract_birth_date(prompt)
        start_date = _extract_start_date(prompt)
        wants_admin = _is_admin_request(prompt_n)
        payload, variant, employee_id = await _create_employee_with_retry(
            client,
            first_name,
            last_name,
            email,
            birth_date,
            wants_admin,
        )

        employment_created = False
        if employee_id and start_date:
            try:
                await client.post("/employee/employment", {"employee": {"id": employee_id}, "startDate": start_date, "isMainEmployer": True})
                employment_created = True
            except Exception:
                logger.warning("employee_employment_create_failed employee_id=%s start_date=%s", employee_id, start_date)

        entitlement_granted = False
        if employee_id and wants_admin:
            try:
                await client.put(
                    "/employee/entitlement/:grantEntitlementsByTemplate",
                    params={"employeeId": employee_id, "template": "ALL_PRIVILEGES"},
                )
                entitlement_granted = True
            except Exception:
                logger.warning("employee_admin_entitlement_failed employee_id=%s", employee_id)

        return {
            "action": "create_employee",
            "variant": variant,
            "employeeId": employee_id,
            "payload": payload,
            "birthDate": birth_date,
            "startDate": start_date,
            "employmentCreated": employment_created,
            "adminRequested": wants_admin,
            "adminGranted": entitlement_granted,
        }

    if _is_customer_intent(prompt_n):
        customer_name = _extract_customer_name(prompt) or name
        payload: dict[str, Any] = {"name": customer_name, "isCustomer": True}
        if email:
            payload["email"] = email
        org_no = _extract_org_number(prompt)
        if org_no:
            payload["organizationNumber"] = org_no
        created_customer = await client.post("/customer", payload)
        return {"action": "create_customer", "customerId": _extract_value_id(created_customer), "payload": payload}

    return {"action": "no_op", "reason": "unclassified_prompt"}


