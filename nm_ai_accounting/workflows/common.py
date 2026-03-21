from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from parsing.entity_extractor import (
    extract_activity_name,
    extract_all_amounts,
    extract_customer_name,
    extract_email,
    extract_hours,
    extract_hourly_rate,
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


async def find_department(client: TripletexClient, name: str) -> dict[str, Any] | None:
    response = await client.get("/department", params={"name": name, "count": 10, "fields": "id,name,displayName"})
    values = response.get("values", [])
    target = name.strip().lower()
    for value in values:
        candidate = str(value.get("displayName") or value.get("name") or "").strip().lower()
        if candidate == target:
            return value
    return values[0] if values else None


async def ensure_department(client: TripletexClient, name: str | None) -> int | None:
    if not name:
        return None
    found = await find_department(client, name)
    if found:
        return int(found["id"])
    created = await client.post("/department", {"name": name})
    return pick_first_value_id(created)


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


async def find_or_create_project(
    client: TripletexClient,
    prompt: str,
    customer_id: int | None,
    project_manager_id: int | None = None,
) -> int | None:
    project_name = extract_project_name(prompt) or "Project"
    existing = await client.get("/project", params={"name": project_name, "count": 10, "fields": "id,name"})
    values = existing.get("values", [])
    if values:
        return int(values[0]["id"])
    payload: dict[str, Any] = {"name": project_name}
    if customer_id is not None:
        payload["customer"] = {"id": customer_id}
    if project_manager_id is not None:
        payload["projectManager"] = {"id": project_manager_id}
    created = await client.post("/project", payload)
    return pick_first_value_id(created)


async def find_or_create_activity(client: TripletexClient, prompt: str) -> int | None:
    activity_name = extract_activity_name(prompt) or "Activity"
    response = await client.get("/activity", params={"name": activity_name, "count": 10, "fields": "id,name,displayName,rate"})
    values = response.get("values", [])
    for value in values:
        candidate = str(value.get("displayName") or value.get("name") or "").strip().lower()
        if candidate == activity_name.strip().lower():
            return int(value["id"])

    payload: dict[str, Any] = {
        "name": activity_name,
        "activityType": "PROJECT_GENERAL_ACTIVITY",
        "isChargeable": True,
    }
    hourly_rate = extract_hourly_rate(prompt)
    if hourly_rate is not None:
        payload["rate"] = hourly_rate
    created = await client.post("/activity", payload)
    return pick_first_value_id(created)


def parse_hours(prompt: str) -> float | None:
    return extract_hours(prompt)


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


def _safe_iso_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _is_active_employment(employment: dict[str, Any], *, on_date: date) -> bool:
    start_date = _safe_iso_date(employment.get("startDate"))
    end_date = _safe_iso_date(employment.get("endDate"))
    if start_date and start_date > on_date:
        return False
    if end_date and end_date < on_date:
        return False
    return True


async def ensure_employee_employment(
    client: TripletexClient,
    *,
    employee_id: int,
    effective_date: date,
    base_salary_amount: float | None = None,
    monthly_salary_amount: float | None = None,
    percentage_of_full_time_equivalent: float | None = None,
) -> int | None:
    employment_response = await client.get(
        "/employee/employment",
        params={"employeeId": employee_id, "count": 10, "fields": "id,startDate,endDate"},
    )
    employments = employment_response.get("values", [])
    active_employment = next(
        (
            employment
            for employment in employments
            if isinstance(employment, dict) and _is_active_employment(employment, on_date=effective_date)
        ),
        None,
    )

    if active_employment:
        employment_id = int(active_employment["id"])
    else:
        created_employment = await client.post(
            "/employee/employment",
            {
                "employee": {"id": employee_id},
                "startDate": effective_date.isoformat(),
                "isMainEmployer": True,
            },
        )
        employment_id = pick_first_value_id(created_employment)
        if employment_id is None:
            return None

    details_response = await client.get(
        "/employee/employment/details",
        params={
            "employmentId": str(employment_id),
            "count": 10,
            "fields": "id,date,employmentType,employmentForm,remunerationType,workingHoursScheme,percentageOfFullTimeEquivalent,annualSalary,hourlyWage",
        },
    )
    detail_values = details_response.get("values", [])
    detail_exists = any(isinstance(value, dict) for value in detail_values)
    if not detail_exists:
        details_payload: dict[str, Any] = {
            "employment": {"id": employment_id},
            "date": effective_date.isoformat(),
            "employmentType": "ORDINARY",
            "employmentForm": "PERMANENT",
            "remunerationType": "MONTHLY_WAGE",
            "workingHoursScheme": "NOT_SHIFT",
            "percentageOfFullTimeEquivalent": percentage_of_full_time_equivalent or 100,
        }
        if monthly_salary_amount and monthly_salary_amount > 0:
            details_payload["monthlySalary"] = float(monthly_salary_amount)
        elif base_salary_amount and base_salary_amount > 0:
            details_payload["annualSalary"] = float(base_salary_amount) * 12
        await client.post("/employee/employment/details", details_payload)

    return employment_id
