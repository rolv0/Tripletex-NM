from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_amount, extract_customer_name, extract_org_number
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import find_customer, invoice_lookup_range, today_iso


class RegisterPaymentWorkflow(Workflow):
    name = "register_payment"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "register_payment"

    def allowed_endpoints(self) -> set[str]:
        return {"/customer", "/invoice", "/invoice/paymentType"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/order"],
            steps=[
                PlanStep(op="find_customer", method="GET", endpoint="/customer"),
                PlanStep(op="find_invoice", method="GET", endpoint="/invoice"),
                PlanStep(op="get_payment_type", method="GET", endpoint="/invoice/paymentType"),
                PlanStep(op="register_payment", method="PUT", endpoint="/invoice/{id}/:payment"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        customer_name = extract_customer_name(prompt) or ""
        org_no = extract_org_number(prompt)
        customer_id: int | None = None
        if customer_name:
            found = await find_customer(client, customer_name, org_no)
            if found:
                customer_id = int(found["id"])

        date_from, date_to = invoice_lookup_range()
        params: dict[str, Any] = {
            "invoiceDateFrom": date_from,
            "invoiceDateTo": date_to,
            "count": 50,
            "sorting": "-invoiceDate",
            "fields": "id,invoiceDate,invoiceNumber,amountExcludingVat,amountOutstanding,paidAmount,customer",
        }
        if customer_id is not None:
            params["customerId"] = str(customer_id)
        response = await client.get("/invoice", params=params)
        invoices = response.get("values", [])
        if not invoices:
            return {"action": "register_payment", "status": "no_invoice_found"}

        target_amount = extract_amount(prompt)
        chosen = invoices[0]
        if target_amount is not None:
            for inv in invoices:
                amount = float(inv.get("amountExcludingVat") or 0)
                if abs(amount - target_amount) < 1.0:
                    chosen = inv
                    break

        payment_type_resp = await client.get("/invoice/paymentType", params={"count": 10, "fields": "id,description"})
        payment_types = payment_type_resp.get("values", [])
        if not payment_types:
            return {"action": "register_payment", "status": "no_payment_type"}
        payment_type_id = int(payment_types[0]["id"])

        outstanding = float(chosen.get("amountOutstanding") or chosen.get("amountExcludingVat") or 0)
        await client.put(
            f"/invoice/{chosen['id']}/:payment",
            params={"paymentDate": today_iso(), "paymentTypeId": payment_type_id, "paidAmount": outstanding},
        )
        return {
            "action": "register_payment",
            "invoiceId": int(chosen["id"]),
            "paymentTypeId": payment_type_id,
            "paidAmount": outstanding,
        }
