from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    op: str
    method: str
    endpoint: str
    params: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    task_family: str
    allowed_endpoints: list[str] = Field(default_factory=list)
    forbidden_domains: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)

