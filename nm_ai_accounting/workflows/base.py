from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from models import ExecutionPlan, TaskSpec
from tripletex import TripletexClient


class Workflow(ABC):
    name: str

    @abstractmethod
    def can_handle(self, task_spec: TaskSpec) -> bool:
        raise NotImplementedError

    @abstractmethod
    def allowed_endpoints(self) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        raise NotImplementedError

    @abstractmethod
    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        raise NotImplementedError

