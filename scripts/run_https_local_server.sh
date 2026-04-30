#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${PUBLIC_DOMAIN:-}" ]]; then
  echo "PUBLIC_DOMAIN is required. Example: PUBLIC_DOMAIN=sensor-qa.example.com $0" >&2
  exit 1
fi

IMAGE_NAME="${IMAGE_NAME:-autodriving-sensor-qa:local-server}"
APP_CONTAINER_NAME="${APP_CONTAINER_NAME:-autodriving-sensor-qa-local-server}"
CADDY_CONTAINER_NAME="${CADDY_CONTAINER_NAME:-autodriving-sensor-qa-caddy}"
NETWORK_NAME="${NETWORK_NAME:-autodriving-sensor-qa-net}"
HTTP_PORT="${HTTP_PORT:-8088}"
HTTPS_PORT="${HTTPS_PORT:-8443}"
UPLOAD_HOST_DIR="${UPLOAD_HOST_DIR:-/media/byeongjae/HDD00/autodriving_sensor_qa_uploads}"
UPLOAD_CONTAINER_DIR="${UPLOAD_CONTAINER_DIR:-/data/uploads}"
CADDY_DATA_DIR="${CADDY_DATA_DIR:-/media/byeongjae/HDD00/autodriving_sensor_qa_caddy/data}"
CADDY_CONFIG_DIR="${CADDY_CONFIG_DIR:-/media/byeongjae/HDD00/autodriving_sensor_qa_caddy/config}"
MAX_UPLOAD_BYTES="${MAX_UPLOAD_BYTES:-10737418240}"
MAX_ACTIVE_UPLOAD_BYTES="${MAX_ACTIVE_UPLOAD_BYTES:-268435456000}"

mkdir -p "${UPLOAD_HOST_DIR}" "${CADDY_DATA_DIR}" "${CADDY_CONFIG_DIR}"

docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1 || docker network create "${NETWORK_NAME}" >/dev/null
docker build -t "${IMAGE_NAME}" .

docker rm -f "${APP_CONTAINER_NAME}" "${CADDY_CONTAINER_NAME}" >/dev/null 2>&1 || true

docker run -d \
  --name "${APP_CONTAINER_NAME}" \
  --restart unless-stopped \
  --network "${NETWORK_NAME}" \
  -v "${UPLOAD_HOST_DIR}:${UPLOAD_CONTAINER_DIR}" \
  -e "UPLOAD_TEMP_DIR=${UPLOAD_CONTAINER_DIR}" \
  -e "MAX_UPLOAD_BYTES=${MAX_UPLOAD_BYTES}" \
  -e "MAX_UPLOAD_SIZE_LABEL=10GB" \
  -e "MAX_ACTIVE_UPLOAD_BYTES=${MAX_ACTIVE_UPLOAD_BYTES}" \
  -e "MAX_ACTIVE_UPLOAD_SIZE_LABEL=250GB" \
  "${IMAGE_NAME}" >/dev/null

docker run -d \
  --name "${CADDY_CONTAINER_NAME}" \
  --restart unless-stopped \
  --network "${NETWORK_NAME}" \
  -p "${HTTP_PORT}:80" \
  -p "${HTTPS_PORT}:443" \
  -e "PUBLIC_DOMAIN=${PUBLIC_DOMAIN}" \
  -v "$(pwd)/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v "${CADDY_DATA_DIR}:/data" \
  -v "${CADDY_CONFIG_DIR}:/config" \
  caddy:2 >/dev/null

echo "HTTPS proxy is starting for https://${PUBLIC_DOMAIN}"
echo "Forward router external 80  -> this PC ${HTTP_PORT}"
echo "Forward router external 443 -> this PC ${HTTPS_PORT}"
echo "Upload temp directory: ${UPLOAD_HOST_DIR}"
