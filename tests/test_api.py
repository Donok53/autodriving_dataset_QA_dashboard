from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sample_analysis_api_returns_summary():
    response = client.get("/api/sample-analysis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] > 0
    assert payload["quality_score"] <= 100
    assert len(payload["events"]) > 0


def test_dashboard_renders_html():
    response = client.get("/")

    assert response.status_code == 200
    assert "자율주행 센서 로그 품질 대시보드" in response.text
    assert "QA Score" in response.text
