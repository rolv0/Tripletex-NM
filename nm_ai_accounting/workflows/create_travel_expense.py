from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from models import ExecutionPlan, PlanStep, TaskSpec
from parsing.entity_extractor import extract_quoted_items
from tripletex import TripletexClient
from workflows.base import Workflow
from workflows.common import find_employee_id, pick_first_value_id


def _extract_days(prompt: str) -> int:
    match = re.search(r"\b(\d{1,2})\s*(?:day|days|dag|dagar|dias|tage|jours)\b", prompt, re.IGNORECASE)
    if not match:
        return 1
    try:
        return max(1, min(30, int(match.group(1))))
    except Exception:
        return 1


def _extract_title(prompt: str) -> str:
    quoted = extract_quoted_items(prompt)
    return quoted[0] if quoted else "Travel expense"


class CreateTravelExpenseWorkflow(Workflow):
    name = "create_travel_expense"

    def can_handle(self, task_spec: TaskSpec) -> bool:
        return task_spec.task_family == "create_travel_expense"

    def allowed_endpoints(self) -> set[str]:
        return {"/employee", "/travelExpense"}

    def build_plan(self, task_spec: TaskSpec) -> ExecutionPlan:
        return ExecutionPlan(
            task_family=self.name,
            allowed_endpoints=sorted(self.allowed_endpoints()),
            forbidden_domains=["/salary", "/ledger", "/voucher", "/invoice", "/order"],
            steps=[
                PlanStep(op="find_employee", method="GET", endpoint="/employee"),
                PlanStep(op="create_travel_expense", method="POST", endpoint="/travelExpense"),
            ],
        )

    async def execute(self, *, task_spec: TaskSpec, plan: ExecutionPlan, client: TripletexClient) -> dict[str, Any]:
        employee_id = await find_employee_id(client, task_spec.prompt)
        if employee_id is None:
            return {"action": "create_travel_expense", "status": "employee_missing"}

        days = _extract_days(task_spec.prompt)
        title = _extract_title(task_spec.prompt)
        departure = date.today()
        returning = departure + timedelta(days=max(days - 1, 0))

        payload: dict[str, Any] = {
            "title": title,
            "employee": {"id": employee_id},
            "date": departure.isoformat(),
            "travelDetails": {
                "isForeignTravel": False,
                "isDayTrip": days == 1,
                "departureDate": departure.isoformat(),
                "returnDate": returning.isoformat(),
                "departureFrom": "Office",
                "destination": title,
                "purpose": title,
                "detailedJourneyDescription": title,
            },
        }
        created = await client.post("/travelExpense", payload)
        return {
            "action": "create_travel_expense",
            "travelExpenseId": pick_first_value_id(created),
            "employeeId": employee_id,
        }

