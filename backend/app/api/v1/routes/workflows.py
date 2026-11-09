from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends

from app.core.exceptions import CortexError
from app.core.security import require_api_key
from app.schemas.workflow import MetricSnapshot, RecoveryRequest, RecoveryResult, Severity, Workflow
from app.services.recovery_service import recovery_service
from app.services.runtime_service import runtime_service
from app.services.workflow_service import workflow_service

router = APIRouter()


@router.get("", response_model=list[Workflow])
async def list_workflows() -> list[Workflow]:
    return await workflow_service.list_workflows()


@router.get("/metrics", response_model=MetricSnapshot)
async def metrics() -> MetricSnapshot:
    return await workflow_service.metrics()


@router.get("/{workflow_id}", response_model=Workflow)
async def get_workflow(workflow_id: UUID) -> Workflow:
    workflow = await workflow_service.get_workflow(workflow_id)
    if workflow is None:
        raise CortexError("Workflow not found", status_code=404)
    return workflow


@router.post("/{workflow_id}/fail", response_model=Workflow, dependencies=[Depends(require_api_key)])
async def fail_workflow(workflow_id: UUID, background_tasks: BackgroundTasks, auto_recover: bool = True) -> Workflow:
    workflow = await runtime_service.inject_failure(workflow_id, auto_recover=False)
    if workflow is None:
        raise CortexError("Workflow not found", status_code=404)
    if auto_recover:
        background_tasks.add_task(
            recovery_service.recover_background,
            RecoveryRequest(workflow_id=workflow.id, severity=Severity.HIGH),
        )
    return workflow


@router.post("/{workflow_id}/recover", response_model=RecoveryResult, dependencies=[Depends(require_api_key)])
async def recover_workflow(workflow_id: UUID) -> RecoveryResult:
    return await recovery_service.recover(RecoveryRequest(workflow_id=workflow_id, severity=Severity.HIGH))
