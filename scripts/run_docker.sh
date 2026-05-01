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

  echo "포트 사전 확인 도구가 없어 Docker 실행 결과로 포트 충돌을 확인합니다." >&2
  return 0
}

docker_port_conflict() {
  local port="$1"
  local log_file="$2"

  grep -Eiq "port is already allocated|Bind for 0\.0\.0\.0:${port} failed|Ports are not available" "$log_file"
}

run_with_available_port() {
  local port
  for ((port = START_PORT; port <= MAX_PORT; port++)); do
    if ! port_is_free "$port"; then
      echo "Port ${port} appears to be in use. Trying next port..."
      continue
    fi

    local log_file
    log_file="$(mktemp)"

    echo "Trying Docker port mapping at http://localhost:${port}"
    set +e
    docker run --rm \
      -p "${port}:8000" \
      -e "HOST_PORT=${port}" \
      "$IMAGE_NAME" 2>&1 | tee "$log_file"
    local exit_code="${PIPESTATUS[0]}"
    set -e

    if [[ "$exit_code" -eq 0 ]]; then
      rm -f "$log_file"
      return 0
    fi

    if docker_port_conflict "$port" "$log_file"; then
      echo "Port ${port} is already allocated by Docker. Trying next port..."
      rm -f "$log_file"
      continue
    fi

    rm -f "$log_file"
    return "$exit_code"
  done

  echo "${START_PORT}-${MAX_PORT} 범위에서 사용 가능한 포트를 찾지 못했습니다." >&2
  return 1
}

if [[ "$NO_BUILD" != "true" ]]; then
  docker build -t "$IMAGE_NAME" .
fi

run_with_available_port
