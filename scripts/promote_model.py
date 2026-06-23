from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.model_registry import ModelRegistryError, promote_candidate_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote a candidate VLM model to models/current.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate-dir", help="Local candidate directory containing student_baseline.joblib and model_info.json.")
    source.add_argument("--run-id", help="MLflow run id. The service_model artifact will be downloaded and promoted.")
    parser.add_argument("--tracking-uri", default=None, help="MLflow tracking URI for --run-id.")
    parser.add_argument("--no-archive", action="store_true", help="Do not archive the current model before promotion.")
    args = parser.parse_args()

    try:
        candidate_dir = Path(args.candidate_dir).expanduser().resolve() if args.candidate_dir else download_candidate(args.run_id, args.tracking_uri)
        result = promote_candidate_model(candidate_dir, archive_current=not args.no_archive)
    except ModelRegistryError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result, ensure_ascii=False, indent=2))


def download_candidate(run_id: str, tracking_uri: str | None) -> Path:
    try:
        import mlflow
    except ImportError as exc:
        raise SystemExit(
            "MLflow가 설치되어 있지 않습니다. `pip install -r requirements-mlops.txt` 후 다시 실행해 주세요."
        ) from exc

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    elif os.getenv("MLFLOW_TRACKING_URI"):
        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    local_path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="service_model")
    return Path(local_path).resolve()


if __name__ == "__main__":
    main()
