"""Tests for the friend endpoints.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from uuid import UUID

import httpx
import pytest

from supabase_mock import body, postgres_error, rows, user_row

ME = UUID(int=1)  # matches the test_user fixture
FRIEND = UUID(int=2)
REQUEST_ID = UUID(int=100)

ME_ROW = user_row(ME, "alex_morgan")
FRIEND_ROW = user_row(FRIEND, "sam_lee", verified=True)
FRIEND_PUBLIC = {
    "user_id": str(FRIEND),
    "username": "sam_lee",
    "first_name": "Sam",
    "last_name": "Lee",
    "verified": True,
}


def friendship(requester, addressee, status="pending", accepted_at=None):
    rows_by_id = {ME: ME_ROW, FRIEND: FRIEND_ROW}
    return {
        "id": str(REQUEST_ID),
        "status": status,
        "created_at": "2026-11-08T10:00:00+00:00",
        "accepted_at": accepted_at,
        "requester_id": str(requester),
        "addressee_id": str(addressee),
        "requester": rows_by_id[requester],
        "addressee": rows_by_id[addressee],
    }


# POST /friend-requests


def test_send_friend_request(db_client, db):
    db.on("GET", "users", rows(ME_ROW, FRIEND_ROW))
    db.on("GET", "friendships", rows())
    db.on("POST", "friendships", rows(friendship(ME, FRIEND), status=201))

    response = db_client.post("/api/v1/friend-requests", json={"username": "sam_lee"})

    assert response.status_code == 201
    assert response.json() == {
        "id": str(REQUEST_ID),
        "direction": "outgoing",
        "user": FRIEND_PUBLIC,
        "created_at": "2026-11-08T10:00:00Z",
    }
    lookup = db.sent("GET", "users")[0]
    assert lookup.url.params["or"] == f"(id.eq.{ME},username.eq.sam_lee)"
    # The requester comes from the token.
    assert body(db.sent("POST", "friendships")[0]) == {
        "requester_id": str(ME),
        "addressee_id": str(FRIEND),
    }


def test_friend_request_responses_never_include_onboarding_answers(db_client, db):
    leaky = {**FRIEND_ROW, "onboarding_answers": {"pet_name": "Max"}}
    db.on("GET", "users", rows(ME_ROW, leaky))
    db.on("GET", "friendships", rows())
    created = {**friendship(ME, FRIEND), "addressee": leaky}
    db.on("POST", "friendships", rows(created, status=201))

    response = db_client.post("/api/v1/friend-requests", json={"username": "sam_lee"})

    assert "onboarding_answers" not in response.text
    assert "onboarding_answers" not in db.sent("GET", "users")[0].url.params["select"]


def test_friend_request_without_profile_is_409(db_client, db):
    db.on("GET", "users", rows(FRIEND_ROW))
    response = db_client.post("/api/v1/friend-requests", json={"username": "sam_lee"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "profile_required"


def test_friend_request_to_unknown_user_is_404(db_client, db):
    db.on("GET", "users", rows(ME_ROW))
    response = db_client.post("/api/v1/friend-requests", json={"username": "nobody"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "user_not_found"


def test_friend_request_to_self_is_400(db_client, db):
    db.on("GET", "users", rows(ME_ROW))
    response = db_client.post("/api/v1/friend-requests", json={"username": "alex_morgan"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "cannot_friend_self"


@pytest.mark.parametrize(
    ("existing", "code"),
    [
        ({"status": "accepted", "requester_id": str(FRIEND)}, "already_friends"),
        ({"status": "pending", "requester_id": str(ME)}, "friend_request_exists"),
        ({"status": "pending", "requester_id": str(FRIEND)}, "incoming_friend_request"),
    ],
)
def test_friend_request_conflicts(db_client, db, existing, code):
    db.on("GET", "users", rows(ME_ROW, FRIEND_ROW))
    db.on("GET", "friendships", rows(existing))
    response = db_client.post("/api/v1/friend-requests", json={"username": "sam_lee"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code


def test_friend_request_race_is_409(db_client, db):
    db.on("GET", "users", rows(ME_ROW, FRIEND_ROW))
    db.on("GET", "friendships", rows())
    db.on("POST", "friendships", postgres_error("23505", "friendships_pair_idx"))
    response = db_client.post("/api/v1/friend-requests", json={"username": "sam_lee"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "friend_request_exists"


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "Sam"},
        {"username": "sam_lee,id.eq.x"},  # cannot inject into the PostgREST filter
        {"username": "sam_lee", "requester_id": str(FRIEND)},
        {"recipient_user_id": str(FRIEND)},
    ],
)
def test_friend_request_rejects_invalid_bodies(db_client, db, payload):
    assert db_client.post("/api/v1/friend-requests", json=payload).status_code == 422


# GET /friend-requests


def test_list_friend_requests(db_client, db):
    db.on("GET", "friendships", rows(friendship(FRIEND, ME), friendship(ME, FRIEND)))

    response = db_client.get("/api/v1/friend-requests?limit=5&offset=10")

    assert response.status_code == 200
    page = response.json()
    assert [item["direction"] for item in page["items"]] == ["incoming", "outgoing"]
    assert all(item["user"] == FRIEND_PUBLIC for item in page["items"])
    assert (page["limit"], page["offset"]) == (5, 10)
    params = db.sent("GET", "friendships")[0].url.params
    assert params["or"] == f"(requester_id.eq.{ME},addressee_id.eq.{ME})"
    assert params["status"] == "eq.pending"
    assert (params["limit"], params["offset"]) == ("5", "10")


@pytest.mark.parametrize(
    ("direction", "column"), [("incoming", "addressee_id"), ("outgoing", "requester_id")]
)
def test_list_friend_requests_by_direction(db_client, db, direction, column):
    db.on("GET", "friendships", rows())
    response = db_client.get(f"/api/v1/friend-requests?direction={direction}")
    assert response.status_code == 200
    params = db.sent("GET", "friendships")[0].url.params
    assert params[column] == f"eq.{ME}"
    assert "or" not in params


# POST /friend-requests/{id}/accept and /decline


def test_accept_friend_request(db_client, db):
    accepted = friendship(FRIEND, ME, "accepted", "2026-11-09T10:00:00+00:00")
    db.on("PATCH", "friendships", rows(accepted))

    response = db_client.post(f"/api/v1/friend-requests/{REQUEST_ID}/accept")

    assert response.status_code == 200
    assert response.json() == {**FRIEND_PUBLIC, "friends_since": "2026-11-09T10:00:00Z"}
    request = db.sent("PATCH", "friendships")[0]
    # Only the addressee can accept, and only a pending request.
    assert request.url.params["id"] == f"eq.{REQUEST_ID}"
    assert request.url.params["addressee_id"] == f"eq.{ME}"
    assert request.url.params["status"] == "eq.pending"
    assert body(request)["status"] == "accepted"


def test_accepting_someone_elses_request_is_404(db_client, db):
    db.on("PATCH", "friendships", rows())
    response = db_client.post(f"/api/v1/friend-requests/{REQUEST_ID}/accept")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "friend_request_not_found"


def test_decline_friend_request_deletes_it(db_client, db):
    db.on("DELETE", "friendships", rows({"id": str(REQUEST_ID)}))
    response = db_client.post(f"/api/v1/friend-requests/{REQUEST_ID}/decline")
    assert response.status_code == 204
    params = db.sent("DELETE", "friendships")[0].url.params
    # Only this request, only if it was sent to the current user, only while pending.
    assert params["id"] == f"eq.{REQUEST_ID}"
    assert params["addressee_id"] == f"eq.{ME}"
    assert params["status"] == "eq.pending"


def test_declining_unknown_request_is_404(db_client, db):
    db.on("DELETE", "friendships", rows())
    response = db_client.post(f"/api/v1/friend-requests/{REQUEST_ID}/decline")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "friend_request_not_found"


# GET /friends and DELETE /friends/{user_id}


@pytest.mark.parametrize(("requester", "addressee"), [(ME, FRIEND), (FRIEND, ME)])
def test_list_friends(db_client, db, requester, addressee):
    db.on(
        "GET",
        "friendships",
        rows(friendship(requester, addressee, "accepted", "2026-11-09T10:00:00+00:00")),
    )
    response = db_client.get("/api/v1/friends")
    assert response.status_code == 200
    # The other user is listed, whichever side sent the original request.
    assert response.json()["items"] == [
        {**FRIEND_PUBLIC, "friends_since": "2026-11-09T10:00:00Z"}
    ]
    params = db.sent("GET", "friendships")[0].url.params
    # Only the current user's friendships.
    assert params["or"] == f"(requester_id.eq.{ME},addressee_id.eq.{ME})"
    assert params["status"] == "eq.accepted"


def test_remove_friend(db_client, db):
    # Consents are deleted by the database with the friendship (on delete cascade).
    db.on("DELETE", "friendships", rows({"id": str(REQUEST_ID)}))

    response = db_client.delete(f"/api/v1/friends/{FRIEND}")

    assert response.status_code == 204
    params = db.sent("DELETE", "friendships")[0].url.params
    assert params["or"] == (
        f"(and(requester_id.eq.{ME},addressee_id.eq.{FRIEND}),"
        f"and(requester_id.eq.{FRIEND},addressee_id.eq.{ME}))"
    )
    assert params["status"] == "eq.accepted"


def test_remove_non_friend_is_404(db_client, db):
    db.on("DELETE", "friendships", rows())
    response = db_client.delete(f"/api/v1/friends/{FRIEND}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "friend_not_found"


def test_database_outage_is_503(db_client, db):
    db.on("GET", "friendships", httpx.Response(503))
    response = db_client.get("/api/v1/friends")
    assert response.status_code == 503
    assert response.json() == {"detail": "A backend service is unavailable. Try again later."}
