from __future__ import annotations

from models import ExecutionPlan, TaskSpec
from workflows.base import Workflow


def build_execution_plan(workflow: Workflow, task_spec: TaskSpec) -> ExecutionPlan:
    return workflow.build_plan(task_spec)

