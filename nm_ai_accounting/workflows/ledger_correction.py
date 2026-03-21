from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.prompt_normalizer import normalize_prompt
from parsing.entity_extractor import extract_all_amounts, extract_quoted_items
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import today_iso


DIMENSION_HINTS = (
    "dimension",
    "dimensjon",
    "dimensao",
    "dimension contable",
    "dimension comptable",
    "accounting dimension",
)

DEPRECIATION_HINTS = (
    "depreciation",
    "depreciate",
    "amortissement",
    "amortization",
    "amortizacao",
    "avskrivning",
    "abschreibung",
    "immobilisation",
    "fixed asset",
    "anleggsmiddel",
)

YEAR_END_HINTS = (
    "year end",
    "year-end",
    "annual close",
    "annual closing",
    "cloture annuelle",
    "cloture simplifiee",
    "arsavslutning",
    "arsoppgjor",
    "cierre anual",
    "fecho anual",
    "cloture mensuelle",
    "monthly close",
    "cierre mensual",
    "fecho mensal",
)

MONTH_MAP = {
    "january": 1,
    "januar": 1,
    "janvier": 1,
    "enero": 1,
    "janeiro": 1,
    "february": 2,
    "februar": 2,
    "fevrier": 2,
    "février": 2,
    "febrero": 2,
    "fevereiro": 2,
    "march": 3,
    "mars": 3,
    "marzo": 3,
    "marco": 3,
    "março": 3,
    "april": 4,
    "april": 4,
    "abril": 4,
    "may": 5,
    "mai": 5,
    "mayo": 5,
    "maio": 5,
    "june": 6,
    "juni": 6,
    "juin": 6,
    "junio": 6,
    "junho": 6,
    "july": 7,
    "juli": 7,
    "juillet": 7,
    "julio": 7,
    "julho": 7,
    "august": 8,
    "août": 8,
    "aout": 8,
    "agosto": 8,
    "september": 9,
    "septembre": 9,
    "septiembre": 9,
    "setembro": 9,
    "october": 10,
    "oktober": 10,
    "octobre": 10,
    "octubre": 10,
    "outubro": 10,
    "november": 11,
    "novembre": 11,
    "noviembre": 11,
    "novembro": 11,
    "december": 12,
    "desember": 12,
    "decembre": 12,
    "décembre": 12,
    "diciembre": 12,
    "dezembro": 12,
}


def _wants_dimension(prompt: str) -> bool:
    prompt_l = normalize_prompt(prompt)
    return any(token in prompt_l for token in DIMENSION_HINTS)


def _parse_account_numbers(prompt: str) -> list[str]:
    matches = re.findall(r"(?:konto|account|cuenta|compte|conta)\s*(\d{4})", prompt, re.IGNORECASE)
    ordered: list[str] = []
    for value in matches:
        if value not in ordered:
            ordered.append(value)
    return ordered


def _parse_dimension_name(prompt: str) -> str:
    quoted = extract_quoted_items(prompt)
    return quoted[0] if quoted else "Dimension"


def _parse_dimension_values(prompt: str) -> list[str]:
    quoted = extract_quoted_items(prompt)
    if len(quoted) >= 2:
        return quoted[1:3]
    return ["Standard", "Basis"]


def _parse_voucher_date(prompt: str) -> str:
    prompt_l = normalize_prompt(prompt)
    year_match = re.search(r"\b(20\d{2})\b", prompt_l)
    month = None
    for token, month_value in MONTH_MAP.items():
        if token in prompt_l:
            month = month_value
            break
    if month and year_match:
        year = int(year_match.group(1))
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day).isoformat()
    if year_match and any(token in prompt_l for token in YEAR_END_HINTS):
        year = int(year_match.group(1))
        return date(year, 12, 31).isoformat()
    return today_iso()


def _is_year_end_task(prompt: str) -> bool:
    prompt_l = normalize_prompt(prompt)
    return any(token in prompt_l for token in YEAR_END_HINTS) or any(token in prompt_l for token in DEPRECIATION_HINTS)


def _parse_years(value: str) -> float | None:
    year_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:ans?|years?|jahre?|ar|anos?)\b", value, re.IGNORECASE)
    if year_match:
        return float(year_match.group(1).replace(",", "."))
    month_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:months?|monate?|mois|meses?)\b", value, re.IGNORECASE)
    if month_match:
        months = float(month_match.group(1).replace(",", "."))
        if months > 0:
            return months / 12.0
    return None


def _clean_asset_name(raw: str) -> str:
    text = raw.strip().strip(" :-")
    text = re.sub(r"^\d+\)\s*", "", text)
    text = re.sub(
        r"^(?:and|und|et|og|e|y|then|puis|ensuite|luego)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if ":" in text:
        text = text.split(":")[-1].strip()
    return text


def _parse_asset_specs(prompt: str) -> list[dict[str, Any]]:
    asset_specs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for match in re.finditer(r"([A-ZÀ-ÖØ-öø-ÿ0-9][^(\n]{1,120}?)\s*\(([^)]{8,220})\)", prompt):
        raw_name, body = match.groups()
        amount_values = extract_all_amounts(body)
        if not amount_values:
            continue
        lifetime_years = _parse_years(body)
        if lifetime_years is None or lifetime_years <= 0:
            continue
        account_numbers = _parse_account_numbers(body)
        if not account_numbers:
            continue
        name = _clean_asset_name(raw_name)
        acquisition_cost = float(amount_values[0])
        unique_key = (name.lower(), account_numbers[0])
        if unique_key in seen:
            continue
        seen.add(unique_key)
        asset_specs.append(
            {
                "name": name or f"Asset {len(asset_specs) + 1}",
                "acquisitionCost": acquisition_cost,
                "lifetimeYears": lifetime_years,
                "assetAccountNumber": account_numbers[0],
                "expenseAccountNumber": account_numbers[1] if len(account_numbers) > 1 else None,
            }
        )

    return asset_specs


async def _find_account(client: TripletexClient, number: str) -> dict[str, Any] | None:
    response = await client.get(
        "/ledger/account",
        params={"number": number, "count": 10, "fields": "id,number,name,type,ledgerType,isInactive,isBankAccount,vatType,vatLocked,legalVatTypes"},
    )
    values = response.get("values", [])
    for value in values:
        if str(value.get("number")) == str(number) and str(value.get("ledgerType") or "GENERAL") == "GENERAL":
            return value
    return values[0] if values else None


async def _find_offset_account(
    client: TripletexClient,
    *,
    prefer_balance: bool,
    exclude_numbers: set[str],
) -> dict[str, Any] | None:
    response = await client.get(
        "/ledger/account",
        params={
            "isBalanceAccount": prefer_balance,
            "count": 50,
            "fields": "id,number,name,type,ledgerType,isInactive,isBankAccount,vatType,vatLocked,legalVatTypes",
        },
    )
    values = response.get("values", [])
    for value in values:
        if str(value.get("ledgerType") or "GENERAL") != "GENERAL":
            continue
        if value.get("isInactive"):
            continue
        if value.get("isBankAccount"):
            continue
        number = str(value.get("number") or "")
        if number in exclude_numbers:
            continue
        return value
    return None


async def _find_depreciation_expense_account(
    client: TripletexClient,
    *,
    exclude_numbers: set[str],
) -> dict[str, Any] | None:
    response = await client.get(
        "/ledger/account",
        params={
            "isBalanceAccount": False,
            "count": 200,
            "fields": "id,number,name,type,ledgerType,isInactive,isBankAccount,vatType,vatLocked,legalVatTypes",
        },
    )
    values = response.get("values", [])
    keywords = ("depreci", "amort", "avskr", "abschreib", "verdifall")
    fallback: dict[str, Any] | None = None
    for value in values:
        if str(value.get("ledgerType") or "GENERAL") != "GENERAL":
            continue
        if value.get("isInactive"):
            continue
        if value.get("isBankAccount"):
            continue
        number = str(value.get("number") or "")
        if number in exclude_numbers:
            continue
        if fallback is None and number.startswith(("6", "7")):
            fallback = value
        normalized_name = normalize_prompt(str(value.get("name") or ""))
        if any(token in normalized_name for token in keywords):
            return value
    return fallback


def _is_balance_type(account: dict[str, Any]) -> bool:
    return str(account.get("type") or "") in {"ASSETS", "EQUITY", "LIABILITIES"}


def _pick_vat_type_id(account: dict[str, Any]) -> int | None:
    vat_type = account.get("vatType")
    if isinstance(vat_type, dict) and vat_type.get("id") is not None:
        return int(vat_type["id"])

    legal_vat_types = account.get("legalVatTypes")
    if isinstance(legal_vat_types, list):
        for vat in legal_vat_types:
            if isinstance(vat, dict) and vat.get("id") == 0:
                return 0
    return None


def _build_postings(
    *,
    voucher_date: str,
    source_account: dict[str, Any],
    offset_account: dict[str, Any],
    amount: float,
    dimension_value_id: int | None,
) -> list[dict[str, Any]]:
    if _is_balance_type(source_account):
        source_amount = -abs(amount)
        offset_amount = abs(amount)
    else:
        source_amount = abs(amount)
        offset_amount = -abs(amount)

    source_vat_type_id = _pick_vat_type_id(source_account)
    source_posting: dict[str, Any] = {
        "row": 1,
        "date": voucher_date,
        "account": {"id": int(source_account["id"])},
        "amountGross": source_amount,
        "amountGrossCurrency": source_amount,
    }
    if dimension_value_id is not None:
        source_posting["freeAccountingDimension1"] = {"id": dimension_value_id}
    if source_vat_type_id is not None:
        source_posting["vatType"] = {"id": source_vat_type_id}

    offset_posting: dict[str, Any] = {
        "row": 2,
        "date": voucher_date,
        "account": {"id": int(offset_account["id"])},
        "amountGross": offset_amount,
        "amountGrossCurrency": offset_amount,
    }
    return [source_posting, offset_posting]


async def _build_depreciation_postings(
    *,
    client: TripletexClient,
    voucher_date: str,
    asset_specs: list[dict[str, Any]],
    dimension_value_id: int | None,
) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    used_numbers = {str(spec["assetAccountNumber"]) for spec in asset_specs if spec.get("assetAccountNumber")}
    shared_expense_account = await _find_depreciation_expense_account(client, exclude_numbers=used_numbers)

    row = 1
    for spec in asset_specs:
        asset_account = await _find_account(client, str(spec["assetAccountNumber"]))
        if asset_account is None:
            continue

        expense_account: dict[str, Any] | None = shared_expense_account
        explicit_expense_number = spec.get("expenseAccountNumber")
        if explicit_expense_number:
            expense_account = await _find_account(client, str(explicit_expense_number))
        if expense_account is None:
            expense_account = await _find_offset_account(
                client,
                prefer_balance=False,
                exclude_numbers={str(asset_account.get("number") or "")},
            )
        if expense_account is None:
            continue

        lifetime_years = float(spec["lifetimeYears"])
        depreciation_amount = round(float(spec["acquisitionCost"]) / lifetime_years, 2)
        if depreciation_amount <= 0:
            continue

        expense_posting: dict[str, Any] = {
            "row": row,
            "date": voucher_date,
            "description": f"Annual depreciation {spec['name']}",
            "account": {"id": int(expense_account["id"])},
            "amountGross": depreciation_amount,
            "amountGrossCurrency": depreciation_amount,
        }
        if dimension_value_id is not None:
            expense_posting["freeAccountingDimension1"] = {"id": dimension_value_id}

        asset_posting: dict[str, Any] = {
            "row": row + 1,
            "date": voucher_date,
            "description": f"Annual depreciation {spec['name']}",
            "account": {"id": int(asset_account["id"])},
            "amountGross": -depreciation_amount,
            "amountGrossCurrency": -depreciation_amount,
        }
        if dimension_value_id is not None:
            asset_posting["freeAccountingDimension1"] = {"id": dimension_value_id}

        postings.extend([expense_posting, asset_posting])
        row += 2

    return postings


class LedgerCorrectionWorkflow(Workflow):
    name = "ledger_correction"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "ledger_correction"

    def allowed_endpoints(self) -> set[str]:
        return {
            "/ledger/account",
            "/ledger/accountingDimensionName",
            "/ledger/accountingDimensionValue",
            "/ledger/voucher",
        }

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        wants_dimension = _wants_dimension(task_spec.prompt)
        steps = [PlanStep(op="resolve_accounts", method="GET", endpoint="/ledger/account")]
        if wants_dimension:
            steps.extend(
                [
                    PlanStep(op="create_dimension_name", method="POST", endpoint="/ledger/accountingDimensionName"),
                    PlanStep(op="create_dimension_values", method="POST", endpoint="/ledger/accountingDimensionValue"),
                ]
            )
        steps.append(PlanStep(op="create_voucher", method="POST", endpoint="/ledger/voucher"))
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/invoice", "/order", "/project"],
            steps=steps,
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        prompt = task_spec.prompt
        amounts = extract_all_amounts(prompt)
        amount = amounts[0] if amounts else 0.0
        account_numbers = _parse_account_numbers(prompt)
        primary_account_no = account_numbers[0] if account_numbers else "7100"
        asset_specs = _parse_asset_specs(prompt)
        dimension_name = _parse_dimension_name(prompt)
        dimension_values = _parse_dimension_values(prompt)
        wants_dimension = _wants_dimension(prompt)
        voucher_date = _parse_voucher_date(prompt)

        created_dimension_id: int | None = None
        dimension_index: int | None = None
        created_value_ids: list[int] = []

        if wants_dimension:
            created_dimension = await client.post(
                "/ledger/accountingDimensionName",
                {"dimensionName": dimension_name, "active": True},
            )
            dim_value = created_dimension.get("value", {})
            dimension_index = int(dim_value.get("dimensionIndex") or 1)
            if dim_value.get("id") is not None:
                created_dimension_id = int(dim_value["id"])

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
        if asset_specs and _is_year_end_task(prompt):
            postings = await _build_depreciation_postings(
                client=client,
                voucher_date=voucher_date,
                asset_specs=asset_specs,
                dimension_value_id=created_value_ids[0] if created_value_ids else None,
            )
            voucher_payload: dict[str, Any] = {
                "date": voucher_date,
                "description": "Auto year-end depreciation entry",
                "postings": postings,
            }
            if postings:
                voucher_resp = await client.post("/ledger/voucher", voucher_payload)
                val = voucher_resp.get("value", {})
                if val.get("id") is not None:
                    voucher_id = int(val["id"])
        elif amount > 0:
            source_account = await _find_account(client, primary_account_no)
            if source_account is not None:
                offset_account = await _find_offset_account(
                    client,
                    prefer_balance=not _is_balance_type(source_account),
                    exclude_numbers={str(source_account.get("number") or "")},
                )
            else:
                offset_account = None

            postings: list[dict[str, Any]] = []
            if source_account is not None and offset_account is not None:
                postings = _build_postings(
                    voucher_date=voucher_date,
                    source_account=source_account,
                    offset_account=offset_account,
                    amount=amount,
                    dimension_value_id=created_value_ids[0] if created_value_ids else None,
                )

            voucher_payload: dict[str, Any] = {
                "date": voucher_date,
                "description": f"Auto ledger entry for {dimension_name if wants_dimension else 'monthly close'}",
                "postings": postings,
            }
            if postings:
                voucher_resp = await client.post("/ledger/voucher", voucher_payload)
                val = voucher_resp.get("value", {})
                if val.get("id") is not None:
                    voucher_id = int(val["id"])

        return {
            "action": "ledger_correction",
            "dimensionName": dimension_name if wants_dimension else None,
            "dimensionId": created_dimension_id,
            "dimensionIndex": dimension_index,
            "dimensionValueIds": created_value_ids,
            "assetCount": len(asset_specs),
            "voucherId": voucher_id,
        }
