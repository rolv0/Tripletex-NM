from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import ensure_customer, parse_order_lines, pick_first_value_id, today_iso


class CreateInvoiceWorkflow(Workflow):
    name = "create_invoice"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_invoice"

    def allowed_endpoints(self) -> set[str]:
        return {"/customer", "/order", "/invoice"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher"],
            steps=[
                PlanStep(op="ensure_customer", method="GET", endpoint="/customer"),
                PlanStep(op="create_order", method="POST", endpoint="/order"),
                PlanStep(op="create_invoice_from_order", method="PUT", endpoint="/order/{id}/:invoice"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        customer_id = await ensure_customer(client, task_spec.prompt)
        if customer_id is None:
            return {"action": "create_invoice", "status": "customer_missing"}

        order_payload = {
            "customer": {"id": customer_id},
            "orderDate": today_iso(),
            "deliveryDate": today_iso(),
            "orderLines": parse_order_lines(task_spec.prompt),
        }
        order_created = await client.post("/order", order_payload)
        order_id = pick_first_value_id(order_created)
        if order_id is None:
            return {"action": "create_invoice", "status": "order_create_failed"}

        try:
            invoice = await client.put(
                f"/order/{order_id}/:invoice",
                params={"invoiceDate": today_iso(), "sendToCustomer": False, "sendType": "MANUAL"},
            )
            invoice_id = pick_first_value_id(invoice)
        except RuntimeError as exc:
            text = str(exc).lower()
            if "bankkontonummer" in text or "bankkonto" in text:
                return {"action": "create_invoice", "status": "blocked_missing_bank_account", "orderId": order_id}
            invoice = await client.post(
                "/invoice",
                {
                    "customer": {"id": customer_id},
                    "invoiceDate": today_iso(),
                    "invoiceDueDate": today_iso(),
                    "orders": [{"id": order_id}],
                },
            )
            invoice_id = pick_first_value_id(invoice)

        return {"action": "create_invoice", "invoiceId": invoice_id, "orderId": order_id, "customerId": customer_id}

