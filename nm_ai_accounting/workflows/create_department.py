from __future__ import annotations

import re
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_quoted_items
from tripletex import TripletexClient
from workflows.base import Workflow


class CreateDepartmentWorkflow(Workflow):
    name = "create_department"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_department"

    def allowed_endpoints(self) -> set[str]:
        return {"/department"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[PlanStep(op="create_departments", method="POST", endpoint="/department")],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        names = extract_quoted_items(prompt)
        if not names and ":" in prompt:
            tail = prompt.split(":", maxsplit=1)[1]
            parts = re.split(r",|\bog\b|\band\b|\bet\b|\by\b", tail, flags=re.IGNORECASE)
            names = [part.strip().strip("\"'. ") for part in parts if part.strip().strip("\"'. ")]
        if not names:
            names = ["Department"]

        created: list[str] = []
        for department_name in names:
            await client.post("/department", {"name": department_name})
            created.append(department_name)
        return {"action": "create_department", "count": len(created), "names": created}

