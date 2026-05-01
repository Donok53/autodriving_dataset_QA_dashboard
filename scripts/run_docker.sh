#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-autodriving-sensor-qa}"
START_PORT="${START_PORT:-8000}"
MAX_PORT="${MAX_PORT:-8099}"
NO_BUILD="${NO_BUILD:-false}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI를 찾을 수 없습니다. Docker를 먼저 설치하거나 실행해 주세요." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon에 연결할 수 없습니다. Docker Desktop 또는 Docker Engine이 실행 중인지 확인해 주세요." >&2
  exit 1
fi

port_is_free() {
  local port="$1"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
    return $?
  fi

  if command -v lsof >/dev/null 2>&1; then
    ! lsof -iTCP:"$port" -sTCP:LISTEN -Pn >/dev/null 2>&1
    return $?
  fi

  if command -v ss >/dev/null 2>&1; then
    ! ss -ltn "sport = :$port" | grep -q ":$port"
    return $?
  fi

  echo "포트 사용 여부를 확인하려면 python3, lsof, ss 중 하나가 필요합니다." >&2
  return 1
}

find_available_port() {
  local port
  for ((port = START_PORT; port <= MAX_PORT; port++)); do
    if port_is_free "$port"; then
      echo "$port"
      return 0
    fi
  done

  echo "${START_PORT}-${MAX_PORT} 범위에서 사용 가능한 포트를 찾지 못했습니다." >&2
  return 1
}

HOST_PORT="$(find_available_port)"

if [[ "$NO_BUILD" != "true" ]]; then
  docker build -t "$IMAGE_NAME" .
fi

echo "Docker container will be available at http://localhost:${HOST_PORT}"
docker run --rm \
  -p "${HOST_PORT}:8000" \
  -e "HOST_PORT=${HOST_PORT}" \
  "$IMAGE_NAME"
