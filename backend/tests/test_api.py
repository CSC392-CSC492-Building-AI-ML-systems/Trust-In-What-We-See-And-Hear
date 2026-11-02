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


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Configured API")
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = Settings(_env_file=None)
    assert settings.app_name == "Configured API"
    assert settings.environment == "test"
