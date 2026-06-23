import json

from app.services import model_service


def test_get_model_info_reads_current_metadata(tmp_path, monkeypatch):
    model_file = tmp_path / "student_baseline.joblib"
    model_file.write_bytes(b"placeholder")
    info_path = tmp_path / "model_info.json"
    info_path.write_text(
        json.dumps(
            {
                "model_name": "xai_student_model",
                "version": "v2",
                "run_id": "run-2",
                "accuracy": 0.91,
                "macro_f1": 0.88,
                "promoted_at": "2026-06-23T00:00:00+00:00",
                "status": "champion",
                "model_path": str(model_file),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_INFO_PATH", str(info_path))
    monkeypatch.delenv("MODEL_PATH", raising=False)

    info = model_service.get_model_info()

    assert info["model_name"] == "xai_student_model"
    assert info["version"] == "v2"
    assert info["status"] == "champion"
    assert info["model_available"] is True
    assert info["model_path"] == str(model_file.resolve())


def test_get_model_info_marks_missing_model(tmp_path, monkeypatch):
    info_path = tmp_path / "model_info.json"
    info_path.write_text(
        json.dumps(
            {
                "model_name": "xai_student_model",
                "version": "v3",
                "run_id": "run-3",
                "status": "candidate",
                "model_path": str(tmp_path / "missing.joblib"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_INFO_PATH", str(info_path))
    monkeypatch.delenv("MODEL_PATH", raising=False)

    info = model_service.get_model_info()

    assert info["version"] == "v3"
    assert info["model_available"] is False
