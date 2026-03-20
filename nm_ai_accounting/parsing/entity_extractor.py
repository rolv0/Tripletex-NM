from __future__ import annotations

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

    patterns = [
        rf"(?:kunde|customer|cliente|client)\s+([A-Za-z0-9 .&\-]+?\b{company_suffixes}\b)",
        rf"(?:el cliente|le client|o cliente|der kunde)\s+([A-Za-z0-9 .&\-]+?\b{company_suffixes}\b)",
        rf"(?:for|para|pour|fur)\s+([A-Za-z0-9 .&\-]+?\b{company_suffixes}\b)",
        rf"\b([A-Za-z0-9 .&\-]+?\b{company_suffixes}\b)\b",
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
    match = re.search(r"(?:prosjekt|project|proyecto|projet|projekt)\s+[\"']?([^\"'\n,]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_amount(text: str) -> float | None:
    values = extract_all_amounts(text)
    return values[0] if values else None


def extract_all_entities(prompt: str, attachment_texts: list[str]) -> dict[str, Any]:
    merged = "\n".join([prompt, *attachment_texts])
    return {
        "email": extract_email(merged),
        "organizationNumber": extract_org_number(merged),
        "quotedItems": extract_quoted_items(merged),
        "customerName": extract_customer_name(merged),
        "personName": extract_person_name(merged),
        "projectName": extract_project_name(merged),
        "amounts": extract_all_amounts(merged),
    }
