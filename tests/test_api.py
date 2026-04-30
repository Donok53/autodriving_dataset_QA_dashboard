import importlib

from fastapi.testclient import TestClient

main_module = importlib.import_module("app.main")

client = TestClient(main_module.app)


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
    assert "CSV/BAG 업로드" in response.text
    assert "pagination.js" in response.text
    assert "upload-progress.js?v=3" in response.text
    assert "analysis-progress-panel" in response.text
    assert "sample_sensor_log.csv" not in response.text
    assert "QA Score" not in response.text
    assert "전체 이벤트" not in response.text


def test_sample_dashboard_renders_analysis_result():
    response = client.get("/sample")

    assert response.status_code == 200
    assert "sample_sensor_log.csv" in response.text
    assert "QA Score" in response.text
    assert "전체 이벤트" in response.text
    assert 'data-page-size="5"' in response.text
    assert 'data-page-size="10"' in response.text


def test_invalid_bag_upload_returns_dashboard_error():
    response = client.post(
        "/upload",
        files={"file": ("broken.bag", b"not a real bag", "application/octet-stream")},
    )

    assert response.status_code == 200
    assert "bag 파일을 읽을 수 없습니다" in response.text


def test_async_csv_upload_job_completes_and_renders_result():
    sample_csv = client.get("/api/sample-analysis")
    assert sample_csv.status_code == 200

    with open("data/sample_sensor_log.csv", "rb") as file:
        response = client.post(
            "/api/upload",
            files={"file": ("sample_sensor_log.csv", file, "text/csv")},
        )

    assert response.status_code == 200
    job = response.json()
    assert job["job_id"]
    assert job["source_type"] == "csv"

    status_response = client.get(f"/api/jobs/{job['job_id']}")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "completed"
    assert status_payload["progress"] == 100

    result_response = client.get(f"/jobs/{job['job_id']}")
    assert result_response.status_code == 200
    assert "sample_sensor_log.csv" in result_response.text


def test_raw_csv_upload_job_completes_and_renders_result():
    with open("data/sample_sensor_log.csv", "rb") as file:
        response = client.post(
            "/api/upload/raw",
            content=file.read(),
            headers={
                "content-type": "application/octet-stream",
                "x-filename": "sample_sensor_log.csv",
            },
        )

    assert response.status_code == 200
    job = response.json()
    assert job["job_id"]
    assert job["source_type"] == "csv"

    status_response = client.get(f"/api/jobs/{job['job_id']}")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "completed"
    assert status_payload["progress"] == 100

    result_response = client.get(f"/jobs/{job['job_id']}")
    assert result_response.status_code == 200
    assert "sample_sensor_log.csv" in result_response.text


def test_raw_upload_rejects_file_over_size_limit(monkeypatch):
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 5)
    monkeypatch.setattr(main_module, "MAX_UPLOAD_SIZE_LABEL", "5B")

    response = client.post(
        "/api/upload/raw",
        content=b"123456",
        headers={
            "content-type": "application/octet-stream",
            "x-filename": "too-large.bag",
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "파일은 5B 이하만 업로드할 수 있습니다."


def test_raw_upload_rejects_when_active_storage_pool_is_full(monkeypatch):
    with main_module._upload_reservation_lock:
        main_module._upload_reservations.clear()

    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 20)
    monkeypatch.setattr(main_module, "MAX_ACTIVE_UPLOAD_BYTES", 5)
    monkeypatch.setattr(main_module, "MAX_ACTIVE_UPLOAD_SIZE_LABEL", "5B")

    response = client.post(
        "/api/upload/raw",
        content=b"123456",
        headers={
            "content-type": "application/octet-stream",
            "x-filename": "too-large-pool.bag",
        },
    )

    assert response.status_code == 507
    assert response.json()["detail"] == "동시 업로드 저장 한도 5B를 초과했습니다. 다른 분석이 끝난 뒤 다시 시도해주세요."


def test_async_upload_rejects_unsupported_file_type():
    response = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "CSV 또는 BAG 파일만 업로드할 수 있습니다."
