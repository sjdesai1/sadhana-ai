from fastapi.testclient import TestClient

from sadhana.api.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_office_economics() -> None:
    assert "solo_manhattan" in client.get("/offices").json()
    resp = client.get("/offices/solo_manhattan/economics")
    assert resp.status_code == 200
    assert len(resp.json()["procedures"]) > 30


def test_unknown_office() -> None:
    assert client.get("/offices/nope/economics").status_code == 404
