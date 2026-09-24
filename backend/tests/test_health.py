from fastapi.testclient import TestClient

from codehound.main import app


def test_health_contract():
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "codehound"}


def test_database_readiness():
    with TestClient(app) as client:
        response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
