"""All user-facing feature routes share the authentication dependency.
   Code Generated with the help of ClaudeCode.
"""

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.errors import (
    AUTH_SERVICE_UNAVAILABLE_RESPONSES,
    MFA_REQUIRED_RESPONSES,
    NOT_IMPLEMENTED_RESPONSES,
    UNAUTHORIZED_RESPONSES,
)
from app.modules.calls.router import router as calls_router
from app.modules.consents.router import router as consents_router
from app.modules.friends.router import router as friends_router
from app.modules.scenarios.router import router as scenarios_router
from app.modules.users.router import router as users_router
from app.modules.verification.router import router as verification_router
from app.modules.voice_training.router import router as voice_training_router

router = APIRouter(prefix="/api/v1", responses=NOT_IMPLEMENTED_RESPONSES)

protected_router = APIRouter(
    dependencies=[Depends(get_current_user)],
    responses=UNAUTHORIZED_RESPONSES
    | MFA_REQUIRED_RESPONSES
    | AUTH_SERVICE_UNAVAILABLE_RESPONSES,
)
for feature_router in (
    users_router,
    verification_router,
    friends_router,
    consents_router,
    calls_router,
    voice_training_router,
    scenarios_router,
):
    protected_router.include_router(feature_router)

router.include_router(protected_router)
