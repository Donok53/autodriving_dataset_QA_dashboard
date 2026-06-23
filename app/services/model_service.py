from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = PROJECT_ROOT / "models" / "current"
DEFAULT_MODEL_INFO_PATH = DEFAULT_MODEL_DIR / "model_info.json"
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "student_baseline.joblib"


class ModelUnavailableError(RuntimeError):
    pass


def get_model_info() -> dict[str, Any]:
    info_path = _model_info_path()
    info = _read_model_info(info_path)
    model_path = resolve_model_path(info)
    return {
        **info,
        "model_available": model_path.exists(),
        "model_path": str(model_path),
        "model_info_path": str(info_path),
    }


def resolve_model_path(model_info: dict[str, Any] | None = None) -> Path:
    env_path = os.getenv("MODEL_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()

    raw_model_path = (model_info or {}).get("model_path")
    if raw_model_path:
        candidate = Path(str(raw_model_path)).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        return candidate.resolve()

    return DEFAULT_MODEL_PATH.resolve()


def load_student_model() -> Any:
    model_info = _read_model_info(_model_info_path())
    model_path = resolve_model_path(model_info)
    if not model_path.exists():
        raise ModelUnavailableError(f"student model file not found: {model_path}")

    try:
        import joblib
    except ImportError as exc:
        raise ModelUnavailableError("joblib is required to load the student model") from exc

    return joblib.load(model_path)


def predict_student(payload: dict[str, Any]) -> dict[str, Any]:
    bundle = load_student_model()
    model = bundle.get("model")
    if model is None:
        raise ModelUnavailableError("student model bundle does not contain a model")

    vector = _build_prediction_vector(bundle, payload)
    probabilities = model.predict_proba(vector)[0]
    label_encoder = bundle.get("label_encoder")
    top_index = int(probabilities.argmax())
    if label_encoder is not None:
        prediction = str(label_encoder.inverse_transform([top_index])[0])
    else:
        prediction = str(top_index)

    return {
        "prediction": prediction,
        "confidence": float(probabilities[top_index]),
        "model_info": get_model_info(),
    }


def write_model_info(
    *,
    model_name: str,
    version: str,
    run_id: str,
    accuracy: float | None = None,
    macro_f1: float | None = None,
    status: str = "champion",
    model_path: str = "models/current/student_baseline.joblib",
) -> dict[str, Any]:
    info = {
        "model_name": model_name,
        "version": version,
        "run_id": run_id,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "model_path": model_path,
    }
    path = _model_info_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return info


def _build_prediction_vector(bundle: dict[str, Any], payload: dict[str, Any]) -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise ModelUnavailableError("numpy is required to run prediction") from exc

    if "features" in payload:
        return np.asarray([payload["features"]], dtype=np.float32)

    image_size = int(bundle.get("image_size") or 0)
    if image_size <= 0:
        raise ModelUnavailableError("student model bundle does not define image_size")

    image_feature = payload.get("image_feature")
    if image_feature is None:
        image_feature = [0.0] * (image_size * image_size)
    image_matrix = np.asarray([image_feature], dtype=np.float32)

    vectorizer = bundle.get("vectorizer")
    if vectorizer is None:
        return image_matrix

    context = payload.get("context") or {}
    context_matrix = vectorizer.transform([context]).astype(np.float32)
    return np.concatenate([image_matrix, context_matrix], axis=1)


def _model_info_path() -> Path:
    env_path = os.getenv("MODEL_INFO_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_MODEL_INFO_PATH


def _read_model_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "model_name": "xai_student_model",
            "version": "unpromoted",
            "run_id": "",
            "accuracy": None,
            "macro_f1": None,
            "promoted_at": None,
            "status": "missing",
            "model_path": str(DEFAULT_MODEL_PATH.relative_to(PROJECT_ROOT)),
        }

    with open(path, "r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ModelUnavailableError(f"model info must be a JSON object: {path}")
    return loaded
