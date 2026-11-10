from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.schemas import Page, Pagination
from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import api_error, error_responses, merge_responses
from app.integrations.supabase import (
    SupabaseDatabase,
    SupabaseForeignKeyError,
    get_supabase_db,
)
from app.modules.scenarios.schemas import (
    Difficulty,
    ScenarioCreate,
    ScenarioResponse,
    ScenarioUpdate,
)

router = APIRouter(prefix="/scenarios", tags=["Scenarios"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]

SCENARIO_COLUMNS = (
    "id,created_by,title,category,difficulty,description,system_prompt,red_flags,"
    "max_duration_seconds,created_at,updated_at"
)
DIFFICULTY_LEVELS: dict[Difficulty, int] = {"easy": 1, "medium": 2, "hard": 3}
DIFFICULTY_NAMES = {level: name for name, level in DIFFICULTY_LEVELS.items()}

NOT_FOUND = error_responses(
    status.HTTP_404_NOT_FOUND,
    ("scenario_not_found", "No scenario the current user can see has this ID."),
)
CHANGE_ERRORS = merge_responses(
    NOT_FOUND,
    error_responses(
        status.HTTP_403_FORBIDDEN,
        ("scenario_read_only", "Built-in scenarios cannot be changed or deleted."),
    ),
)


def visible_to(user_id: UUID) -> dict[str, str]:
    """Active built-in scenarios, plus the user's own."""
    return {"or": f"(and(created_by.is.null,is_active.is.true),created_by.eq.{user_id})"}


def owned_by(scenario_id: UUID, user_id: UUID) -> dict[str, str]:
    return {"id": f"eq.{scenario_id}", "created_by": f"eq.{user_id}"}


@router.post(
    "",
    response_model=ScenarioResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        status.HTTP_409_CONFLICT,
        ("profile_required", "The current user has not finished onboarding."),
    ),
)
async def add_scenario(
    payload: ScenarioCreate, user: CurrentUser, supabase_db: SupabaseDb
) -> ScenarioResponse:
    """Create a scenario that only the current user can see and use."""
    row = _to_row(payload.model_dump())
    try:
        created = await supabase_db.insert(
            "scam_scenarios", {**row, "created_by": str(user.user_id)}, SCENARIO_COLUMNS
        )
    except SupabaseForeignKeyError:
        api_error(status.HTTP_409_CONFLICT, "profile_required", "Finish onboarding first.")
    return _to_scenario(created)


@router.get("", response_model=Page[ScenarioResponse])
async def get_scenarios(
    pagination: Annotated[Pagination, Query()], user: CurrentUser, supabase_db: SupabaseDb
) -> Page[ScenarioResponse]:
    """List active built-in scenarios and the current user's own, oldest first."""
    rows = await supabase_db.select(
        "scam_scenarios",
        visible_to(user.user_id),
        SCENARIO_COLUMNS,
        order="created_at.asc,id",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[_to_scenario(row) for row in rows],
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{id}", response_model=ScenarioResponse, responses=NOT_FOUND)
async def get_scenario(id: UUID, user: CurrentUser, supabase_db: SupabaseDb) -> ScenarioResponse:
    rows = await supabase_db.select(
        "scam_scenarios", {**visible_to(user.user_id), "id": f"eq.{id}"}, SCENARIO_COLUMNS
    )
    if not rows:
        _not_found()
    return _to_scenario(rows[0])


@router.put("/{id}", response_model=ScenarioResponse, responses=CHANGE_ERRORS)
async def update_scenario(
    id: UUID, payload: ScenarioUpdate, user: CurrentUser, supabase_db: SupabaseDb
) -> ScenarioResponse:
    """Update one of the current user's scenarios.

    Only the fields that are sent are changed.
    """
    values = _to_row(payload.model_dump(exclude_unset=True))
    if not values:
        return await get_scenario(id, user, supabase_db)
    rows = await supabase_db.update(
        "scam_scenarios", owned_by(id, user.user_id), values, SCENARIO_COLUMNS
    )
    if not rows:
        await _not_owned(id, user.user_id, supabase_db)
    return _to_scenario(rows[0])


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=CHANGE_ERRORS)
async def remove_scenario(id: UUID, user: CurrentUser, supabase_db: SupabaseDb) -> None:
    """Delete one of the current user's scenarios. Past calls keep its title."""
    rows = await supabase_db.delete("scam_scenarios", owned_by(id, user.user_id), "id")
    if not rows:
        await _not_owned(id, user.user_id, supabase_db)


def _to_row(values: dict[str, Any]) -> dict[str, Any]:
    if "difficulty" in values:
        values["difficulty"] = DIFFICULTY_LEVELS[values["difficulty"]]
    return values


def _to_scenario(row: dict[str, Any]) -> ScenarioResponse:
    built_in = row["created_by"] is None
    return ScenarioResponse(
        id=row["id"],
        built_in=built_in,
        title=row["title"],
        category=row["category"],
        difficulty=DIFFICULTY_NAMES[row["difficulty"]],
        description=row["description"],
        # Built-in prompts stay private so the app is harder to mine for scam scripts.
        system_prompt=None if built_in else row["system_prompt"],
        red_flags=row["red_flags"],
        max_duration_seconds=row["max_duration_seconds"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _not_owned(scenario_id: UUID, user_id: UUID, supabase_db: SupabaseDatabase) -> NoReturn:
    """Explain why a change matched no row: read-only (built-in) or not found."""
    visible = await supabase_db.select(
        "scam_scenarios", {**visible_to(user_id), "id": f"eq.{scenario_id}"}, "id"
    )
    if visible:
        api_error(
            status.HTTP_403_FORBIDDEN,
            "scenario_read_only",
            "Built-in scenarios cannot be changed or deleted.",
        )
    _not_found()


def _not_found() -> NoReturn:
    api_error(status.HTTP_404_NOT_FOUND, "scenario_not_found", "Scenario not found.")
