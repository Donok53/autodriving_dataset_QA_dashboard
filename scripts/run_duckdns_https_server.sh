#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.duckdns}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ -z "${PUBLIC_DOMAIN:-}" ]]; then
  echo "PUBLIC_DOMAIN is required. Put it in ${ENV_FILE} or pass it as an environment variable." >&2
  exit 1
fi

if [[ -z "${DUCKDNS_API_TOKEN:-}" ]]; then
  echo "DUCKDNS_API_TOKEN is required. Put it in ${ENV_FILE}; do not commit that file." >&2
  exit 1
fi

APP_IMAGE_NAME="${APP_IMAGE_NAME:-autodriving-sensor-qa:local-server}"
CADDY_IMAGE_NAME="${CADDY_IMAGE_NAME:-caddy-duckdns:local}"
APP_CONTAINER_NAME="${APP_CONTAINER_NAME:-autodriving-sensor-qa-local-server}"
CADDY_CONTAINER_NAME="${CADDY_CONTAINER_NAME:-autodriving-sensor-qa-caddy}"
NETWORK_NAME="${NETWORK_NAME:-autodriving-sensor-qa-net}"
HTTPS_PORT="${HTTPS_PORT:-8443}"
PUBLIC_HTTPS_PORT="${PUBLIC_HTTPS_PORT:-18443}"
CADDY_CONTAINER_DNS="${CADDY_CONTAINER_DNS:-8.8.8.8}"
DUCKDNS_RESOLVER="${DUCKDNS_RESOLVER:-8.8.8.8:53}"
UPLOAD_HOST_DIR="${UPLOAD_HOST_DIR:-$(pwd)/runtime/uploads}"
UPLOAD_CONTAINER_DIR="${UPLOAD_CONTAINER_DIR:-/data/uploads}"
CADDY_DATA_DIR="${CADDY_DATA_DIR:-$(pwd)/runtime/caddy-duckdns/data}"
CADDY_CONFIG_DIR="${CADDY_CONFIG_DIR:-$(pwd)/runtime/caddy-duckdns/config}"
MAX_UPLOAD_BYTES="${MAX_UPLOAD_BYTES:-10737418240}"
MAX_ACTIVE_UPLOAD_BYTES="${MAX_ACTIVE_UPLOAD_BYTES:-268435456000}"

mkdir -p "${UPLOAD_HOST_DIR}" "${CADDY_DATA_DIR}" "${CADDY_CONFIG_DIR}"

docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1 || docker network create "${NETWORK_NAME}" >/dev/null
docker build -t "${APP_IMAGE_NAME}" .
docker build -f deploy/caddy-duckdns.Dockerfile -t "${CADDY_IMAGE_NAME}" .

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
  "${APP_IMAGE_NAME}" >/dev/null

docker run -d \
  --name "${CADDY_CONTAINER_NAME}" \
  --restart unless-stopped \
  --network "${NETWORK_NAME}" \
  --dns "${CADDY_CONTAINER_DNS}" \
  -p "${HTTPS_PORT}:443" \
  -e "PUBLIC_DOMAIN=${PUBLIC_DOMAIN}" \
  -e "DUCKDNS_API_TOKEN=${DUCKDNS_API_TOKEN}" \
  -e "DUCKDNS_RESOLVER=${DUCKDNS_RESOLVER}" \
  -v "$(pwd)/deploy/Caddyfile.duckdns:/etc/caddy/Caddyfile:ro" \
  -v "${CADDY_DATA_DIR}:/data" \
  -v "${CADDY_CONFIG_DIR}:/config" \
  "${CADDY_IMAGE_NAME}" >/dev/null

echo "HTTPS server is starting on this PC port ${HTTPS_PORT}"
echo "Public URL: https://${PUBLIC_DOMAIN}:${PUBLIC_HTTPS_PORT}"
echo "Forward router external ${PUBLIC_HTTPS_PORT} -> this PC ${HTTPS_PORT}"
echo "Upload temp directory: ${UPLOAD_HOST_DIR}"
echo "Caddy DNS resolver: ${CADDY_CONTAINER_DNS}"
