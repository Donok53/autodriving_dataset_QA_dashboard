import json
from datetime import datetime, timezone

from app.services.model_registry import (
    INFO_FILENAME,
    MODEL_FILENAME,
    archive_current_model,
    list_model_versions,
    promote_candidate_model,
    rollback_model,
)


def _write_model_set(directory, *, version, payload):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / MODEL_FILENAME).write_bytes(payload)
    (directory / INFO_FILENAME).write_text(
        json.dumps(
            {
                "model_name": "xai_student_model",
                "version": version,
                "run_id": f"run-{version}",
                "status": "candidate",
                "model_path": str(directory / MODEL_FILENAME),
            }
        ),
        encoding="utf-8",
    )


def test_promote_candidate_archives_current_model(tmp_path):
    current = tmp_path / "current"
    versions = tmp_path / "versions"
    candidate = tmp_path / "candidate"
    _write_model_set(current, version="old-v1", payload=b"old")
    _write_model_set(candidate, version="new-v2", payload=b"new")

    result = promote_candidate_model(
        candidate,
        current_dir=current,
        versions_dir=versions,
        now=datetime(2026, 6, 23, tzinfo=timezone.utc),
    )

    assert result["status"] == "promoted"
    assert (current / MODEL_FILENAME).read_bytes() == b"new"
    current_info = json.loads((current / INFO_FILENAME).read_text(encoding="utf-8"))
    assert current_info["version"] == "new-v2"
    assert current_info["status"] == "champion"
    assert current_info["promoted_at"] == "2026-06-23T00:00:00+00:00"
    archives = list_model_versions(versions)
    assert len(archives) == 1
    assert (archives[0] / MODEL_FILENAME).read_bytes() == b"old"
    assert (archives[0] / "rollback_info.json").exists()


def test_rollback_restores_archived_model_and_archives_replaced_current(tmp_path):
    current = tmp_path / "current"
    versions = tmp_path / "versions"
    _write_model_set(current, version="new-v2", payload=b"new")
    old_archive = archive_current_model(
        current_dir=current,
        versions_dir=versions,
        now=datetime(2026, 6, 23, 1, 0, tzinfo=timezone.utc),
    )
    _write_model_set(current, version="new-v3", payload=b"newer")

    result = rollback_model(
        old_archive,
        current_dir=current,
        versions_dir=versions,
        now=datetime(2026, 6, 23, 2, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "rolled_back"
    assert (current / MODEL_FILENAME).read_bytes() == b"new"
    current_info = json.loads((current / INFO_FILENAME).read_text(encoding="utf-8"))
    assert current_info["version"] == "new-v2"
    assert current_info["status"] == "champion"
    assert current_info["rolled_back_at"] == "2026-06-23T02:00:00+00:00"
    assert len(list_model_versions(versions)) == 2
