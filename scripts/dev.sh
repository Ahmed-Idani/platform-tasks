#!/usr/bin/env bash
# One command for the whole dev stack.
#
#   scripts/dev.sh                 infra + migrations + API + web + webhook receiver + worker (Docker)
#   scripts/dev.sh --worker local  run the worker from worker/.venv instead (faster to iterate, N_THREADS=4)
#   scripts/dev.sh --worker none   no worker at all (tasks stay queued)
#   scripts/dev.sh --keep-infra    on Ctrl+C, leave RabbitMQ/Postgres/worker containers running
#   scripts/dev.sh down            stop everything without starting anything
#
# Ctrl+C shuts everything down: API, web, webhook receiver, worker, RabbitMQ, Postgres, Garage.
# Data survives (Docker volumes are never removed). A task still running in the worker
# gets 15s to finish; if it doesn't, its message is redelivered on the next start.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORKER=docker
KEEP_INFRA=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --worker) WORKER="$2"; shift 2 ;;
    --worker=*) WORKER="${1#*=}"; shift ;;
    --keep-infra) KEEP_INFRA=true; shift ;;
    down) docker compose down -t 15; exit 0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)"; exit 1 ;;
  esac
done
[[ "$WORKER" =~ ^(docker|local|none)$ ]] || { echo "--worker must be docker, local or none"; exit 1; }

bold=$'\e[1m'; dim=$'\e[2m'; reset=$'\e[0m'
say() { echo "${bold}==>${reset} $*"; }

# Prefix every line of a process's output with a colored tag: "api    | ...".
# Every PID is recorded: the cleanup must not rely on Ctrl+C reaching the children,
# because background jobs of a script start with SIGINT *ignored*.
PIDS=()
run() {
  local name=$1 color=$2; shift 2
  ( "$@" 2>&1 | sed -u "s/^/"$'\e['"${color}m$(printf '%-7s' "$name")|"$'\e[0m'" /" ) &
  PIDS+=($!)
}

descendants() { local c; for c in $(pgrep -P "$1"); do descendants "$c"; echo "$c"; done; }

need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
need docker; need go; need npm

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for p in 8080 5173 9000; do
  if port_busy "$p"; then echo "port $p is already in use (an old dev run?). Stop it first."; exit 1; fi
done

cleanup() {
  trap - EXIT
  trap '' INT TERM   # a second Ctrl+C must not interrupt the shutdown half-way
  echo
  say "stopping API, web, webhook receiver, local worker"
  local all=() pid
  for pid in "${PIDS[@]}"; do all+=($(descendants "$pid") "$pid"); done
  if ((${#all[@]})); then
    kill -TERM "${all[@]}" 2>/dev/null || true
    # A local worker finishes its current task first: give it a moment, then force.
    for _ in $(seq 1 50); do
      local alive=()
      for pid in "${all[@]}"; do kill -0 "$pid" 2>/dev/null && alive+=("$pid"); done
      ((${#alive[@]})) || break
      sleep 0.3
    done
    for pid in "${all[@]}"; do kill -0 "$pid" 2>/dev/null && { echo "  force-killing $(ps -o comm= -p "$pid" 2>/dev/null || echo "$pid")"; kill -KILL "$pid" 2>/dev/null; }; done
  fi
  if [[ "$KEEP_INFRA" == true ]]; then
    say "leaving Docker services running (--keep-infra)"
  else
    say "stopping Docker services (worker gets 15s to finish its task; volumes are kept)"
    docker compose down -t 15
  fi
  say "bye"
}
trap cleanup INT TERM EXIT

# ---- config shared by the API, storage-init and a local worker --------------------
# Garage dev credentials (see docker-compose.yml). In prod: no endpoint, IRSA, no keys.
export S3_ENDPOINT_URL=http://localhost:3900
export S3_BUCKET=platform-tasks-dev
export AWS_REGION=garage
export AWS_ACCESS_KEY_ID=GK706c6174666f726d2d746b73
export AWS_SECRET_ACCESS_KEY=6465762d6f6e6c792d73332d7365637265742d706c6174666f726d2d7461736b

# ---- infra -----------------------------------------------------------------------
say "starting RabbitMQ, Postgres and Garage"
docker compose up -d --wait rabbitmq postgres garage

say "running migrations"
export GOOSE_DRIVER=postgres
export GOOSE_DBSTRING="postgres://app:app@localhost:5433/tasks?sslmode=disable"
export GOOSE_MIGRATION_DIR=api/migrations
if command -v goose >/dev/null; then GOOSE=goose
elif [[ -x "$HOME/go/bin/goose" ]]; then GOOSE="$HOME/go/bin/goose"
else GOOSE="go run github.com/pressly/goose/v3/cmd/goose@v3.28.0"; fi
$GOOSE up

say "configuring the bucket"
(cd api && go run ./cmd/storage-init)

# ---- web deps --------------------------------------------------------------------
if [[ ! -d web/node_modules ]]; then
  say "installing web dependencies"
  (cd web && npm install --no-fund --no-audit)
fi

# ---- processes -------------------------------------------------------------------
say "starting API, web and webhook receiver"

# API: restarted on every .go change if wgo is installed.
if command -v wgo >/dev/null || [[ -x "$HOME/go/bin/wgo" ]]; then
  WGO=$(command -v wgo || echo "$HOME/go/bin/wgo")
  run api 36 "$WGO" run -cd api -file '\.go$' ./cmd/api
else
  echo "${dim}(install wgo for API hot reload: go install github.com/bokwoon95/wgo@latest)${reset}"
  run api 36 bash -c "cd api && exec go run ./cmd/api"
fi

run web 35 bash -c "cd web && exec npx vite --clearScreen false"
run hook 33 python3 scripts/webhook_receiver.py

case "$WORKER" in
  docker)
    if ! docker image inspect summarizer-worker:dev >/dev/null 2>&1; then
      say "building the worker image (first time: compiles llama.cpp, a few minutes)"
      docker build -t summarizer-worker:dev worker
    fi
    docker compose up -d worker
    run worker 32 docker compose logs -f --no-log-prefix --since 0s worker
    ;;
  local)
    docker compose stop worker >/dev/null 2>&1 || true   # don't let the container compete for tasks
    [[ -x worker/.venv/bin/python ]] || { echo "worker/.venv missing: python3 -m venv worker/.venv && worker/.venv/bin/pip install -r worker/requirements.txt"; exit 1; }
    run worker 32 bash -c "cd worker && N_THREADS=\${N_THREADS:-4} exec .venv/bin/python worker.py"
    ;;
  none)
    echo "${dim}no worker: tasks will stay queued${reset}"
    ;;
esac

sleep 2
cat <<EOF

  ${bold}Web UI${reset}        http://localhost:5173
  ${bold}API${reset}           http://localhost:8080
  ${bold}RabbitMQ UI${reset}   http://localhost:15672   ${dim}(app / app)${reset}
  ${bold}Postgres${reset}      localhost:5433           ${dim}(app / app, db tasks)${reset}
  ${bold}Garage (S3)${reset}   http://localhost:3900    ${dim}(bucket platform-tasks-dev)${reset}
  ${bold}Webhooks${reset}      http://host.docker.internal:9000/hook ${dim}(worker in Docker)${reset}
                http://localhost:9000/hook            ${dim}(worker local)${reset}

  ${dim}Ctrl+C stops everything.${reset}

EOF
wait
