"""Tests for the scenario endpoints.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from uuid import UUID

import pytest

from supabase_mock import body, postgres_error, rows

ME = UUID(int=1)  # matches the test_user fixture
SCENARIO_ID = UUID(int=300)
VISIBLE = f"(and(created_by.is.null,is_active.is.true),created_by.eq.{ME})"

NEW_SCENARIO = {
    "title": "Bank fraud alert",
    "category": "bank_fraud_alert",
    "difficulty": "hard",
    "description": "Your bank says your card was used abroad.",
    "system_prompt": "You are a bank fraud agent...",
    "red_flags": ["urgency", "asks for PIN"],
}


def scenario_row(created_by=ME, **changes):
    return {
        "id": str(SCENARIO_ID),
        "created_by": None if created_by is None else str(created_by),
        "title": "Bank fraud alert",
        "category": "bank_fraud_alert",
        "difficulty": 3,
        "description": "Your bank says your card was used abroad.",
        "system_prompt": "You are a bank fraud agent...",
        "red_flags": ["urgency", "asks for PIN"],
        "max_duration_seconds": 300,
        "created_at": "2026-11-10T12:00:00+00:00",
        "updated_at": "2026-11-10T12:00:00+00:00",
        **changes,
    }


# POST /scenarios


def test_add_scenario(db_client, db):
    db.on("POST", "scam_scenarios", rows(scenario_row(), status=201))

    response = db_client.post("/api/v1/scenarios", json=NEW_SCENARIO)

    assert response.status_code == 201
    data = response.json()
    assert data["built_in"] is False
    assert data["difficulty"] == "hard"
    assert data["system_prompt"] == "You are a bank fraud agent..."
    assert body(db.sent("POST", "scam_scenarios")[0]) == {
        **NEW_SCENARIO,
        "difficulty": 3,
        "max_duration_seconds": 300,
        "created_by": str(ME),  # always the current user
    }


def test_add_scenario_without_profile_is_409(db_client, db):
    db.on("POST", "scam_scenarios", postgres_error("23503", "scam_scenarios_created_by_fkey"))
    response = db_client.post("/api/v1/scenarios", json=NEW_SCENARIO)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "profile_required"


@pytest.mark.parametrize(
    "changes",
    [
        {"difficulty": 2},
        {"difficulty": "extreme"},
        {"category": "crypto"},
        {"title": "  "},
        {"red_flags": ["x"] * 21},
        {"max_duration_seconds": 10},
        {"created_by": None},  # cannot create built-in scenarios
        {"content": {}},
    ],
)
def test_add_scenario_rejects_invalid_bodies(db_client, db, changes):
    response = db_client.post("/api/v1/scenarios", json={**NEW_SCENARIO, **changes})
    assert response.status_code == 422


# GET /scenarios and /scenarios/{id}


def test_list_scenarios_hides_built_in_prompts(db_client, db):
    db.on("GET", "scam_scenarios", rows(scenario_row(created_by=None), scenario_row()))

    response = db_client.get("/api/v1/scenarios")

    assert response.status_code == 200
    built_in, own = response.json()["items"]
    assert (built_in["built_in"], built_in["system_prompt"]) == (True, None)
    assert (own["built_in"], own["system_prompt"]) == (False, "You are a bank fraud agent...")
    assert db.sent("GET", "scam_scenarios")[0].url.params["or"] == VISIBLE


@pytest.mark.parametrize(
    ("created_by", "prompt"), [(ME, "You are a bank fraud agent..."), (None, None)]
)
def test_get_scenario(db_client, db, created_by, prompt):
    db.on("GET", "scam_scenarios", rows(scenario_row(created_by=created_by)))
    response = db_client.get(f"/api/v1/scenarios/{SCENARIO_ID}")
    assert response.status_code == 200
    assert response.json()["system_prompt"] == prompt
    params = db.sent("GET", "scam_scenarios")[0].url.params
    assert params["id"] == f"eq.{SCENARIO_ID}"
    assert params["or"] == VISIBLE


def test_get_hidden_scenario_is_404(db_client, db):
    db.on("GET", "scam_scenarios", rows())
    response = db_client.get(f"/api/v1/scenarios/{SCENARIO_ID}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "scenario_not_found"


# PUT and DELETE /scenarios/{id}


def test_update_own_scenario(db_client, db):
    db.on("PATCH", "scam_scenarios", rows(scenario_row(difficulty=1)))

    response = db_client.put(f"/api/v1/scenarios/{SCENARIO_ID}", json={"difficulty": "easy"})

    assert response.status_code == 200
    assert response.json()["difficulty"] == "easy"
    request = db.sent("PATCH", "scam_scenarios")[0]
    assert request.url.params["created_by"] == f"eq.{ME}"
    assert body(request) == {"difficulty": 1}


@pytest.mark.parametrize(("visible", "status", "code"), [
    (rows({"id": str(SCENARIO_ID)}), 403, "scenario_read_only"),
    (rows(), 404, "scenario_not_found"),
])
def test_update_scenario_not_owned(db_client, db, visible, status, code):
    db.on("PATCH", "scam_scenarios", rows())
    db.on("GET", "scam_scenarios", visible)
    response = db_client.put(f"/api/v1/scenarios/{SCENARIO_ID}", json={"title": "Mine now"})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_empty_update_returns_the_scenario_unchanged(db_client, db):
    db.on("GET", "scam_scenarios", rows(scenario_row()))
    response = db_client.put(f"/api/v1/scenarios/{SCENARIO_ID}", json={})
    assert response.status_code == 200
    assert response.json()["title"] == "Bank fraud alert"
    assert db.sent("GET", "scam_scenarios")[0].url.params["or"] == VISIBLE


@pytest.mark.parametrize("payload", [{"title": None}, {"created_by": str(ME)}])
def test_update_scenario_rejects_invalid_bodies(db_client, db, payload):
    response = db_client.put(f"/api/v1/scenarios/{SCENARIO_ID}", json=payload)
    assert response.status_code == 422


def test_delete_own_scenario(db_client, db):
    db.on("DELETE", "scam_scenarios", rows({"id": str(SCENARIO_ID)}))
    response = db_client.delete(f"/api/v1/scenarios/{SCENARIO_ID}")
    assert response.status_code == 204
    assert db.sent("DELETE", "scam_scenarios")[0].url.params["created_by"] == f"eq.{ME}"


def test_delete_built_in_scenario_is_403(db_client, db):
    db.on("DELETE", "scam_scenarios", rows())
    db.on("GET", "scam_scenarios", rows({"id": str(SCENARIO_ID)}))
    response = db_client.delete(f"/api/v1/scenarios/{SCENARIO_ID}")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "scenario_read_only"
