from __future__ import annotations

import re
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_all_amounts, extract_quoted_items
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import today_iso


def _parse_account_number(prompt: str) -> str | None:
    match = re.search(r"(?:konto|account|cuenta)\s*(\d{4})", prompt, re.IGNORECASE)
    return match.group(1) if match else None


def _parse_dimension_name(prompt: str) -> str:
    quoted = extract_quoted_items(prompt)
    return quoted[0] if quoted else "Dimension"


def _parse_dimension_values(prompt: str) -> list[str]:
    quoted = extract_quoted_items(prompt)
    if len(quoted) >= 2:
        return quoted[1:3]
    return ["Standard", "Basis"]


class LedgerCorrectionWorkflow(Workflow):
    name = "ledger_correction"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "ledger_correction"

    def allowed_endpoints(self) -> set[str]:
        return {
            "/ledger/accountingDimensionName",
            "/ledger/accountingDimensionValue",
            "/ledger/voucher",
        }

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/invoice", "/order", "/project"],
            steps=[
                PlanStep(op="create_dimension_name", method="POST", endpoint="/ledger/accountingDimensionName"),
                PlanStep(op="create_dimension_values", method="POST", endpoint="/ledger/accountingDimensionValue"),
                PlanStep(op="create_voucher", method="POST", endpoint="/ledger/voucher"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        amount = extract_all_amounts(prompt)[0] if extract_all_amounts(prompt) else 0.0
        account_no = _parse_account_number(prompt) or "7100"
        dimension_name = _parse_dimension_name(prompt)
        dimension_values = _parse_dimension_values(prompt)

        created_dimension = await client.post(
            "/ledger/accountingDimensionName",
            {"dimensionName": dimension_name, "active": True},
        )
        dim_value = created_dimension.get("value", {})
        dimension_index = int(dim_value.get("dimensionIndex") or 1)
        created_dimension_id = dim_value.get("id")

        created_value_ids: list[int] = []
        for idx, value_name in enumerate(dimension_values, start=1):
            value_resp = await client.post(
                "/ledger/accountingDimensionValue",
                {
                    "displayName": value_name,
                    "dimensionIndex": dimension_index,
                    "number": str(idx),
                    "active": True,
                    "showInVoucherRegistration": True,
                },
            )
            val = value_resp.get("value", {})
            if val.get("id") is not None:
                created_value_ids.append(int(val["id"]))

        voucher_id: int | None = None
        if amount > 0:
            voucher_payload: dict[str, Any] = {
                "date": today_iso(),
                "description": f"Auto ledger entry for {dimension_name}",
                "postings": [
                    {
                        "date": today_iso(),
                        "account": {"number": account_no},
                        "amount": amount,
                        "amountCurrency": amount,
                    },
                    {
                        "date": today_iso(),
                        "account": {"number": "2400"},
                        "amount": -amount,
                        "amountCurrency": -amount,
                    },
                ],
            }
            if created_value_ids:
                voucher_payload["postings"][0]["accountingDimensionValues"] = [{"id": created_value_ids[0]}]
            try:
                voucher_resp = await client.post("/ledger/voucher", voucher_payload)
                val = voucher_resp.get("value", {})
                if val.get("id") is not None:
                    voucher_id = int(val["id"])
            except RuntimeError:
                # Keep partial progress (dimension + values) instead of failing whole solve.
                voucher_id = None

        return {
            "action": "ledger_correction",
            "dimensionName": dimension_name,
            "dimensionId": int(created_dimension_id) if created_dimension_id is not None else None,
            "dimensionIndex": dimension_index,
            "dimensionValueIds": created_value_ids,
            "voucherId": voucher_id,
        }

