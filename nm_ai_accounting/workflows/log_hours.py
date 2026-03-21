from __future__ import annotations

from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import ensure_customer, find_employee_id, find_or_create_activity, find_or_create_project, parse_hours, pick_first_value_id, today_iso


class LogHoursWorkflow(Workflow):
    name = "log_hours"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "log_hours"

    def allowed_endpoints(self) -> set[str]:
        return {"/employee", "/customer", "/project", "/activity", "/timesheet/entry"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/travelExpense", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="find_employee", method="GET", endpoint="/employee"),
                PlanStep(op="ensure_customer", method="GET", endpoint="/customer"),
                PlanStep(op="find_or_create_project", method="POST", endpoint="/project"),
                PlanStep(op="find_or_create_activity", method="POST", endpoint="/activity"),
                PlanStep(op="create_timesheet_entry", method="POST", endpoint="/timesheet/entry"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        employee_id = await find_employee_id(client, task_spec.prompt)
        if employee_id is None:
            return {"action": "log_hours", "status": "employee_missing"}

        customer_id = await ensure_customer(client, task_spec.prompt)
        project_id = await find_or_create_project(client, task_spec.prompt, customer_id)
        if project_id is None:
            return {"action": "log_hours", "status": "project_missing", "employeeId": employee_id, "customerId": customer_id}

        activity_id = await find_or_create_activity(client, task_spec.prompt)
        if activity_id is None:
            return {"action": "log_hours", "status": "activity_missing", "employeeId": employee_id, "projectId": project_id}

        hours = parse_hours(task_spec.prompt) or 0.0
        payload: dict[str, Any] = {
            "employee": {"id": employee_id},
            "project": {"id": project_id},
            "activity": {"id": activity_id},
            "date": today_iso(),
            "hours": hours,
        }
        created = await client.post("/timesheet/entry", payload)
        return {
            "action": "log_hours",
            "timesheetEntryId": pick_first_value_id(created),
            "employeeId": employee_id,
            "customerId": customer_id,
            "projectId": project_id,
            "activityId": activity_id,
            "hours": hours,
        }
