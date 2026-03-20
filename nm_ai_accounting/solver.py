from __future__ import annotations

import logging
import time
from typing import Any

from config import get_settings
from execution import RetryPolicy, build_execution_plan, execute_plan
from models import SolveRequest
from parsing import parse_attachments
from routing import classify_task, get_workflow
from tripletex import TripletexClient
from utils.logging import json_log

logger = logging.getLogger("nm-ai-accounting.solver")


async def solve_task(req: SolveRequest) -> dict[str, Any]:
    started = time.perf_counter()
    settings = get_settings()

    attachments = parse_attachments(req.files)
    task_spec = classify_task(req.prompt, attachments)
    workflow = get_workflow(task_spec.task_family)

    if workflow is None:
        result = {
            "action": "no_op",
            "reason": "unclassified_prompt",
            "task_family": task_spec.task_family,
            "language": task_spec.language,
        }
        json_log(
            logger,
            "solve_result",
            language=task_spec.language,
            task_family=task_spec.task_family,
            confidence=task_spec.confidence,
            files=len(attachments),
            duration_ms=int((time.perf_counter() - started) * 1000),
            result=result,
        )
        return result

    client = TripletexClient(
        base_url=req.tripletex_credentials.base_url,
        session_token=req.tripletex_credentials.session_token,
        timeout_seconds=settings.request_timeout_seconds,
    )
    plan = build_execution_plan(workflow, task_spec)
    retry_policy = RetryPolicy(max_retries=settings.max_intelligent_retries)

    json_log(
        logger,
        "execution_plan",
        language=task_spec.language,
        task_family=task_spec.task_family,
        confidence=task_spec.confidence,
        files=len(attachments),
        steps=[step.op for step in plan.steps],
        allowed_endpoints=plan.allowed_endpoints,
    )

    result = await execute_plan(
        workflow=workflow,
        task_spec=task_spec,
        plan=plan,
        client=client,
        retry_policy=retry_policy,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    json_log(
        logger,
        "api_summary",
        get=client.summary.get,
        post=client.summary.post,
        put=client.summary.put,
        delete=client.summary.delete,
        client_4xx=client.summary.client_4xx,
        client_5xx=client.summary.client_5xx,
        calls=client.summary.calls,
        duration_ms=duration_ms,
    )
    return result

