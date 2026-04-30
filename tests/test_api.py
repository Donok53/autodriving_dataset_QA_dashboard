import asyncio
import importlib

from fastapi import HTTPException
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
    assert "upload-progress.js?v=4" in response.text
    assert "analysis-progress-panel" in response.text
    assert 'data-max-upload-bytes="10737418240"' in response.text
    assert 'data-max-upload-label="10GB"' in response.text
    assert "sample_sensor_log.csv" not in response.text
    assert "QA Score" in response.text
    assert "업로드 대기" in response.text
    assert "전체 이벤트" in response.text
    assert "품질 지표" in response.text
    assert "데이터 동기화" in response.text
    assert "주행 이벤트" in response.text
    assert "이상 구간" in response.text


def test_local_dashboard_skips_single_file_upload_limit():
    response = client.get("/", headers={"host": "127.0.0.1:8088"})

    assert response.status_code == 200
    assert 'data-max-upload-bytes=""' in response.text
    assert 'data-max-upload-label="제한 없음"' in response.text


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


def test_local_raw_upload_skips_file_size_limit(monkeypatch):
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 5)
    monkeypatch.setattr(main_module, "MAX_UPLOAD_SIZE_LABEL", "5B")

    with main_module._upload_reservation_lock:
        main_module._upload_reservations.clear()

    with open("data/sample_sensor_log.csv", "rb") as file:
        response = client.post(
            "/api/upload/raw",
            content=file.read(),
            headers={
                "host": "127.0.0.1:8088",
                "content-type": "application/octet-stream",
                "x-filename": "sample_sensor_log.csv",
            },
        )

    assert response.status_code == 200


def test_interrupted_upload_cleans_temp_file_and_marks_job_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "UPLOAD_TEMP_DIR", tmp_path)
    with main_module._upload_reservation_lock:
        main_module._upload_reservations.clear()

    job = main_module.create_job("interrupted.bag", "bag")

    async def run_interrupted_upload():
        read_count = 0

        async def read_next_chunk():
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                return b"partial upload"
            raise RuntimeError("client disconnected")

        await main_module._write_chunks_to_temp_file(read_next_chunk, ".bag", job.job_id)

    try:
        asyncio.run(run_interrupted_upload())
    except HTTPException as exc:
        assert exc.status_code == 499
        assert exc.detail == "업로드가 중단되었습니다. 다시 시도해주세요."
    else:
        raise AssertionError("interrupted upload should fail")

    updated_job = main_module.get_job(job.job_id)
    assert updated_job is not None
    assert updated_job.status == "failed"
    assert updated_job.stage == "업로드 실패"
    assert updated_job.error == "업로드가 중단되었습니다. 다시 시도해주세요."
    assert list(tmp_path.iterdir()) == []
    assert main_module._upload_reservations == {}


def test_startup_cleanup_removes_abandoned_upload_files(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "UPLOAD_TEMP_DIR", tmp_path)
    abandoned_file = tmp_path / "sensor-qa-upload-old.bag"
    unrelated_file = tmp_path / "keep-me.bag"
    abandoned_file.write_bytes(b"stale")
    unrelated_file.write_bytes(b"keep")

    main_module._cleanup_abandoned_upload_files()

    assert not abandoned_file.exists()
    assert unrelated_file.exists()


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


def test_unexpected_analysis_failure_reports_issue(tmp_path, monkeypatch):
    job = main_module.create_job("private_dataset.csv", "csv")
    temp_path = tmp_path / "input.csv"
    temp_path.write_text("broken", encoding="utf-8")

    def raise_unexpected_error(path):
        raise RuntimeError("unexpected parser crash")

    captured = {}
    monkeypatch.setattr(main_module, "analyze_csv", raise_unexpected_error)
    monkeypatch.setattr(
        main_module,
        "report_unexpected_error",
        lambda exc, context: captured.update({"exc": exc, "context": context}) or None,
    )

    main_module._run_analysis_job(job.job_id, temp_path, ".csv")

    updated_job = main_module.get_job(job.job_id)
    assert updated_job is not None
    assert updated_job.status == "failed"
    assert updated_job.stage == "분석 실패"
    assert "예상하지 못한 오류" in (updated_job.error or "")
    assert not temp_path.exists()
    assert isinstance(captured["exc"], RuntimeError)
    assert captured["context"] == {
        "job_id": job.job_id,
        "source_type": "csv",
        "stage": "analysis_job",
    }
