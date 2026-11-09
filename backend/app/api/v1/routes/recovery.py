from fastapi import APIRouter, Depends

from app.core.security import require_api_key
from app.schemas.workflow import RecoveryRequest, RecoveryResult
from app.services.recovery_service import recovery_service

router = APIRouter()


@router.post("", response_model=RecoveryResult, dependencies=[Depends(require_api_key)])
async def recover(request: RecoveryRequest) -> RecoveryResult:
    return await recovery_service.recover(request)

