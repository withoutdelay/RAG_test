#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
ENV_FILE="$ROOT_DIR/.env"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
FRONTEND_HOST="127.0.0.1"
FRONTEND_PORT="3000"
GATEWAY_PORT="8001"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"

log() {
  printf '[dev-up] %s\n' "$*"
}

fail() {
  printf '[dev-up] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [restart]

Commands:
  restart   Stop managed backend/frontend processes first, then reload .env and start again
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing required command: $1"
  fi
}

load_env_file() {
  local env_file="$1"
  if [[ ! -f "$env_file" ]]; then
    return
  fi

  log "Loading environment from ${env_file#$ROOT_DIR/}"

  local restore_nounset=0
  if [[ -o nounset ]]; then
    restore_nounset=1
    set +u
  fi

  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a

  if [[ "$restore_nounset" -eq 1 ]]; then
    set -u
  fi
}

cleanup_stale_pid_file() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    rm -f "$pid_file"
  fi
}

stop_managed_process_if_needed() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "$pid_file"
}

restart_managed_processes() {
  log "Restart mode enabled; stopping managed backend/frontend processes"
  stop_managed_process_if_needed "$BACKEND_PID_FILE"
  stop_managed_process_if_needed "$FRONTEND_PID_FILE"
}

port_pid() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"

  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  log "Recent ${label} log tail:"
  case "$label" in
    backend)
      tail -n 40 "$BACKEND_LOG" 2>/dev/null || true
      ;;
    frontend)
      tail -n 40 "$FRONTEND_LOG" 2>/dev/null || true
      ;;
  esac
  fail "Timed out waiting for ${label} at ${url}"
}

start_compose_dependencies() {
  log "Starting Docker dependencies"
  (
    cd "$ROOT_DIR"
    docker compose up -d postgres redis qdrant minio gateway
  )
  wait_for_url "http://${BACKEND_HOST}:${GATEWAY_PORT}/health" "gateway" 60
}

run_migrations() {
  local alembic_bin="$ROOT_DIR/.venv/bin/alembic"
  if [[ ! -x "$alembic_bin" ]]; then
    fail "Missing Alembic binary: $alembic_bin"
  fi
  log "Running database migrations"
  (
    cd "$ROOT_DIR/backend"
    "$alembic_bin" upgrade head >/dev/null
  )
}

start_backend() {
  local health_url="http://${BACKEND_HOST}:${BACKEND_PORT}/health"
  cleanup_stale_pid_file "$BACKEND_PID_FILE"
  if curl -fsS --max-time 2 "$health_url" >/dev/null 2>&1; then
    log "Backend already running at ${health_url}"
    return
  fi

  stop_managed_process_if_needed "$BACKEND_PID_FILE"
  local existing_pid
  existing_pid="$(port_pid "$BACKEND_PORT" || true)"
  if [[ -n "$existing_pid" ]]; then
    fail "Port ${BACKEND_PORT} is already in use by PID ${existing_pid}"
  fi

  local python_bin="$ROOT_DIR/.venv/bin/python"
  if [[ ! -x "$python_bin" ]]; then
    fail "Missing Python binary: $python_bin"
  fi

  : >"$BACKEND_LOG"
  log "Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
  (
    cd "$ROOT_DIR/backend"
    nohup "$python_bin" -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" >>"$BACKEND_LOG" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
  wait_for_url "$health_url" "backend" 60
}

start_frontend() {
  local frontend_url="http://${FRONTEND_HOST}:${FRONTEND_PORT}/projects"
  cleanup_stale_pid_file "$FRONTEND_PID_FILE"
  if curl -fsS --max-time 2 "$frontend_url" >/dev/null 2>&1; then
    log "Frontend already running at ${frontend_url}"
    return
  fi

  stop_managed_process_if_needed "$FRONTEND_PID_FILE"
  local existing_pid
  existing_pid="$(port_pid "$FRONTEND_PORT" || true)"
  if [[ -n "$existing_pid" ]]; then
    fail "Port ${FRONTEND_PORT} is already in use by PID ${existing_pid}"
  fi

  local next_bin="$ROOT_DIR/frontend/node_modules/.bin/next"
  if [[ ! -x "$next_bin" ]]; then
    fail "Missing Next.js binary: $next_bin"
  fi

  : >"$FRONTEND_LOG"
  log "Starting frontend on ${FRONTEND_HOST}:${FRONTEND_PORT}"
  (
    cd "$ROOT_DIR/frontend"
    nohup "$next_bin" dev --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT" >>"$FRONTEND_LOG" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )
  wait_for_url "$frontend_url" "frontend" 60
}

main() {
  local mode="${1:-up}"
  if [[ $# -gt 1 ]]; then
    usage
    fail "Too many arguments"
  fi

  case "$mode" in
    up)
      ;;
    restart)
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "Unknown command: $mode"
      ;;
  esac

  mkdir -p "$RUN_DIR"
  require_command docker
  require_command curl
  require_command lsof

  load_env_file "$ENV_FILE"

  if [[ "$mode" == "restart" ]]; then
    restart_managed_processes
  fi

  start_compose_dependencies
  run_migrations
  start_backend
  start_frontend

  log "All services are ready"
  log "Frontend: http://${FRONTEND_HOST}:${FRONTEND_PORT}/projects"
  log "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}/health"
  log "Logs:     $RUN_DIR"
}

main "$@"
