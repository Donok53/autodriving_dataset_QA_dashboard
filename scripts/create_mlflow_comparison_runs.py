from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

import joblib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.model_registry import CURRENT_MODEL_DIR, INFO_FILENAME, MODEL_FILENAME, read_model_info


DEFAULT_EXPERIMENT = "xai-vlm-dashboard"
DEFAULT_REGISTERED_MODEL = "xai_student_model"
DEFAULT_CANDIDATES = [
    {
        "version": "outdoor-rich-v2-full",
        "run_name": "outdoor-rich-v2-full",
        "image_size": "48",
        "seed": "53",
        "c": "1.0",
        "max_iter": "1000",
    },
    {
        "version": "outdoor-rich-v2-lite",
        "run_name": "outdoor-rich-v2-lite",
        "image_size": "32",
        "seed": "54",
        "c": "0.35",
        "max_iter": "700",
    },
    {
        "version": "outdoor-rich-v2-regularized",
        "run_name": "outdoor-rich-v2-regularized",
        "image_size": "24",
        "seed": "55",
        "c": "0.08",
        "max_iter": "500",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create MLflow baseline and candidate runs for report screenshots."
    )
    parser.add_argument(
        "--tracking-uri",
        default=None,
        help="MLflow tracking URI. Defaults to MLFLOW_TRACKING_URI or http://127.0.0.1:5000.",
    )
    parser.add_argument("--experiment-name", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--registered-model-name", default=DEFAULT_REGISTERED_MODEL)
    parser.add_argument("--force", action="store_true", help="Create runs even when a version tag already exists.")
    parser.add_argument("--skip-candidates", action="store_true", help="Only import the current champion baseline.")
    args = parser.parse_args()

    try:
        import mlflow
    except ImportError as exc:
        raise SystemExit(
            "MLflow가 설치되어 있지 않습니다. `pip install -r requirements-mlops.txt` 후 다시 실행해 주세요."
        ) from exc

    tracking_uri = args.tracking_uri or os.getenv("MLFLOW_TRACKING_URI") or "http://127.0.0.1:5000"
    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.set_experiment(args.experiment_name)
    experiment_id = experiment.experiment_id

    created: list[dict[str, Any]] = []
    baseline = import_current_champion(
        tracking_uri=tracking_uri,
        experiment_name=args.experiment_name,
        experiment_id=experiment_id,
        registered_model_name=args.registered_model_name,
        force=args.force,
    )
    if baseline is not None:
        created.append(baseline)

    if not args.skip_candidates:
        for candidate in DEFAULT_CANDIDATES:
            if version_exists(experiment_id, candidate["version"]) and not args.force:
                created.append({"version": candidate["version"], "status": "skipped_existing"})
                continue
            created.append(
                run_candidate_training(
                    candidate,
                    tracking_uri=tracking_uri,
                    experiment_name=args.experiment_name,
                    registered_model_name=args.registered_model_name,
                )
            )

    print(
        json.dumps(
            {
                "status": "completed",
                "tracking_uri": tracking_uri,
                "experiment_name": args.experiment_name,
                "registered_model_name": args.registered_model_name,
                "runs": created,
                "mlflow_ui": tracking_uri,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def import_current_champion(
    *,
    tracking_uri: str,
    experiment_name: str,
    experiment_id: str,
    registered_model_name: str,
    force: bool,
) -> dict[str, Any] | None:
    try:
        import mlflow
        import mlflow.sklearn
    except ImportError as exc:
        raise SystemExit(
            "MLflow가 설치되어 있지 않습니다. `pip install -r requirements-mlops.txt` 후 다시 실행해 주세요."
        ) from exc

    model_path = CURRENT_MODEL_DIR / MODEL_FILENAME
    info_path = CURRENT_MODEL_DIR / INFO_FILENAME
    info = read_model_info(info_path)
    version = str(info.get("version") or "current-champion")
    if version_exists(experiment_id, version) and not force:
        return {"version": version, "status": "skipped_existing"}

    with mlflow.start_run(run_name=f"{version}-baseline-import") as run:
        run_id = run.info.run_id
        metrics = {
            key: float(info[key])
            for key in ("accuracy", "macro_f1", "weighted_f1", "macro_precision", "macro_recall")
            if isinstance(info.get(key), int | float)
        }
        if metrics:
            mlflow.log_metrics(metrics)
        mlflow.log_params(
            {
                "model_type": "LogisticRegression",
                "source": "models/current",
                "model_path": str(model_path.relative_to(PROJECT_ROOT)),
                "service_status": str(info.get("status") or "champion"),
            }
        )
        mlflow.set_tags(
            {
                "service": "autodriving-dataset-qa-dashboard",
                "model_stage": "baseline_champion",
                "version": version,
                "source_run_id": str(info.get("run_id") or ""),
            }
        )
        mlflow.log_artifacts(str(CURRENT_MODEL_DIR), artifact_path="service_model")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bundle = joblib.load(model_path)
        classifier = bundle.get("model") if isinstance(bundle, dict) else None
        if classifier is not None:
            mlflow.sklearn.log_model(
                classifier,
                artifact_path="classifier",
                registered_model_name=registered_model_name,
            )

    return {
        "version": version,
        "status": "imported_baseline",
        "run_id": run_id,
        "run_url": f"{tracking_uri}/#/experiments/{experiment_id}/runs/{run_id}",
    }


def run_candidate_training(
    candidate: dict[str, str],
    *,
    tracking_uri: str,
    experiment_name: str,
    registered_model_name: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/train_vlm_model.py",
        "--version",
        candidate["version"],
        "--run-name",
        candidate["run_name"],
        "--tracking-uri",
        tracking_uri,
        "--experiment-name",
        experiment_name,
        "--registered-model-name",
        registered_model_name,
        "--image-size",
        candidate["image_size"],
        "--seed",
        candidate["seed"],
        "--c",
        candidate["c"],
        "--max-iter",
        candidate["max_iter"],
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=True, text=True, capture_output=True)
    payload = parse_last_json_object(completed.stdout)
    return {
        "version": candidate["version"],
        "status": "trained_candidate",
        "run_id": payload.get("run_id"),
        "metrics": payload.get("metrics"),
        "candidate_dir": payload.get("candidate_dir"),
    }


def version_exists(experiment_id: str, version: str) -> bool:
    import mlflow

    client = mlflow.tracking.MlflowClient()
    runs = client.search_runs(
        [experiment_id],
        filter_string=f"tags.version = '{version}'",
        max_results=1,
    )
    return bool(runs)


def parse_last_json_object(text: str) -> dict[str, Any]:
    start = text.rfind("\n{")
    if start >= 0:
        raw = text[start + 1 :]
    else:
        start = text.find("{")
        raw = text[start:] if start >= 0 else ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


if __name__ == "__main__":
    main()
