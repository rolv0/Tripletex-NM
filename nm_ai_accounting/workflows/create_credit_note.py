from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_amount, extract_customer_name, extract_org_number
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import find_customer, invoice_lookup_range


class CreateCreditNoteWorkflow(Workflow):
    name = "create_credit_note"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_credit_note"

    def allowed_endpoints(self) -> set[str]:
        return {"/customer", "/invoice"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/order"],
            steps=[
                PlanStep(op="find_customer", method="GET", endpoint="/customer"),
                PlanStep(op="find_invoice", method="GET", endpoint="/invoice"),
                PlanStep(op="create_credit_note", method="PUT", endpoint="/invoice/{id}/:createCreditNote"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        customer_name = extract_customer_name(prompt) or ""
        org_no = extract_org_number(prompt)
        customer_id: int | None = None
        if customer_name:
            customer = await find_customer(client, customer_name, org_no)
            if customer:
                customer_id = int(customer["id"])

        date_from, date_to = invoice_lookup_range()
        params: dict[str, Any] = {
            "invoiceDateFrom": date_from,
            "invoiceDateTo": date_to,
            "count": 100,
            "sorting": "-invoiceDate",
            "fields": "id,invoiceDate,invoiceNumber,amountExcludingVat,customer,comment,reference",
        }
        if customer_id is not None:
            params["customerId"] = str(customer_id)

        invoices = await client.get("/invoice", params=params)
        values = invoices.get("values", [])
        if not values:
            return {"action": "create_credit_note", "status": "no_invoice_found"}

        target_amount = extract_amount(prompt)
        chosen: dict[str, Any] = values[0]
        if target_amount is not None:
            for invoice in values:
                amount = float(invoice.get("amountExcludingVat") or 0)
                if abs(amount - target_amount) < 1.0:
                    chosen = invoice
                    break

        invoice_id = int(chosen["id"])
        await client.put(f"/invoice/{invoice_id}/:createCreditNote")
        return {"action": "create_credit_note", "invoiceId": invoice_id}
