from __future__ import annotations

from typing import Any

from execution.retry_policy import RetryPolicy
from models import ExecutionPlan, TaskSpec
from tripletex import TripletexClient
from workflows.base import Workflow


async def execute_plan(
    *,
    workflow: Workflow,
    task_spec: TaskSpec,
    plan: ExecutionPlan,
    client: TripletexClient,
    retry_policy: RetryPolicy,
) -> dict[str, Any]:
    client.set_allowed_endpoints(set(plan.allowed_endpoints))
    attempt = 0
    while True:
        try:
            return await workflow.execute(task_spec=task_spec, plan=plan, client=client)
        except Exception as exc:
            text = str(exc)
            if retry_policy.should_retry(attempt, text):
                attempt += 1
                continue
            raise

