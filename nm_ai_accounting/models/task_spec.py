from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskFamily = Literal[
    "create_employee",
    "update_employee",
    "create_customer",
    "create_supplier",
    "update_customer",
    "create_product",
    "create_project",
    "create_order",
    "order_to_invoice",
    "create_invoice",
    "register_payment",
    "create_credit_note",
    "create_travel_expense",
    "delete_travel_expense",
    "create_department",
    "enable_department_accounting",
    "ledger_correction",
    "voucher_reverse_or_delete",
    "salary_transaction",
    "unknown",
]

Intent = Literal["create", "update", "delete", "reverse", "register", "create_and_convert", "unknown"]
LanguageCode = Literal["nb", "nn", "en", "es", "pt", "de", "fr", "unknown"]


class Entity(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class TaskSpec(BaseModel):
    language: LanguageCode
    task_family: TaskFamily
    intent: Intent
    entities: list[Entity] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    requires_payment: bool = False
    confidence: float = 0.0
    risk_flags: list[str] = Field(default_factory=list)
    prompt: str = ""
