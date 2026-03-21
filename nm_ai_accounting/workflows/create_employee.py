from __future__ import annotations

from datetime import date
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_email, extract_person_name
from tripletex import TripletexClient
from tripletex.schemas import require_fields
from workflows.base import Workflow
from workflows.common import ensure_department, ensure_employee_employment, pick_first_value_id


class CreateEmployeeWorkflow(Workflow):
    name = "create_employee"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_employee"

    def allowed_endpoints(self) -> set[str]:
        return {"/employee", "/department", "/employee/employment", "/employee/employment/details"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="ensure_department", method="GET", endpoint="/department"),
                PlanStep(op="find_employee", method="GET", endpoint="/employee"),
                PlanStep(op="create_employee", method="POST", endpoint="/employee"),
                PlanStep(op="ensure_employment", method="POST", endpoint="/employee/employment"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        extracted = task_spec.extracted or {}
        email = str(extracted.get("email") or extract_email(prompt) or "").strip() or None
        department_name = str(extracted.get("departmentName") or "").strip() or None
        department_id = await ensure_department(client, department_name)

        employee_id: int | None = None
        existing_employee = False
        if email:
            existing = await client.get("/employee", params={"email": email, "count": 10, "fields": "id,firstName,lastName,email"})
            values = existing.get("values", [])
            if values:
                employee_id = int(values[0]["id"])
                existing_employee = True

        if employee_id is None:
            full_name = str(extracted.get("personName") or extract_person_name(prompt) or "Auto Employee").strip()
            parts = full_name.split()
            first_name = parts[0] if parts else "Auto"
            last_name = " ".join(parts[1:]) if len(parts) > 1 else "User"

            payload: dict[str, Any] = {"firstName": first_name, "lastName": last_name}
            if email:
                payload["email"] = email
            if department_id is not None:
                payload["department"] = {"id": department_id}
            require_fields(payload, {"firstName", "lastName"}, "create_employee")
            created = await client.post("/employee", payload)
            employee_id = pick_first_value_id(created)
        if employee_id is None:
            return {"action": "create_employee", "status": "employee_create_failed"}

        start_date_raw = str(extracted.get("startDate") or "").strip()
        try:
            effective_date = date.fromisoformat(start_date_raw[:10]) if start_date_raw else date.today().replace(day=1)
        except ValueError:
            effective_date = date.today().replace(day=1)

        annual_salary = extracted.get("annualSalary")
        monthly_salary = extracted.get("monthlySalary")
        full_time_percentage = extracted.get("fullTimePercentage")
        employment_id = await ensure_employee_employment(
            client,
            employee_id=employee_id,
            effective_date=effective_date,
            base_salary_amount=float(annual_salary) if annual_salary else None,
            monthly_salary_amount=float(monthly_salary) if monthly_salary else None,
            percentage_of_full_time_equivalent=float(full_time_percentage) if full_time_percentage else None,
        )

        return {
            "action": "create_employee",
            "employeeId": employee_id,
            "departmentId": department_id,
            "employmentId": employment_id,
            "existing": existing_employee,
        }
