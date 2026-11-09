from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.events import FailureInjectionEvent, FailureInjectionRequest, FailureType
from app.core.logging import get_logger
from app.core.security import require_api_key
from app.schemas.workflow import RecoveryRequest, Severity
from app.services.metrics_service import metrics_service
from app.services.recovery_service import recovery_service
from app.services.runtime_service import runtime_service
from app.services.topology_service import topology_service
from app.services.websocket_events import sandbox_ws
from app.services.workflow_service import workflow_service

logger = get_logger(__name__)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

SUPPORTED_FAILURES = {item.value for item in FailureType}

FAILURE_SEVERITY = {
    FailureType.database_timeout: Severity.HIGH,
    FailureType.redis_failure: Severity.HIGH,
    FailureType.memory_leak: Severity.CRITICAL,
    FailureType.cpu_spike: Severity.HIGH,
    FailureType.api_crash: Severity.CRITICAL,
    FailureType.dependency_failure: Severity.MEDIUM,
    FailureType.network_latency: Severity.MEDIUM,
}


async def _resolve_workflow_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        workflows = await workflow_service.list_workflows()
        match = next((item for item in workflows if item.name == raw or str(item.id) == raw), None)
        if match is None and workflows:
            return workflows[0].id
        if match is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return match.id


@router.post("/inject-failure", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)])
async def inject_failure(request: FailureInjectionRequest, background_tasks: BackgroundTasks) -> dict[str, str | bool]:
    logger.info("sandbox_inject_api_hit workflow_id=%s failure=%s", request.workflow_id, request.failure_type.value)
    if request.failure_type.value not in SUPPORTED_FAILURES:
        raise HTTPException(status_code=400, detail="Unsupported failure type")

    workflow_id = await _resolve_workflow_id(request.workflow_id)
    signal = request.failure_type.value
    severity = FAILURE_SEVERITY.get(request.failure_type, Severity.HIGH)

    workflow = await runtime_service.inject_failure(workflow_id, signal=signal, auto_recover=False)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    await metrics_service.spike_metrics(request, workflow_id)
    await topology_service.apply_failure(workflow_id, signal)

    injection_event = FailureInjectionEvent(
        failure_type=request.failure_type,
        workflow_id=str(workflow_id),
        injected_by=request.injected_by,
        signal=signal,
    )
    await sandbox_ws.broadcast(
        {
            **injection_event.model_dump(mode="json"),
            "message": f"Injected {signal} on workflow {workflow.name}",
            "step": 0,
        }
    )
    await sandbox_ws.broadcast(
        {
            "type": "timeline_update",
            "step": 0,
            "stage": "failure.detected",
            "message": f"Failure detected: {signal}",
            "workflow_id": str(workflow_id),
        }
    )

    if request.auto_recover:
        background_tasks.add_task(
            recovery_service.recover_background,
            RecoveryRequest(workflow_id=workflow_id, signal=signal, severity=severity),
        )
        logger.info("sandbox_recovery_scheduled workflow_id=%s signal=%s", workflow_id, signal)

    return {
        "status": "failure injected",
        "workflow_id": str(workflow_id),
        "signal": signal,
        "auto_recover": request.auto_recover,
    }
