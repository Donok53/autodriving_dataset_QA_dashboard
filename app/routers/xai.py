from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.model_service import ModelUnavailableError, get_model_info, predict_student
from app.services.xai_log_analyzer import (
    InvalidXaiLogError,
    analyze_sample_xai_log,
    analyze_xai_log,
)

router = APIRouter(prefix="/api/xai", tags=["xai"])


@router.get("/health")
def xai_health() -> dict[str, str]:
    return {"status": "ok", "component": "xai"}


@router.get("/model-info")
def model_info() -> dict[str, Any]:
    return get_model_info()


@router.get("/sample-result")
def sample_result() -> dict[str, Any]:
    return analyze_sample_xai_log()


@router.post("/log-summary")
def log_summary(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    try:
        return analyze_xai_log(payload)
    except InvalidXaiLogError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/predict")
def predict(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return predict_student(payload)
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
