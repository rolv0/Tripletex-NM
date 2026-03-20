from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_customer_name, extract_email, extract_org_number
from tripletex import TripletexClient
from tripletex.schemas import require_fields
from workflows.base import Workflow
from workflows.common import find_customer, pick_first_value_id


class CreateCustomerWorkflow(Workflow):
    name = "create_customer"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_customer"

    def allowed_endpoints(self) -> set[str]:
        return {"/customer"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher"],
            steps=[
                PlanStep(op="find_customer", method="GET", endpoint="/customer"),
                PlanStep(op="create_customer", method="POST", endpoint="/customer"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        customer_name = extract_customer_name(prompt) or "Customer"
        org_no = extract_org_number(prompt)
        existing = await find_customer(client, customer_name, org_no)
        if existing:
            return {"action": "create_customer", "customerId": int(existing["id"]), "existing": True}

        payload: dict[str, Any] = {"name": customer_name, "isCustomer": True}
        email = extract_email(prompt)
        if email:
            payload["email"] = email
        if org_no:
            payload["organizationNumber"] = org_no
        require_fields(payload, {"name", "isCustomer"}, "create_customer")
        created = await client.post("/customer", payload)
        return {"action": "create_customer", "customerId": pick_first_value_id(created), "existing": False}

