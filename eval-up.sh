#!/usr/bin/env bash
#
# eval-up.sh — Start the local Docker environment needed to run evals.
#
# What it starts:
#   eval-csql-proxy      Cloud SQL Auth Proxy (internal, tunnels to Cloud SQL)
#   eval-expense-api     Expense API          localhost:8000
#   eval-expense-mcp     Expense MCP server   localhost:8001
#   eval-splitwise-mcp   Splitwise MCP server localhost:8002
#   eval-langgraph-agent LangGraph agent      localhost:7860  ← eval target
#
# Usage:
#   ./eval-up.sh               Build images and start all services
#   ./eval-up.sh --no-build    Skip image rebuild (use existing images)
#   ./eval-up.sh --down        Stop and remove all eval containers + network

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Credentials & config ──────────────────────────────────────────────────────
# Override any of these via environment variables before running the script.
SERVICE_ACCOUNT_KEY="${SERVICE_ACCOUNT_KEY:-$SCRIPT_DIR/../dark-quasar-329408-cb7c3cbf1f34.json}"
DB_PASSWORD="${DB_PASSWORD:-SinCos@1998}"
# Percent-encode special characters in the password for use in connection URLs
DB_PASSWORD_URL="${DB_PASSWORD//@/%40}"
CLOUD_SQL_INSTANCE="project-796df5af-a68e-4648-a8f:us-central1:expense-tracker-db"
SPLITWISE_CONSUMER_KEY="FRWqBDp6uhBh9NCtHJqLiaaBpnFssHDSCCdv8Zc8"
SPLITWISE_CONSUMER_SECRET="0IPWO5jhxQsbcNujvv7IOXYadGUpP4XqyXwjOkmC"

# Auto-load ANTHROPIC_API_KEY from langgraph-agent/.env if not already set
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$SCRIPT_DIR/langgraph-agent/.env" 2>/dev/null \
    | head -1 | cut -d= -f2- | tr -d '"' || true)
fi

# ── Flags ─────────────────────────────────────────────────────────────────────
BUILD=true
DOWN=false

for arg in "$@"; do
  case $arg in
    --no-build) BUILD=false ;;
    --down)     DOWN=true ;;
    *)          echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ── Names ─────────────────────────────────────────────────────────────────────
NETWORK="eval-net"
ALL_CONTAINERS=(eval-csql-proxy eval-expense-api eval-expense-mcp eval-splitwise-mcp eval-langgraph-agent)

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { printf '\033[1;34m▶\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }

wait_for_http() {
  # Requires a 2xx response — use for services with a proper /health endpoint
  local name="$1" url="$2" i=0
  log "Waiting for $name..."
  until curl -sf "$url" &>/dev/null; do
    (( i++ ))
    if (( i > 40 )); then
      err "$name did not become healthy after 40s"
      docker logs "$name" --tail 30
      exit 1
    fi
    sleep 1
  done
  ok "$name is ready  ($url)"
}

wait_for_up() {
  # Accepts any HTTP response (including 4xx) — use for MCP servers without /health
  local name="$1" url="$2" i=0
  log "Waiting for $name..."
  until curl -s -o /dev/null "$url" 2>/dev/null; do
    (( i++ ))
    if (( i > 40 )); then
      err "$name did not respond after 40s"
      docker logs "$name" --tail 30
      exit 1
    fi
    sleep 1
  done
  ok "$name is ready  ($url)"
}

# ── Teardown ──────────────────────────────────────────────────────────────────
down() {
  log "Stopping eval containers..."
  docker rm -f "${ALL_CONTAINERS[@]}" 2>/dev/null || true
  docker network rm "$NETWORK" 2>/dev/null || true
  ok "Eval environment stopped."
}

if [[ $DOWN == true ]]; then
  down
  exit 0
fi

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  err "ANTHROPIC_API_KEY not set and not found in langgraph-agent/.env"
  exit 1
fi

if [[ ! -f "$SERVICE_ACCOUNT_KEY" ]]; then
  err "Service account key not found: $SERVICE_ACCOUNT_KEY"
  err "Set SERVICE_ACCOUNT_KEY=<path> before running, or place the key at the expected path."
  exit 1
fi

echo ""
log "Starting eval environment"
log "Service account: $SERVICE_ACCOUNT_KEY"
log "Cloud SQL:        $CLOUD_SQL_INSTANCE"
echo ""

# ── Remove any leftover eval containers ───────────────────────────────────────
docker rm -f "${ALL_CONTAINERS[@]}" 2>/dev/null || true

# ── Docker network ────────────────────────────────────────────────────────────
if ! docker network inspect "$NETWORK" &>/dev/null; then
  docker network create "$NETWORK" &>/dev/null
fi

# ── Build images ──────────────────────────────────────────────────────────────
if [[ $BUILD == true ]]; then
  log "Building images (pass --no-build to skip)..."
  docker build -q -t expense-api     "$SCRIPT_DIR/expense-api"     && ok "expense-api"
  docker build -q -t expense-mcp     "$SCRIPT_DIR/expense-mcp"     && ok "expense-mcp"
  docker build -q -t splitwise-mcp   "$SCRIPT_DIR/splitwise-mcp"   && ok "splitwise-mcp"
  docker build -q -t langgraph-agent "$SCRIPT_DIR/langgraph-agent" && ok "langgraph-agent"
  echo ""
fi

# ── Cloud SQL Auth Proxy ──────────────────────────────────────────────────────
log "Starting eval-csql-proxy..."
docker run -d \
  --name eval-csql-proxy \
  --network "$NETWORK" \
  -v "$SERVICE_ACCOUNT_KEY:/key.json:ro" \
  gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.8.0 \
  --credentials-file=/key.json \
  --address=0.0.0.0 \
  --port=5432 \
  "$CLOUD_SQL_INSTANCE" \
  > /dev/null

# Proxy has no HTTP health endpoint; wait for "Listening on" in logs
i=0
until docker logs eval-csql-proxy 2>&1 | grep -q "Listening on"; do
  (( i++ ))
  if (( i > 20 )); then
    err "Cloud SQL proxy did not start after 20s"
    docker logs eval-csql-proxy
    exit 1
  fi
  sleep 1
done
ok "eval-csql-proxy ready"

# ── Expense API ───────────────────────────────────────────────────────────────
log "Starting eval-expense-api..."
docker run -d \
  --name eval-expense-api \
  --network "$NETWORK" \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql://app_user:${DB_PASSWORD_URL}@eval-csql-proxy:5432/expense_tracker" \
  -e GOOGLE_SERVICE_ACCOUNT_FILE=/app/service_account.json \
  -v "$SERVICE_ACCOUNT_KEY:/app/service_account.json:ro" \
  expense-api \
  > /dev/null

wait_for_http eval-expense-api "http://localhost:8000/health"

# ── Expense MCP ───────────────────────────────────────────────────────────────
log "Starting eval-expense-mcp..."
docker run -d \
  --name eval-expense-mcp \
  --network "$NETWORK" \
  -p 8001:8001 \
  -e API_BASE_URL="http://eval-expense-api:8000" \
  expense-mcp \
  > /dev/null

wait_for_up eval-expense-mcp "http://localhost:8001/mcp/"

# ── Splitwise MCP ─────────────────────────────────────────────────────────────
log "Starting eval-splitwise-mcp..."
docker run -d \
  --name eval-splitwise-mcp \
  --network "$NETWORK" \
  -p 8002:8002 \
  -e SPLITWISE_CONSUMER_KEY="$SPLITWISE_CONSUMER_KEY" \
  -e SPLITWISE_CONSUMER_SECRET="$SPLITWISE_CONSUMER_SECRET" \
  -e SERVER_URL="http://localhost:8002" \
  -e MCP_PORT=8002 \
  -e TOKEN_DB_URL="postgresql://app_user:${DB_PASSWORD_URL}@eval-csql-proxy:5432/splitwise_mcp" \
  splitwise-mcp \
  > /dev/null

wait_for_up eval-splitwise-mcp "http://localhost:8002/"

# ── LangGraph Agent ───────────────────────────────────────────────────────────
log "Starting eval-langgraph-agent..."
docker run -d \
  --name eval-langgraph-agent \
  --network "$NETWORK" \
  -p 7860:7860 \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e MCP_SERVER_URL="http://eval-expense-mcp:8001/mcp/" \
  -e SPLITWISE_MCP_BASE_URL="http://eval-splitwise-mcp:8002/mcp" \
  -e MODEL_NAME="claude-haiku-4-5-20251001" \
  langgraph-agent \
  > /dev/null

wait_for_http eval-langgraph-agent "http://localhost:7860/health"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
ok "Eval environment is up!"
echo ""
echo "  Agent:        http://localhost:7860"
echo "  Expense API:  http://localhost:8000"
echo "  Expense MCP:  http://localhost:8001"
echo "  Splitwise:    http://localhost:8002"
echo ""
echo "Run evals:"
echo "  python evals/run_evals.py                              # TC-001 to TC-010 (TC-006 skipped)"
echo "  python evals/run_evals.py --splitwise-token <token>   # all 10 cases"
echo ""
echo "Tear down:"
echo "  ./eval-up.sh --down"
echo ""
