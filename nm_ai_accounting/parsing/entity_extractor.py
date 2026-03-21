from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from utils.currency import extract_all_amounts


def extract_email(text: str) -> str | None:
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_org_number(text: str) -> str | None:
    match = re.search(
        r"(?:org(?:\.|anization)?\s*(?:nr|number|numero|no))\s*[:#]?\s*(\d{9})",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    loose = re.search(r"\b(\d{9})\b", text)
    return loose.group(1) if loose else None


def extract_quoted_items(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"[\"']([^\"']+)[\"']", text)]


def extract_customer_name(text: str) -> str | None:
    company_suffixes = r"(?:AS|SL|Lda|SARL|GmbH|SA|SAS|Ltd|LLC|Inc)"
    name_chars = r"A-Za-zÀ-ÖØ-öø-ÿ0-9 .&\-"

    patterns = [
        rf"(?:kunde|customer|cliente|client)\s+([{name_chars}]+?\b{company_suffixes}\b)",
        rf"(?:el cliente|le client|o cliente|der kunde)\s+([{name_chars}]+?\b{company_suffixes}\b)",
        rf"(?:for|para|pour|fur)\s+([{name_chars}]+?\b{company_suffixes}\b)",
        rf"\b([{name_chars}]+?\b{company_suffixes}\b)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue

        name = match.group(1).strip().strip(",.")
        name = re.sub(r"^(?:el cliente|le client|o cliente|der kunde)\s+", "", name, flags=re.IGNORECASE).strip()
        name = re.sub(r"\s+", " ", name)

        if len(name) >= 3:
            return name

    quoted = extract_quoted_items(text)
    for value in quoted:
        if re.search(company_suffixes, value, re.IGNORECASE):
            return value.strip()

    return None


def extract_person_name(text: str) -> str | None:
    email = extract_email(text)
    if email:
        local = email.split("@")[0].replace(".", " ").replace("_", " ").strip()
        if local:
            return " ".join(part.capitalize() for part in local.split()[:2])

    match = re.search(
        r"(?:named|navn|name|employee|ansatt|empleado|mitarbeiter|salarie|de)\s+([A-Z][A-Za-z\-']+\s+[A-Z][A-Za-z\-']+)",
        text,
    )
    if match:
        return match.group(1).strip()

    return None


def extract_project_name(text: str) -> str | None:
    quoted = extract_quoted_items(text)
    if quoted:
        company_suffixes = r"(?:AS|SL|Lda|SARL|GmbH|SA|SAS|Ltd|LLC|Inc)"
        for value in quoted:
            if not re.search(company_suffixes, value, re.IGNORECASE):
                return value.strip()

    match = re.search(r"(?:prosjekt|project|proyecto|projet|projekt)\s+[\"']?([^\"'\n,:()]+)", text, re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
        candidate = re.sub(r"^(?:hat|has|har|tiene|tem)\b", "", candidate, flags=re.IGNORECASE).strip()
        if candidate:
            return candidate
    return None


def extract_department_name(text: str) -> str | None:
    quoted = extract_quoted_items(text)
    dept_patterns = ("department", "departement", "departamento", "abteilung", "avdeling")
    for value in quoted:
        lowered = value.lower()
        if not any(token in lowered for token in dept_patterns):
            continue
        cleaned = re.sub(r"^(?:department|departement|departamento|abteilung|avdeling)\s+", "", value, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned

    match = re.search(
        r"(?:department|departement|departamento|abteilung|avdeling)\s*[:\-]?\s*([A-ZÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9 &/\-]{2,80})",
        text,
        re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip().strip(",.")
        value = re.split(r"\b(?:start|salary|email|phone|birth|adresse|address)\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if value:
            return value
    return None


def extract_activity_name(text: str) -> str | None:
    match = re.search(r"(?:activity|aktivitet|actividad|activite|atividade)\s+[\"']?([^\"'\n,]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    quoted = extract_quoted_items(text)
    if len(quoted) >= 2:
        return quoted[1].strip()
    return None


def extract_hours(text: str) -> float | None:
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:hours|hour|timer|time|timar|stunden|heures|horas)\b", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def extract_hourly_rate(text: str) -> float | None:
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*NOK\s*/?\s*h\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"(?:hourly rate|timesats|stundensatz|taux horaire)\s*[:=]?\s*(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def extract_start_date(text: str) -> str | None:
    patterns = [
        r"(?:start date|startdatum|date de debut|startdato|data de inicio)\s*[:#]?\s*([0-9./-]{8,10})",
        r"(?:starting|debut|fra og med|from)\s*([0-9./-]{8,10})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = _parse_date_token(match.group(1))
            if parsed:
                return parsed
    return None


def extract_annual_salary(text: str) -> float | None:
    patterns = [
        r"(?:annual salary|arslonn|yearly salary|salaire annuel|jahresgehalt)\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
        r"(?:salary|salaire|gehalt)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*NOK\s*(?:per year|annuel|annual)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def extract_monthly_salary(text: str) -> float | None:
    patterns = [
        r"(?:monthly salary|manedslonn|maanedslonn|salaire mensuel|monatsgehalt)\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
        r"(?:base salary|grunnlonn)\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def extract_full_time_percentage(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def extract_amount(text: str) -> float | None:
    values = extract_all_amounts(text)
    return values[0] if values else None


def extract_invoice_number(text: str) -> str | None:
    match = re.search(
        r"(?:invoice|faktura|factura|fatura|rechnung)\s*(?:nr|number|no|n[oº°]?|numero)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _parse_date_token(raw: str) -> str | None:
    value = raw.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def extract_invoice_date(text: str) -> str | None:
    patterns = [
        r"(?:invoice date|fakturadato|data da fatura|date de facture|rechnungsdatum)\s*[:#]?\s*([0-9./-]{8,10})",
        r"(?:dated|dato)\s*[:#]?\s*([0-9./-]{8,10})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = _parse_date_token(match.group(1))
            if parsed:
                return parsed
    return None


def extract_due_date(text: str) -> str | None:
    patterns = [
        r"(?:due date|forfallsdato|vencimento|date d[' ]echeance|falligkeit)\s*[:#]?\s*([0-9./-]{8,10})",
        r"(?:due)\s*[:#]?\s*([0-9./-]{8,10})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            parsed = _parse_date_token(match.group(1))
            if parsed:
                return parsed
    return None


def extract_invoice_description(text: str) -> str | None:
    quoted = extract_quoted_items(text)
    company_suffixes = r"(?:AS|SL|Lda|SARL|GmbH|SA|SAS|Ltd|LLC|Inc)"
    for value in quoted:
        if not re.search(company_suffixes, value, re.IGNORECASE):
            return value.strip()
    return None


def extract_invoice_total(text: str) -> float | None:
    values = extract_all_amounts(text)
    if not values:
        return None

    keywords = (
        "total",
        "belop",
        "sum",
        "to pay",
        "amount due",
        "inkl",
        "incl",
        "iva",
        "mva",
        "mwst",
    )
    best_value: float | None = None
    best_score = -1
    for line in text.splitlines():
        line_values = extract_all_amounts(line)
        if not line_values:
            continue
        normalized = line.lower()
        score = sum(1 for keyword in keywords if keyword in normalized)
        if score > best_score:
            best_score = score
            best_value = max(line_values)

    if best_value is not None:
        return best_value
    return max(values)


def extract_all_entities(prompt: str, attachment_texts: list[str]) -> dict[str, Any]:
    merged = "\n".join([prompt, *attachment_texts])
    return {
        "email": extract_email(merged),
        "organizationNumber": extract_org_number(merged),
        "quotedItems": extract_quoted_items(merged),
        "customerName": extract_customer_name(merged),
        "personName": extract_person_name(merged),
        "departmentName": extract_department_name(merged),
        "projectName": extract_project_name(merged),
        "activityName": extract_activity_name(merged),
        "hours": extract_hours(merged),
        "hourlyRate": extract_hourly_rate(merged),
        "startDate": extract_start_date(merged),
        "annualSalary": extract_annual_salary(merged),
        "monthlySalary": extract_monthly_salary(merged),
        "fullTimePercentage": extract_full_time_percentage(merged),
        "invoiceNumber": extract_invoice_number(merged),
        "invoiceDate": extract_invoice_date(merged),
        "dueDate": extract_due_date(merged),
        "invoiceDescription": extract_invoice_description(merged),
        "invoiceAmount": extract_invoice_total(merged),
        "amounts": extract_all_amounts(merged),
    }
