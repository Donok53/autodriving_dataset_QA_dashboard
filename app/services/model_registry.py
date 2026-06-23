from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = PROJECT_ROOT / "models"
CURRENT_MODEL_DIR = MODEL_ROOT / "current"
CANDIDATE_MODEL_DIR = MODEL_ROOT / "candidates"
VERSION_MODEL_DIR = MODEL_ROOT / "versions"
MODEL_FILENAME = "student_baseline.joblib"
INFO_FILENAME = "model_info.json"


class ModelRegistryError(RuntimeError):
    pass


def read_model_info(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ModelRegistryError(f"model info must be a JSON object: {path}")
    return loaded


def write_model_info(path: Path, info: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_model_set(directory: Path) -> tuple[Path, Path]:
    model_path = directory / MODEL_FILENAME
    info_path = directory / INFO_FILENAME
    missing = [str(path) for path in (model_path, info_path) if not path.exists()]
    if missing:
        raise ModelRegistryError(f"model set is incomplete: {', '.join(missing)}")
    return model_path, info_path


def archive_current_model(
    *,
    current_dir: Path | None = None,
    versions_dir: Path | None = None,
    reason: str = "promotion",
    now: datetime | None = None,
) -> Path | None:
    current_dir = Path(current_dir or CURRENT_MODEL_DIR)
    versions_dir = Path(versions_dir or VERSION_MODEL_DIR)
    current_model = current_dir / MODEL_FILENAME
    current_info = current_dir / INFO_FILENAME
    if not current_model.exists() or not current_info.exists():
        return None

    info = read_model_info(current_info)
    timestamp = _timestamp_for_path(now)
    version = _slug(str(info.get("version") or "unknown"))
    archive_dir = _unique_dir(versions_dir / f"{timestamp}-{version}")
    archive_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(current_model, archive_dir / MODEL_FILENAME)
    shutil.copy2(current_info, archive_dir / INFO_FILENAME)
    write_model_info(
        archive_dir / "rollback_info.json",
        {
            "archived_at": _iso_now(now),
            "archive_reason": reason,
            "source_version": info.get("version"),
            "source_run_id": info.get("run_id"),
            "source_model_path": _display_path(current_model),
        },
    )
    return archive_dir


def promote_candidate_model(
    candidate_dir: Path,
    *,
    current_dir: Path | None = None,
    versions_dir: Path | None = None,
    status: str = "champion",
    archive_current: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    candidate_dir = Path(candidate_dir).expanduser().resolve()
    current_dir = Path(current_dir or CURRENT_MODEL_DIR)
    versions_dir = Path(versions_dir or VERSION_MODEL_DIR)
    candidate_model, candidate_info = require_model_set(candidate_dir)

    archived_to = None
    if archive_current:
        archived_to = archive_current_model(
            current_dir=current_dir,
            versions_dir=versions_dir,
            reason="promotion",
            now=now,
        )

    current_dir.mkdir(parents=True, exist_ok=True)
    current_model = current_dir / MODEL_FILENAME
    current_info = current_dir / INFO_FILENAME
    shutil.copy2(candidate_model, current_model)

    info = read_model_info(candidate_info)
    info.update(
        {
            "status": status,
            "promoted_at": _iso_now(now),
            "model_path": _display_path(current_model),
        }
    )
    if archived_to is not None:
        info["previous_model_dir"] = _display_path(archived_to)
    write_model_info(current_info, info)

    return {
        "status": "promoted",
        "candidate_dir": _display_path(candidate_dir),
        "current_model_info": info,
        "archived_previous_model_to": _display_path(archived_to) if archived_to else None,
    }


def rollback_model(
    version_dir: Path,
    *,
    current_dir: Path | None = None,
    versions_dir: Path | None = None,
    archive_current: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    version_dir = Path(version_dir).expanduser().resolve()
    source_model, source_info = require_model_set(version_dir)
    current_dir = Path(current_dir or CURRENT_MODEL_DIR)
    versions_dir = Path(versions_dir or VERSION_MODEL_DIR)

    archived_to = None
    if archive_current:
        archived_to = archive_current_model(
            current_dir=current_dir,
            versions_dir=versions_dir,
            reason="pre_rollback",
            now=now,
        )

    current_dir.mkdir(parents=True, exist_ok=True)
    current_model = current_dir / MODEL_FILENAME
    current_info = current_dir / INFO_FILENAME
    shutil.copy2(source_model, current_model)

    info = read_model_info(source_info)
    info.update(
        {
            "status": "champion",
            "rolled_back_at": _iso_now(now),
            "rollback_source": _display_path(version_dir),
            "model_path": _display_path(current_model),
        }
    )
    if archived_to is not None:
        info["pre_rollback_model_dir"] = _display_path(archived_to)
    write_model_info(current_info, info)

    return {
        "status": "rolled_back",
        "rollback_source": _display_path(version_dir),
        "current_model_info": info,
        "archived_replaced_model_to": _display_path(archived_to) if archived_to else None,
    }


def list_model_versions(versions_dir: Path | None = None) -> list[Path]:
    versions_dir = Path(versions_dir or VERSION_MODEL_DIR)
    if not versions_dir.exists():
        return []
    return sorted(
        [path for path in versions_dir.iterdir() if path.is_dir() and (path / MODEL_FILENAME).exists()],
        reverse=True,
    )


def latest_model_version(versions_dir: Path | None = None) -> Path:
    versions = list_model_versions(versions_dir)
    if not versions:
        raise ModelRegistryError("rollback 가능한 이전 모델이 없습니다.")
    return versions[0]


def _iso_now(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _timestamp_for_path(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return slug or "model"


def _unique_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise ModelRegistryError(f"unique version directory could not be created: {path}")


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    path = Path(path).resolve()
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)
