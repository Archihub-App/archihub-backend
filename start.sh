#!/bin/bash

set -u

cd /app

child_pid=""
restart_requested=0
stop_requested=0

restart_child() {
  restart_requested=1
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
  fi
}

stop_child() {
  stop_requested=1
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
  fi
}

trap restart_child HUP
trap stop_child TERM INT

# -------- WAIT FOR ELASTICSEARCH --------
echo "Waiting for Elasticsearch to be healthy..."

if [ -n "${ELASTIC_CERT:-}" ]; then
  echo "Using certificate authentication for Elasticsearch"
  
  until curl -s -f -u "${ELASTIC_USER}:${ELASTIC_PASSWORD}" "${ELASTIC_DOMAIN}:${ELASTIC_PORT}/_cluster/health?wait_for_status=yellow&timeout=1s" --cacert "${ELASTIC_CERT}"; do
    >&2 echo "Elasticsearch is unavailable - sleeping"
    sleep 5
  done
else
  echo "Using standard authentication for Elasticsearch"
  
  until curl -s -f -u "${ELASTIC_USER}:${ELASTIC_PASSWORD}" "${ELASTIC_DOMAIN}:${ELASTIC_PORT}/_cluster/health?wait_for_status=yellow&timeout=1s"; do
    >&2 echo "Elasticsearch is unavailable - sleeping"
    sleep 5
  done
fi

# -------- START BACKEND --------
# `main:app` is the ASGI entrypoint; `archihub/` is the application package.
#
# uvicorn runs its own worker processes. gunicorn's
# `uvicorn.workers.UvicornWorker` is DEPRECATED - it moved to the separate
# `uvicorn-worker` distribution - so depending on it would tie the deployment to
# a shim already on its way out.
FASTAPI_ENV="${FASTAPI_ENV:-PROD}"
FASTAPI_RUN_PORT="${FASTAPI_RUN_PORT:-${BACKEND_PORT:-5000}}"
UVICORN_WORKERS="${UVICORN_WORKERS:-4}"

echo "Elasticsearch is up!"
echo "Starting backend in ${FASTAPI_ENV} mode on port ${FASTAPI_RUN_PORT}"

while true; do
  if [ "$FASTAPI_ENV" = "DEV" ]; then
      uvicorn main:app --host 0.0.0.0 --port "${FASTAPI_RUN_PORT}" --reload &
  elif [ "$FASTAPI_ENV" = "PROD" ]; then
      # --no-access-log: the per-request line is emitted by the application's
      # own middleware instead, which is what carries the correlation id tying
      # a request to the log lines it produced. uvicorn's is written by the
      # server, outside that scope.
      uvicorn main:app --host 0.0.0.0 --port "${FASTAPI_RUN_PORT}" \
             --workers "${UVICORN_WORKERS}" --no-access-log &
  else
      echo "Unknown FASTAPI_ENV: ${FASTAPI_ENV} (expected 'DEV' or 'PROD')"
      exit 1
  fi

  child_pid=$!
  wait "$child_pid"
  exit_code=$?
  child_pid=""

  if [[ "$restart_requested" -eq 1 ]]; then
    echo "Restart request received, starting backend again"
    restart_requested=0
    continue
  fi

  if [[ "$stop_requested" -eq 1 ]]; then
    exit "$exit_code"
  fi

  exit "$exit_code"
done