from __future__ import annotations

from datetime import date
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_all_amounts
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import find_employee_id, pick_first_value_id


class SalaryTransactionWorkflow(Workflow):
    name = "salary_transaction"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "salary_transaction"

    def allowed_endpoints(self) -> set[str]:
        return {"/employee", "/salary/type", "/salary/transaction"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="find_employee", method="GET", endpoint="/employee"),
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
        base_type = int(values[0]["id"])
        bonus_type = int(values[1]["id"]) if len(values) > 1 else base_type

        amounts = extract_all_amounts(task_spec.prompt)
        base_amount = amounts[0] if amounts else 0.0
        bonus_amount = amounts[1] if len(amounts) > 1 else 0.0
        today = date.today()

        specs = [{"employee": {"id": employee_id}, "salaryType": {"id": base_type}, "description": "Base salary", "count": 1, "rate": base_amount}]
        if bonus_amount > 0:
            specs.append({"employee": {"id": employee_id}, "salaryType": {"id": bonus_type}, "description": "Bonus", "count": 1, "rate": bonus_amount})

        payload = {
            "date": today.isoformat(),
            "year": today.year,
            "month": today.month,
            "payslips": [{"employee": {"id": employee_id}, "year": today.year, "month": today.month, "specifications": specs}],
        }
        try:
            created = await client.post("/salary/transaction", payload)
        except RuntimeError:
            fallback_specs = [{"employee": {"id": employee_id}, "salaryType": {"id": base_type}, "description": "Base salary", "amount": base_amount}]
            if bonus_amount > 0:
                fallback_specs.append({"employee": {"id": employee_id}, "salaryType": {"id": bonus_type}, "description": "Bonus", "amount": bonus_amount})
            fallback_payload = {
                "date": today.isoformat(),
                "year": today.year,
                "month": today.month,
                "payslips": [{"employee": {"id": employee_id}, "year": today.year, "month": today.month, "specifications": fallback_specs}],
            }
            created = await client.post("/salary/transaction", fallback_payload)

        return {"action": "salary_transaction", "transactionId": pick_first_value_id(created), "employeeId": employee_id}

