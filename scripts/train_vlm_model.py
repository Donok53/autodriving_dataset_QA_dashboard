from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.model_registry import CANDIDATE_MODEL_DIR, INFO_FILENAME, MODEL_FILENAME, write_model_info
from app.services.server_vlm import build_context_feature


DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "vlm_training_manifest.csv"
DEFAULT_IMAGE_SIZE = 48
FEATURE_SPEC_VERSION = "dashboard-vlm-v1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the dashboard student VLM model and log the run to MLflow.")
    parser.add_argument("--data", default=str(DEFAULT_MANIFEST), help="Training manifest CSV path.")
    parser.add_argument("--tracking-uri", default=None, help="MLflow tracking URI. Defaults to MLFLOW_TRACKING_URI or local mlruns.")
    parser.add_argument("--experiment-name", default="xai-vlm-dashboard", help="MLflow experiment name.")
    parser.add_argument("--run-name", default=None, help="Optional MLflow run name.")
    parser.add_argument("--version", default=None, help="Candidate model version name.")
    parser.add_argument("--model-name", default="xai_student_model", help="Service model name.")
    parser.add_argument("--registered-model-name", default=None, help="Optional MLflow model registry name.")
    parser.add_argument("--image-size", type=int, default=DEFAULT_IMAGE_SIZE, help="Synthetic training image side length.")
    parser.add_argument("--test-size", type=float, default=0.25, help="Stratified test split ratio.")
    parser.add_argument("--seed", type=int, default=53, help="Random seed.")
    parser.add_argument("--max-iter", type=int, default=1000, help="LogisticRegression max_iter.")
    parser.add_argument("--c", type=float, default=1.0, help="LogisticRegression C.")
    args = parser.parse_args()

    try:
        import mlflow
        import mlflow.sklearn
    except ImportError as exc:
        raise SystemExit(
            "MLflow가 설치되어 있지 않습니다. `pip install -r requirements-mlops.txt` 후 다시 실행해 주세요."
        ) from exc

    data_path = Path(args.data).expanduser().resolve()
    samples = load_training_manifest(data_path)
    x, y, vectorizer, label_encoder = build_training_arrays(samples, image_size=args.image_size, seed=args.seed)
    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=args.test_size,
        random_state=args.seed,
        stratify=y,
    )

    model = LogisticRegression(
        C=args.c,
        class_weight="balanced",
        max_iter=args.max_iter,
        multi_class="auto",
        random_state=args.seed,
    )
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    metrics = compute_metrics(y_test, predictions)

    tracking_uri = args.tracking_uri or os.getenv("MLFLOW_TRACKING_URI") or str(PROJECT_ROOT / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(args.experiment_name)

    version = args.version or f"candidate-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    candidate_dir = (CANDIDATE_MODEL_DIR / version).resolve()
    candidate_dir.mkdir(parents=True, exist_ok=True)
    report_path = candidate_dir / "classification_report.json"
    confusion_path = candidate_dir / "confusion_matrix.csv"

    with mlflow.start_run(run_name=args.run_name or version) as run:
        run_id = run.info.run_id
        bundle = {
            "model": model,
            "vectorizer": vectorizer,
            "label_encoder": label_encoder,
            "image_size": int(args.image_size),
            "feature_dim": int(x.shape[1]),
            "classes": [str(label) for label in label_encoder.classes_],
            "feature_spec_version": FEATURE_SPEC_VERSION,
        }
        model_path = candidate_dir / MODEL_FILENAME
        joblib.dump(bundle, model_path)

        model_info = {
            "model_name": args.model_name,
            "version": version,
            "run_id": run_id,
            "accuracy": metrics["accuracy"],
            "macro_f1": metrics["macro_f1"],
            "weighted_f1": metrics["weighted_f1"],
            "macro_precision": metrics["macro_precision"],
            "macro_recall": metrics["macro_recall"],
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "status": "candidate",
            "model_path": relative_to_project(model_path),
            "training_data": relative_to_project(data_path),
            "feature_spec_version": FEATURE_SPEC_VERSION,
        }
        write_model_info(candidate_dir / INFO_FILENAME, model_info)
        write_report(report_path, y_test, predictions, label_encoder)
        write_confusion_matrix(confusion_path, y_test, predictions, label_encoder)

        mlflow.log_params(
            {
                "image_size": int(args.image_size),
                "feature_dim": int(x.shape[1]),
                "train_rows": int(len(y_train)),
                "test_rows": int(len(y_test)),
                "label_count": int(len(label_encoder.classes_)),
                "model_type": "LogisticRegression",
                "class_weight": "balanced",
                "max_iter": int(args.max_iter),
                "C": float(args.c),
                "feature_spec_version": FEATURE_SPEC_VERSION,
            }
        )
        mlflow.log_metrics(metrics)
        mlflow.set_tags(
            {
                "service": "autodriving-dataset-qa-dashboard",
                "model_stage": "candidate",
                "version": version,
            }
        )
        mlflow.log_artifact(str(data_path), artifact_path="training_data")
        mlflow.log_artifacts(str(candidate_dir), artifact_path="service_model")
        mlflow.sklearn.log_model(
            model,
            artifact_path="classifier",
            registered_model_name=args.registered_model_name or None,
        )

    print(
        json.dumps(
            {
                "status": "trained",
                "tracking_uri": tracking_uri,
                "experiment_name": args.experiment_name,
                "run_id": model_info["run_id"],
                "candidate_dir": relative_to_project(candidate_dir),
                "metrics": metrics,
                "promote_command": f"python scripts/promote_model.py --candidate-dir {relative_to_project(candidate_dir)}",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def load_training_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"training manifest not found: {path}")
    with open(path, "r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"training manifest is empty: {path}")

    samples: list[dict[str, Any]] = []
    for row in rows:
        copies = max(1, int(row.get("copies") or 1))
        for copy_index in range(copies):
            sample = dict(row)
            sample["_copy_index"] = copy_index
            sample["_sample_key"] = f"{row.get('sample_id')}#{copy_index}"
            samples.append(sample)
    return samples


def build_training_arrays(
    samples: list[dict[str, Any]],
    *,
    image_size: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, DictVectorizer, LabelEncoder]:
    context_rows = [build_context_feature(row_to_context(sample)) for sample in samples]
    vectorizer = DictVectorizer(sparse=False)
    context_matrix = vectorizer.fit_transform(context_rows).astype(np.float32)
    image_matrix = np.vstack([make_image_feature(sample, image_size=image_size, seed=seed) for sample in samples])
    x = np.concatenate([image_matrix, context_matrix], axis=1).astype(np.float32)

    label_encoder = LabelEncoder()
    y = label_encoder.fit_transform([str(sample["label_ko"]) for sample in samples])
    return x, y, vectorizer, label_encoder


def row_to_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_label": row.get("event_label"),
        "motion_state": row.get("motion_state"),
        "planner_reason": row.get("planner_reason"),
        "path_blocked": parse_bool(row.get("path_blocked")),
        "obstacle_summary": {
            "near_raw_points": parse_float(row.get("near_raw_points")),
            "near_raw_min_range_m": parse_float(row.get("near_raw_min_range_m")),
        },
        "source_bag_stem": row.get("source_bag_stem"),
        "motion_summary": {
            "dominant_motion_ko": row.get("dominant_motion_ko"),
            "ego_motion_ko": row.get("ego_motion_ko"),
            "scene_state_ko": row.get("scene_state_ko"),
            "prev_to_curr": {
                "mean_magnitude": parse_float(row.get("prev_to_curr_mean_magnitude")),
                "moving_ratio": parse_float(row.get("prev_to_curr_moving_ratio")),
                "center_moving_ratio": parse_float(row.get("prev_to_curr_center_moving_ratio")),
                "mean_dx": parse_float(row.get("prev_to_curr_mean_dx")),
                "mean_dy": parse_float(row.get("prev_to_curr_mean_dy")),
            },
            "curr_to_next": {
                "mean_magnitude": parse_float(row.get("curr_to_next_mean_magnitude")),
                "moving_ratio": parse_float(row.get("curr_to_next_moving_ratio")),
                "center_moving_ratio": parse_float(row.get("curr_to_next_center_moving_ratio")),
                "mean_dx": parse_float(row.get("curr_to_next_mean_dx")),
                "mean_dy": parse_float(row.get("curr_to_next_mean_dy")),
            },
        },
    }


def make_image_feature(row: dict[str, Any], *, image_size: int, seed: int) -> np.ndarray:
    label = str(row["label_ko"])
    copy_index = int(row.get("_copy_index") or 0)
    rng = np.random.default_rng(stable_seed(f"{label}:{copy_index}", seed))
    image = rng.normal(0.08, 0.018, size=(image_size, image_size)).astype(np.float32)
    yy, xx = np.mgrid[0:image_size, 0:image_size]
    cx = image_size * (0.48 + rng.normal(0, 0.03))
    cy = image_size * (0.52 + rng.normal(0, 0.03))

    if label == "사람":
        image[:, max(0, int(cx) - 2) : min(image_size, int(cx) + 2)] += 0.68
        image[int(image_size * 0.18) : int(image_size * 0.32), int(cx) - 5 : int(cx) + 5] += 0.32
    elif label == "차량":
        image[int(cy) - 8 : int(cy) + 8, int(cx) - 18 : int(cx) + 18] += 0.64
        image[int(cy) + 6 : int(cy) + 11, int(cx) - 15 : int(cx) - 8] += 0.22
        image[int(cy) + 6 : int(cy) + 11, int(cx) + 8 : int(cx) + 15] += 0.22
    elif label == "주차콘":
        mask = (yy > image_size * 0.24) & (yy < image_size * 0.82) & (np.abs(xx - cx) < (yy - image_size * 0.18) * 0.28)
        image[mask] += 0.72
    elif label == "주차금지 표지판":
        ring = np.abs(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) - image_size * 0.18) < 2.2
        image[ring] += 0.76
        image[np.abs((yy - cy) + (xx - cx)) < 2.0] += 0.42
    elif label == "안전봉":
        for offset in (-7, 0, 7):
            image[:, max(0, int(cx + offset) - 1) : min(image_size, int(cx + offset) + 2)] += 0.58
    elif label == "쓰레기통":
        image[int(cy) - 12 : int(cy) + 14, int(cx) - 11 : int(cx) + 11] += 0.52
        image[int(cy) - 16 : int(cy) - 12, int(cx) - 13 : int(cx) + 13] += 0.28
    elif label == "벽":
        image[:, int(image_size * 0.58) :] += 0.50
        image[:, int(image_size * 0.58) : int(image_size * 0.61)] += 0.23
    elif label == "나무":
        trunk = (np.abs(xx - cx) < 3) & (yy > image_size * 0.42)
        crown = ((xx - cx) ** 2 + (yy - image_size * 0.28) ** 2) < (image_size * 0.18) ** 2
        image[trunk] += 0.45
        image[crown] += 0.64
    else:
        image[int(cy) - 10 : int(cy) + 10, int(cx) - 10 : int(cx) + 10] += 0.35
        diagonal = np.abs((yy - cy) - (xx - cx)) < 2.5
        image[diagonal] += 0.35

    return np.clip(image, 0.0, 1.0).reshape(-1).astype(np.float32)


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
        "macro_precision": float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
    }


def write_report(path: Path, y_true: np.ndarray, y_pred: np.ndarray, label_encoder: LabelEncoder) -> None:
    report = classification_report(
        y_true,
        y_pred,
        target_names=[str(label) for label in label_encoder.classes_],
        output_dict=True,
        zero_division=0,
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_confusion_matrix(path: Path, y_true: np.ndarray, y_pred: np.ndarray, label_encoder: LabelEncoder) -> None:
    matrix = confusion_matrix(y_true, y_pred, labels=list(range(len(label_encoder.classes_))))
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", *[str(label) for label in label_encoder.classes_]])
        for label, row in zip(label_encoder.classes_, matrix, strict=True):
            writer.writerow([str(label), *[int(value) for value in row]])


def parse_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def stable_seed(value: str, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def relative_to_project(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
