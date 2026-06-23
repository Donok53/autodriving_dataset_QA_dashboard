#!/usr/bin/env bash
set -euo pipefail

HOST="${MLFLOW_HOST:-127.0.0.1}"
PORT="${MLFLOW_PORT:-5000}"
BACKEND_STORE_URI="${MLFLOW_BACKEND_STORE_URI:-sqlite:///mlruns/mlflow.db}"
ARTIFACT_ROOT="${MLFLOW_ARTIFACT_ROOT:-./mlartifacts}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if ! "${PYTHON_BIN}" -c "import mlflow" >/dev/null 2>&1 && [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

mkdir -p mlruns mlartifacts

echo "MLflow Tracking Server URL: http://${HOST}:${PORT}"
echo "Backend store URI: ${BACKEND_STORE_URI}"
echo "Artifact root: ${ARTIFACT_ROOT}"
echo "Python: ${PYTHON_BIN}"

"${PYTHON_BIN}" -m mlflow server \
  --host "${HOST}" \
  --port "${PORT}" \
  --backend-store-uri "${BACKEND_STORE_URI}" \
  --default-artifact-root "${ARTIFACT_ROOT}"
