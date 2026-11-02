from fastapi import APIRouter

from app.core.errors import not_implemented
from app.modules.verification.schemas import VerificationResponse

router = APIRouter(prefix="/users/me/verification", tags=["Identity verification"])


@router.post("", response_model=VerificationResponse, status_code=202)
async def verify_user() -> VerificationResponse:
    """Start provider verification; clients cannot mark themselves verified."""
    not_implemented("Identity verification")
