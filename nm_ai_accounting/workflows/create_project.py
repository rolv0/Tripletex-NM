from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_project_name
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import ensure_customer, find_or_create_project


class CreateProjectWorkflow(Workflow):
    name = "create_project"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_project"

    def allowed_endpoints(self) -> set[str]:
        return {"/customer", "/project", "/employee"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice"],
            steps=[
                PlanStep(op="ensure_customer", method="GET", endpoint="/customer"),
                PlanStep(op="create_or_find_project", method="POST", endpoint="/project"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        customer_id = await ensure_customer(client, task_spec.prompt)
        project_id = await find_or_create_project(client, task_spec.prompt, customer_id)
        return {
            "action": "create_project",
            "projectId": project_id,
            "customerId": customer_id,
            "projectName": extract_project_name(task_spec.prompt),
        }

