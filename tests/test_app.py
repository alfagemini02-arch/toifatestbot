from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_uptime_head_root() -> None:
    with TestClient(app) as client:
        response = client.head('/')
    assert response.status_code == 200
    assert response.content == b''


def test_uptime_head_health() -> None:
    with TestClient(app) as client:
        response = client.head('/health')
    assert response.status_code == 200
