from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import Page, Pagination
from app.core.errors import not_implemented
from app.modules.scenarios.schemas import ScenarioCreate, ScenarioResponse, ScenarioUpdate

router = APIRouter(prefix="/scenarios", tags=["Scenarios"])


@router.post("", response_model=ScenarioResponse, status_code=201)
async def add_scenario(payload: ScenarioCreate) -> ScenarioResponse:
    not_implemented("Scenario creation")


@router.get("", response_model=Page[ScenarioResponse])
async def get_scenarios(
    pagination: Annotated[Pagination, Query()],
) -> Page[ScenarioResponse]:
    not_implemented("Scenario listing")


@router.get("/{id}", response_model=ScenarioResponse)
async def get_scenario(id: UUID) -> ScenarioResponse:
    not_implemented("Scenario retrieval")


@router.put("/{id}", response_model=ScenarioResponse)
async def update_scenario(id: UUID, payload: ScenarioUpdate) -> ScenarioResponse:
    not_implemented("Scenario editing")


@router.delete("/{id}", status_code=204)
async def remove_scenario(id: UUID) -> None:
    not_implemented("Scenario deletion")
