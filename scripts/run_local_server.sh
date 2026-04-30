#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-autodriving-sensor-qa:local-server}"
CONTAINER_NAME="${CONTAINER_NAME:-autodriving-sensor-qa-local-server}"
APP_PORT="${APP_PORT:-8000}"
UPLOAD_HOST_DIR="${UPLOAD_HOST_DIR:-$(pwd)/runtime/uploads}"
UPLOAD_CONTAINER_DIR="${UPLOAD_CONTAINER_DIR:-/data/uploads}"
MAX_UPLOAD_BYTES="${MAX_UPLOAD_BYTES:-10737418240}"
MAX_ACTIVE_UPLOAD_BYTES="${MAX_ACTIVE_UPLOAD_BYTES:-268435456000}"

mkdir -p "${UPLOAD_HOST_DIR}"

docker build -t "${IMAGE_NAME}" .
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  -p "${APP_PORT}:8000" \
  -v "${UPLOAD_HOST_DIR}:${UPLOAD_CONTAINER_DIR}" \
  -e "UPLOAD_TEMP_DIR=${UPLOAD_CONTAINER_DIR}" \
  -e "MAX_UPLOAD_BYTES=${MAX_UPLOAD_BYTES}" \
  -e "MAX_UPLOAD_SIZE_LABEL=10GB" \
  -e "MAX_ACTIVE_UPLOAD_BYTES=${MAX_ACTIVE_UPLOAD_BYTES}" \
  -e "MAX_ACTIVE_UPLOAD_SIZE_LABEL=250GB" \
  "${IMAGE_NAME}"

echo "Local server is running at http://127.0.0.1:${APP_PORT}"
echo "Upload temp directory: ${UPLOAD_HOST_DIR}"
