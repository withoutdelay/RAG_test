#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
COMPOSE_FILE="$ROOT_DIR/docker-compose.prod.yml"

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [up|restart|down|logs|status]

Commands:
  up       Build and start the production stack, then run migrations
  restart  Recreate the production stack with the latest .env and image build
  down     Stop the production stack
  logs     Tail docker compose logs
  status   Show docker compose status
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing required command: $1"
  fi
}

load_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    fail "Missing .env file at $ENV_FILE"
  fi

  local restore_nounset=0
  if [[ -o nounset ]]; then
    restore_nounset=1
    set +u
  fi

  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  if [[ "$restore_nounset" -eq 1 ]]; then
    set -u
  fi
}

compose() {
  (
    cd "$ROOT_DIR"
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
  )
}

wait_for_postgres() {
  local attempts="${1:-60}"
  local user="${POSTGRES_USER:-copilot}"
  local database="${POSTGRES_DB:-copilot_db}"

  for ((i = 1; i <= attempts; i++)); do
    if compose exec -T postgres pg_isready -U "$user" -d "$database" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  fail "Timed out waiting for postgres to become ready"
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"

  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  compose logs --tail=80 "${label}" || true
  fail "Timed out waiting for ${label} at ${url}"
}

validate_env() {
  if [[ "${APP_ENV:-production}" != "production" ]]; then
    log "Warning: APP_ENV=${APP_ENV:-unset}; server deployment usually should use APP_ENV=production"
  fi

  if [[ -z "${NEXT_PUBLIC_API_BASE_URL:-}" ]]; then
    fail "NEXT_PUBLIC_API_BASE_URL is required in .env for frontend production build"
  fi

  if [[ "${NEXT_PUBLIC_API_BASE_URL}" == *"localhost"* || "${NEXT_PUBLIC_API_BASE_URL}" == *"127.0.0.1"* ]]; then
    if [[ "${DEPLOY_ALLOW_LOCAL_API:-0}" != "1" ]]; then
      fail "NEXT_PUBLIC_API_BASE_URL points to localhost/127.0.0.1; set it to the server IP/domain or export DEPLOY_ALLOW_LOCAL_API=1 to bypass"
    fi
    log "Warning: deploying with a localhost API endpoint because DEPLOY_ALLOW_LOCAL_API=1"
  fi
}

run_up() {
  log "Starting infrastructure services"
  compose up -d --build postgres redis qdrant minio gateway

  log "Waiting for postgres"
  wait_for_postgres 60

  log "Running database migrations"
  compose run --rm backend alembic upgrade head

  log "Starting backend and frontend"
  compose up -d --build backend frontend

  wait_for_url "http://127.0.0.1:${BACKEND_PORT:-8000}/health" "backend" 60
  wait_for_url "http://127.0.0.1:${FRONTEND_PORT:-3000}/projects" "frontend" 60

  log "Deployment finished"
  log "Frontend: http://127.0.0.1:${FRONTEND_PORT:-3000}/projects"
  log "Backend:  http://127.0.0.1:${BACKEND_PORT:-8000}/health"
}

run_restart() {
  log "Recreating the production stack"
  compose down
  run_up
}

main() {
  local mode="${1:-up}"

  if [[ $# -gt 1 ]]; then
    usage
    fail "Too many arguments"
  fi

  case "$mode" in
    up|restart|down|logs|status)
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

  require_command docker
  require_command curl
  load_env_file
  validate_env

  case "$mode" in
    up)
      run_up
      ;;
    restart)
      run_restart
      ;;
    down)
      compose down
      ;;
    logs)
      compose logs -f
      ;;
    status)
      compose ps
      ;;
  esac
}

main "$@"
