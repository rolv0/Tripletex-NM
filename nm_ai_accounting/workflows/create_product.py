from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_quoted_items
from utils.currency import extract_all_amounts
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import pick_first_value_id


class CreateProductWorkflow(Workflow):
    name = "create_product"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_product"

    def allowed_endpoints(self) -> set[str]:
        return {"/product"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[PlanStep(op="create_product", method="POST", endpoint="/product")],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        quoted = extract_quoted_items(prompt)
        name = quoted[0] if quoted else "Product"
        number = None
        for item in quoted[1:]:
            if item.isdigit():
                number = item
                break
        amounts = extract_all_amounts(prompt)
        payload: dict[str, Any] = {"name": name, "isInactive": False}
        if number:
            payload["number"] = number
        if amounts:
            payload["priceExcludingVatCurrency"] = amounts[0]
        created = await client.post("/product", payload)
        return {"action": "create_product", "productId": pick_first_value_id(created), "payload": payload}

