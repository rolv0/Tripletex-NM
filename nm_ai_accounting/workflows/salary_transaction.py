from __future__ import annotations

from datetime import date
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_all_amounts
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import ensure_employee_employment, find_employee_id, pick_first_value_id


def _pick_salary_type_ids(values: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    base_type: int | None = None
    bonus_type: int | None = None

    for value in values:
        type_id = value.get("id")
        if type_id is None:
            continue
        name = str(value.get("name") or "").lower()
        number = str(value.get("number") or "").lower()
        haystack = f"{name} {number}"
        if base_type is None and any(token in haystack for token in ("salary", "lonn", "lønn", "fast", "base", "maaned", "måned", "monthly")):
            base_type = int(type_id)
        if bonus_type is None and any(token in haystack for token in ("bonus", "variable", "variabel", "engang")):
            bonus_type = int(type_id)

    if base_type is None and values:
        base_type = int(values[0]["id"])
    if bonus_type is None:
        bonus_type = base_type
    return base_type, bonus_type


class SalaryTransactionWorkflow(Workflow):
    name = "salary_transaction"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "salary_transaction"

    def allowed_endpoints(self) -> set[str]:
        return {
            "/employee",
            "/employee/employment",
            "/employee/employment/details",
            "/salary/type",
            "/salary/transaction",
        }

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="find_employee", method="GET", endpoint="/employee"),
                PlanStep(op="ensure_employment", method="GET", endpoint="/employee/employment"),
                PlanStep(op="find_salary_types", method="GET", endpoint="/salary/type"),
                PlanStep(op="create_salary_transaction", method="POST", endpoint="/salary/transaction"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        employee_id = await find_employee_id(client, task_spec.prompt)
        if employee_id is None:
            return {"action": "salary_transaction", "status": "employee_not_found"}

        salary_types = await client.get("/salary/type", params={"count": 10, "fields": "id,name,number"})
        values = salary_types.get("values", [])
        if not values:
            return {"action": "salary_transaction", "status": "salary_type_not_found"}
        base_type, bonus_type = _pick_salary_type_ids(values)
        if base_type is None:
            return {"action": "salary_transaction", "status": "salary_type_not_found"}

        amounts = extract_all_amounts(task_spec.prompt)
        base_amount = amounts[0] if amounts else 0.0
        bonus_amount = amounts[1] if len(amounts) > 1 else 0.0
        today = date.today()
        effective_date = date(today.year, today.month, 1)

        employment_id = await ensure_employee_employment(
            client,
            employee_id=employee_id,
            effective_date=effective_date,
            base_salary_amount=base_amount,
        )
        if employment_id is None:
            return {"action": "salary_transaction", "status": "employment_setup_failed", "employeeId": employee_id}

        specs = [
            {
                "employee": {"id": employee_id},
                "salaryType": {"id": base_type},
                "description": "Base salary",
                "count": 1,
                "rate": base_amount,
            }
        ]
        if bonus_amount > 0:
            specs.append(
                {
                    "employee": {"id": employee_id},
                    "salaryType": {"id": bonus_type},
                    "description": "Bonus",
                    "count": 1,
                    "rate": bonus_amount,
                }
            )

        payload = {
            "date": today.isoformat(),
            "year": today.year,
            "month": today.month,
            "payslips": [{"employee": {"id": employee_id}, "year": today.year, "month": today.month, "specifications": specs}],
        }
        created = await client.post("/salary/transaction", payload)

        return {
            "action": "salary_transaction",
            "transactionId": pick_first_value_id(created),
            "employeeId": employee_id,
            "employmentId": employment_id,
        }
