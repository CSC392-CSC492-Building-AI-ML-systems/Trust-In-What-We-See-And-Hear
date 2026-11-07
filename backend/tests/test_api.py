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
