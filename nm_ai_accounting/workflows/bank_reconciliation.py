from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from tripletex import TripletexClient
from utils.text import normalize_text
from workflows.base import Workflow
from workflows.common import invoice_lookup_range, today_iso


def _parse_date_value(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    for sep in (".", "/", "-"):
        parts = value.split(sep)
        if len(parts) != 3:
            continue
        if len(parts[0]) == 4:
            year, month, day = parts
        else:
            day, month, year = parts
        try:
            return date(int(year), int(month), int(day)).isoformat()
        except Exception:
            continue
    return None


def _parse_amount(raw: str) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    cleaned = value.replace("NOK", "").replace("kr", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except Exception:
        return None


def _extract_reference(text: str) -> str | None:
    match = re.search(r"\b([A-Z]{0,3}\d{3,})\b", text or "", re.IGNORECASE)
    return match.group(1) if match else None


def _parse_csv_rows(attachment_texts: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for text in attachment_texts:
        stripped = text.strip()
        if not stripped:
            continue
        sample = "\n".join(stripped.splitlines()[:10])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel
            dialect.delimiter = ";" if sample.count(";") >= sample.count(",") else ","

        reader = csv.DictReader(io.StringIO(stripped), dialect=dialect)
        if reader.fieldnames:
            for raw_row in reader:
                normalized = {normalize_text(str(k or "")): (v or "").strip() for k, v in raw_row.items()}
                description = (
                    normalized.get("description")
                    or normalized.get("tekst")
                    or normalized.get("text")
                    or normalized.get("details")
                    or normalized.get("beskrivelse")
                    or normalized.get("referanse")
                    or ""
                )
                amount = (
                    _parse_amount(normalized.get("amount") or "")
                    or _parse_amount(normalized.get("belop") or "")
                    or _parse_amount(normalized.get("belop inn") or "")
                    or _parse_amount(normalized.get("value") or "")
                )
                if amount is None:
                    debit = _parse_amount(normalized.get("debit") or normalized.get("ut") or "")
                    credit = _parse_amount(normalized.get("credit") or normalized.get("inn") or "")
                    if credit is not None and abs(credit) > 0:
                        amount = abs(credit)
                    elif debit is not None and abs(debit) > 0:
                        amount = -abs(debit)
                row_date = _parse_date_value(
                    normalized.get("date")
                    or normalized.get("dato")
                    or normalized.get("booking date")
                    or normalized.get("transaction date")
                    or ""
                )
                if amount is None:
                    continue
                rows.append(
                    {
                        "date": row_date or today_iso(),
                        "description": description,
                        "amount": amount,
                        "reference": _extract_reference(description),
                    }
                )
            if rows:
                return rows

        for line in stripped.splitlines():
            parts = [part.strip() for part in re.split(r"[;,|\t]", line) if part.strip()]
            if len(parts) < 2:
                continue
            amount = None
            row_date = None
            description_parts: list[str] = []
            for part in parts:
                if row_date is None:
                    row_date = _parse_date_value(part)
                    if row_date:
                        continue
                parsed_amount = _parse_amount(part)
                if parsed_amount is not None:
                    amount = parsed_amount
                    continue
                description_parts.append(part)
            if amount is None:
                continue
            description = " ".join(description_parts)
            rows.append(
                {
                    "date": row_date or today_iso(),
                    "description": description,
                    "amount": amount,
                    "reference": _extract_reference(description),
                }
            )
    return rows


def _choose_customer_invoice(row: dict[str, Any], invoices: list[dict[str, Any]]) -> dict[str, Any] | None:
    reference = normalize_text(str(row.get("reference") or ""))
    description = normalize_text(str(row.get("description") or ""))
    amount = abs(float(row["amount"]))
    best: tuple[float, dict[str, Any]] | None = None
    for invoice in invoices:
        score = 0.0
        invoice_number = normalize_text(str(invoice.get("invoiceNumber") or ""))
        customer_name = normalize_text(str((invoice.get("customer") or {}).get("name") or ""))
        outstanding = float(invoice.get("amountOutstanding") or invoice.get("amountExcludingVat") or 0)
        if reference and invoice_number and reference in invoice_number:
            score += 6.0
        if customer_name and customer_name in description:
            score += 3.0
        if abs(outstanding - amount) < 1.0:
            score += 4.0
        elif abs(float(invoice.get("amountExcludingVat") or 0) - amount) < 1.0:
            score += 2.0
        if outstanding <= 0:
            score -= 5.0
        if best is None or score > best[0]:
            best = (score, invoice)
    if best and best[0] >= 3.0:
        return best[1]
    return None


def _choose_supplier_invoice(row: dict[str, Any], invoices: list[dict[str, Any]]) -> dict[str, Any] | None:
    reference = normalize_text(str(row.get("reference") or ""))
    description = normalize_text(str(row.get("description") or ""))
    amount = abs(float(row["amount"]))
    best: tuple[float, dict[str, Any]] | None = None
    for invoice in invoices:
        header = invoice.get("invoiceHeader") or {}
        score = 0.0
        invoice_number = normalize_text(str(header.get("invoiceNumber") or ""))
        note = normalize_text(str(header.get("note") or header.get("description") or ""))
        invoice_amount = float(header.get("invoiceAmount") or 0)
        if reference and invoice_number and reference in invoice_number:
            score += 6.0
        if note and note in description:
            score += 2.0
        if abs(invoice_amount - amount) < 1.0:
            score += 4.0
        if best is None or score > best[0]:
            best = (score, invoice)
    if best and best[0] >= 3.0:
        return best[1]
    return None


class BankReconciliationWorkflow(Workflow):
    name = "bank_reconciliation"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == self.name

    def allowed_endpoints(self) -> set[str]:
        return {"/invoice", "/invoice/paymentType", "/incomingInvoice/search", "/incomingInvoice"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/order", "/project"],
            steps=[
                PlanStep(op="parse_bank_statement", method="LOCAL", endpoint="attachment"),
                PlanStep(op="find_open_customer_invoices", method="GET", endpoint="/invoice"),
                PlanStep(op="get_payment_type", method="GET", endpoint="/invoice/paymentType"),
                PlanStep(op="find_open_supplier_invoices", method="GET", endpoint="/incomingInvoice/search"),
                PlanStep(op="register_matches", method="PUT", endpoint="/invoice/{id}/:payment"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        attachment_texts = task_spec.extracted.get("attachmentTexts") or []
        rows = _parse_csv_rows([str(text) for text in attachment_texts])
        if not rows:
            return {"action": self.name, "status": "no_bank_rows_found"}

        date_from, date_to = invoice_lookup_range()
        customer_invoice_resp = await client.get(
            "/invoice",
            params={
                "invoiceDateFrom": date_from,
                "invoiceDateTo": date_to,
                "count": 200,
                "sorting": "-invoiceDate",
                "fields": "id,invoiceDate,invoiceNumber,amountExcludingVat,amountOutstanding,paidAmount,customer,reference,comment",
            },
        )
        customer_invoices = [
            invoice
            for invoice in customer_invoice_resp.get("values", [])
            if float(invoice.get("amountOutstanding") or invoice.get("amountExcludingVat") or 0) > 0
        ]

        payment_type_resp = await client.get("/invoice/paymentType", params={"count": 20, "fields": "id,description,name"})
        payment_types = payment_type_resp.get("values", [])
        payment_type_id = int(payment_types[0]["id"]) if payment_types else None

        supplier_invoice_resp = await client.get(
            "/incomingInvoice/search",
            params={
                "invoiceDateFrom": date_from,
                "invoiceDateTo": date_to,
                "count": 200,
                "sorting": "-invoiceDate",
                "fields": "voucherId,invoiceHeader,metadata",
            },
        )
        supplier_invoices = supplier_invoice_resp.get("values", [])

        customer_matches = 0
        supplier_matches = 0
        match_errors: list[str] = []

        for row in rows:
            amount = float(row["amount"])
            row_date = str(row.get("date") or today_iso())
            if amount > 0 and payment_type_id is not None:
                invoice = _choose_customer_invoice(row, customer_invoices)
                if invoice is None:
                    continue
                outstanding = float(invoice.get("amountOutstanding") or invoice.get("amountExcludingVat") or amount)
                paid_amount = min(abs(amount), outstanding) if outstanding > 0 else abs(amount)
                try:
                    await client.put(
                        f"/invoice/{invoice['id']}/:payment",
                        params={"paymentDate": row_date, "paymentTypeId": payment_type_id, "paidAmount": paid_amount},
                    )
                    customer_matches += 1
                except Exception as exc:
                    match_errors.append(f"customer:{invoice.get('id')}:{exc}")
                continue

            if amount < 0:
                invoice = _choose_supplier_invoice(row, supplier_invoices)
                if invoice is None:
                    continue
                voucher_id = invoice.get("voucherId")
                if voucher_id is None:
                    continue
                try:
                    await client.post(
                        f"/incomingInvoice/{voucher_id}/addPayment",
                        {
                            "amountCurrency": abs(amount),
                            "paymentDate": row_date,
                            "useDefaultPaymentType": True,
                            "partialPayment": False,
                        },
                    )
                    supplier_matches += 1
                except Exception as exc:
                    match_errors.append(f"supplier:{voucher_id}:{exc}")

        return {
            "action": self.name,
            "rowsParsed": len(rows),
            "customerPaymentsRegistered": customer_matches,
            "supplierPaymentsRegistered": supplier_matches,
            "matchErrors": match_errors[:5],
        }
