from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_email, extract_person_name
from tripletex import TripletexClient
from tripletex.schemas import require_fields
from workflows.base import Workflow
from workflows.common import pick_first_value_id


class CreateEmployeeWorkflow(Workflow):
    name = "create_employee"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_employee"

    def allowed_endpoints(self) -> set[str]:
        return {"/employee"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="find_employee", method="GET", endpoint="/employee"),
                PlanStep(op="create_employee", method="POST", endpoint="/employee"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        email = extract_email(prompt)
        if email:
            existing = await client.get("/employee", params={"email": email, "count": 10, "fields": "id,firstName,lastName,email"})
            values = existing.get("values", [])
            if values:
                return {"action": "create_employee", "employeeId": int(values[0]["id"]), "existing": True}

        full_name = extract_person_name(prompt) or "Auto Employee"
        parts = full_name.split()
        first_name = parts[0] if parts else "Auto"
        last_name = " ".join(parts[1:]) if len(parts) > 1 else "User"

        payload: dict[str, Any] = {"firstName": first_name, "lastName": last_name}
        if email:
            payload["email"] = email
        require_fields(payload, {"firstName", "lastName"}, "create_employee")
        created = await client.post("/employee", payload)
        return {"action": "create_employee", "employeeId": pick_first_value_id(created), "existing": False}

