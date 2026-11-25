from app.core.config import Settings


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_docs(client):
    assert client.get("/docs").status_code == 200


def test_openapi(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Test API"
    assert schema["paths"]["/api/v1/users/me"]["get"]["security"]


def test_openapi_documents_tags_and_errors(client):
    schema = client.get("/openapi.json").json()
    operation_tags = {
        tag
        for operations in schema["paths"].values()
        for operation in operations.values()
        for tag in operation.get("tags", [])
    }
    assert operation_tags <= {tag["name"] for tag in schema["tags"]}

    protected = schema["paths"]["/api/v1/users/me"]["get"]["responses"]
    assert "401" in protected
    assert "503" in protected
    assert "security" not in schema["paths"]["/health"]["get"]
    assert "404" in schema["paths"]["/api/v1/calls/{id}"]["get"]["responses"]


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Configured API")
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = Settings(_env_file=None)
    assert settings.app_name == "Configured API"
    assert settings.environment == "test"


def test_cors_preflight_allows_frontend_origin(client):
    response = client.options(
        "/api/v1/users/me",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_cors_rejects_unknown_origin(client):
    response = client.options(
        "/api/v1/users/me",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_origins_read_environment(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["http://localhost:4000"]')
    assert Settings(_env_file=None).cors_allow_origins == ["http://localhost:4000"]


def test_cors_headers_on_body_size_rejection(client):
    # CORS is the outermost middleware, so a browser can read the 413 instead of a CORS error.
    response = client.put(
        "/api/v1/users/me/voice",
        headers={"Origin": "http://localhost:3000", "Content-Length": str(1024**3)},
        content=b"",
    )
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
