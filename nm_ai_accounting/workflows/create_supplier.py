from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_customer_name, extract_email, extract_org_number
from tripletex import TripletexClient
from tripletex.schemas import require_fields
from workflows.base import Workflow
from workflows.common import find_supplier, pick_first_value_id


class CreateSupplierWorkflow(Workflow):
    name = "create_supplier"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_supplier"

    def allowed_endpoints(self) -> set[str]:
        return {"/supplier"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="find_supplier", method="GET", endpoint="/supplier"),
                PlanStep(op="create_supplier", method="POST", endpoint="/supplier"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        supplier_name = extract_customer_name(prompt) or "Supplier"
        org_no = extract_org_number(prompt)
        existing = await find_supplier(client, supplier_name, org_no)
        if existing:
            return {"action": "create_supplier", "supplierId": int(existing["id"]), "existing": True}

        payload: dict[str, Any] = {"name": supplier_name, "isSupplier": True}
        email = extract_email(prompt)
        if email:
            payload["email"] = email
            payload["invoiceEmail"] = email
        if org_no:
            payload["organizationNumber"] = org_no

        require_fields(payload, {"name", "isSupplier"}, "create_supplier")
        created = await client.post("/supplier", payload)
        return {"action": "create_supplier", "supplierId": pick_first_value_id(created), "existing": False}

