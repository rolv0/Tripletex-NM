from __future__ import annotations

import base64
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_customer_name, extract_email, extract_org_number
from tripletex import TripletexClient
from tripletex.schemas import require_fields
from utils.text import normalize_text
from workflows.base import Workflow
from workflows.common import find_supplier, pick_first_value_id, today_iso


class RegisterIncomingInvoiceWorkflow(Workflow):
    name = "register_incoming_invoice"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == self.name

    def allowed_endpoints(self) -> set[str]:
        return {
            "/supplier",
            "/incomingInvoice",
            "/ledger/account",
            "/ledger/vatType",
            "/ledger/voucherType",
            "/ledger/voucher",
        }

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/invoice/paymentType", "/timesheet/entry"],
            steps=[
                PlanStep(op="ensure_supplier", method="POST", endpoint="/supplier"),
                PlanStep(op="resolve_expense_account", method="GET", endpoint="/ledger/account"),
                PlanStep(op="resolve_input_vat", method="GET", endpoint="/ledger/vatType"),
                PlanStep(op="resolve_voucher_type", method="GET", endpoint="/ledger/voucherType"),
                PlanStep(op="create_incoming_invoice", method="POST", endpoint="/incomingInvoice"),
                PlanStep(op="attach_document", method="POST", endpoint="/ledger/voucher/{id}/attachment"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        extracted = task_spec.extracted or {}
        supplier_name = str(extracted.get("customerName") or extract_customer_name(task_spec.prompt) or "Supplier").strip()
        org_no = str(extracted.get("organizationNumber") or extract_org_number(task_spec.prompt) or "").strip() or None
        email = str(extracted.get("email") or extract_email(task_spec.prompt) or "").strip() or None

        supplier = await find_supplier(client, supplier_name, org_no)
        if supplier:
            supplier_id = int(supplier["id"])
        else:
            supplier_payload: dict[str, Any] = {"name": supplier_name, "isSupplier": True}
            if org_no:
                supplier_payload["organizationNumber"] = org_no
            if email:
                supplier_payload["email"] = email
                supplier_payload["invoiceEmail"] = email
            require_fields(supplier_payload, {"name", "isSupplier"}, self.name)
            created_supplier = await client.post("/supplier", supplier_payload)
            supplier_id = pick_first_value_id(created_supplier)
            if supplier_id is None:
                return {"action": self.name, "status": "supplier_create_failed"}

        description = str(extracted.get("invoiceDescription") or "Supplier invoice").strip() or "Supplier invoice"
        invoice_amount = extracted.get("invoiceAmount")
        if invoice_amount is None:
            amounts = extracted.get("amounts") or []
            invoice_amount = max(amounts) if amounts else None
        if invoice_amount is None:
            return {"action": self.name, "status": "invoice_amount_missing", "supplierId": supplier_id}
        invoice_amount = float(invoice_amount)

        account_id = await self._resolve_expense_account(client, task_spec.prompt, description)
        vat_type_id = await self._resolve_input_vat_type(client)
        voucher_type_id = await self._resolve_voucher_type(client)
        if account_id is None or vat_type_id is None or voucher_type_id is None:
            return {
                "action": self.name,
                "status": "lookup_failed",
                "supplierId": supplier_id,
                "accountId": account_id,
                "vatTypeId": vat_type_id,
                "voucherTypeId": voucher_type_id,
            }

        invoice_date = str(extracted.get("invoiceDate") or today_iso())
        due_date = str(extracted.get("dueDate") or invoice_date)
        invoice_number = str(extracted.get("invoiceNumber") or "SUP-INV").strip()

        payload = {
            "invoiceHeader": {
                "vendorId": supplier_id,
                "invoiceDate": invoice_date,
                "dueDate": due_date,
                "invoiceAmount": invoice_amount,
                "description": description,
                "invoiceNumber": invoice_number,
                "voucherTypeId": voucher_type_id,
            },
            "orderLines": [
                {
                    "row": 1,
                    "description": description,
                    "accountId": account_id,
                    "count": 1,
                    "amountInclVat": invoice_amount,
                    "vatTypeId": vat_type_id,
                    "vendorId": supplier_id,
                }
            ],
        }

        created = await client.post("/incomingInvoice", payload)
        value = created.get("value") or {}
        voucher_id = value.get("voucherId")

        attachment_uploaded = False
        if voucher_id and task_spec.attachments:
            attachment = task_spec.attachments[0]
            raw = base64.b64decode(attachment.get("content_base64", "") or "")
            if raw:
                await client.post_file(
                    f"/ledger/voucher/{voucher_id}/attachment",
                    filename=attachment.get("filename") or "attachment.pdf",
                    content=raw,
                    mime_type=attachment.get("mime_type") or "application/octet-stream",
                )
                attachment_uploaded = True

        return {
            "action": self.name,
            "supplierId": supplier_id,
            "voucherId": int(voucher_id) if voucher_id is not None else None,
            "accountId": account_id,
            "vatTypeId": vat_type_id,
            "voucherTypeId": voucher_type_id,
            "attachmentUploaded": attachment_uploaded,
        }

    async def _resolve_expense_account(self, client: TripletexClient, prompt: str, description: str) -> int | None:
        normalized = normalize_text(f"{prompt} {description}")
        keyword_map: list[tuple[tuple[str, ...], list[str]]] = [
            (("travel", "reise", "viagem", "conference", "konferanse", "hotel", "taxi"), ["7140", "7150"]),
            (("software", "system", "web", "design", "development", "hosting", "cloud", "license", "licence", "programvare"), ["6790", "6550", "6540"]),
            (("marketing", "advertising", "reklame"), ["7320", "7330"]),
        ]
        candidate_numbers = ["6790", "6550", "6540", "6800", "7140", "7320"]
        for keywords, numbers in keyword_map:
            if any(word in normalized for word in keywords):
                candidate_numbers = numbers + [n for n in candidate_numbers if n not in numbers]
                break

        for number in candidate_numbers:
            response = await client.get(
                "/ledger/account",
                params={"number": number, "count": 5, "fields": "id,number,name,isInactive"},
            )
            for value in response.get("values", []):
                if not value.get("isInactive"):
                    return int(value["id"])
        return None

    async def _resolve_input_vat_type(self, client: TripletexClient) -> int | None:
        response = await client.get(
            "/ledger/vatType",
            params={"count": 100, "fields": "id,name,number,displayName,percentage,deductionPercentage"},
        )
        values = response.get("values", [])
        ranked: list[tuple[int, int]] = []
        for value in values:
            text = normalize_text(
                f"{value.get('name', '')} {value.get('displayName', '')} {value.get('number', '')}"
            )
            percentage = float(value.get("percentage") or 0)
            score = 0
            if abs(percentage - 25.0) < 0.2:
                score += 4
            if any(token in text for token in ("inngaende", "fradrag", "input", "deduct")):
                score += 4
            if any(token in text for token in ("utgaende", "output", "sales")):
                score -= 4
            if score > 0 and value.get("id") is not None:
                ranked.append((score, int(value["id"])))
        if ranked:
            ranked.sort(reverse=True)
            return ranked[0][1]
        for value in values:
            if value.get("id") is not None:
                return int(value["id"])
        return None

    async def _resolve_voucher_type(self, client: TripletexClient) -> int | None:
        response = await client.get(
            "/ledger/voucherType",
            params={"count": 50, "fields": "id,name,displayName"},
        )
        values = response.get("values", [])
        ranked: list[tuple[int, int]] = []
        for value in values:
            text = normalize_text(f"{value.get('name', '')} {value.get('displayName', '')}")
            score = 0
            if any(token in text for token in ("supplier", "purchase", "incoming", "leverandor")):
                score += 3
            if value.get("id") is not None:
                ranked.append((score, int(value["id"])))
        if ranked:
            ranked.sort(reverse=True)
            return ranked[0][1]
        return None
