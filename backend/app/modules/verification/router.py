from fastapi import APIRouter

from app.core.errors import not_implemented
from app.modules.verification.schemas import VerificationResponse

router = APIRouter(prefix="/users/me/verification", tags=["Identity verification"])


@router.post("", response_model=VerificationResponse, status_code=202)
async def verify_user() -> VerificationResponse:
    """Start identity verification with the external provider.

    Clients cannot mark themselves verified; the provider's result sets the
    user's verified status.

    **TBD:** how the API receives the provider's result, and how clients
    check verification status.
    """
    not_implemented("Identity verification")
