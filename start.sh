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
# Which stack this container runs. Defaults to `flask` so an existing deploy is
# unchanged by this file gaining the option; Phase 7 flips the default to
# `fastapi` and deletes the legacy branch with app/.
#
# Until then nothing shipped could run the ported backend at all - the image
# booted `app:app` whatever else was in it - so a deployment could not be tested
# against the port even on a disposable instance.
ARCHIHUB_STACK="${ARCHIHUB_STACK:-flask}"

echo "Elasticsearch is up!"
echo "Backend stack: ${ARCHIHUB_STACK}"

while true; do
  if [ "$ARCHIHUB_STACK" = "fastapi" ]; then
      # `main:app` is the ASGI entrypoint; `archihub/` is the ported package.
      # uvicorn runs its own workers - gunicorn's `uvicorn.workers.UvicornWorker`
      # is DEPRECATED (it moved to the separate `uvicorn-worker` distribution),
      # so depending on it would tie the deploy to a shim that is already on its
      # way out.
      if [ "$FLASK_ENV" = "DEV" ]; then
          echo "Running FastAPI in development mode"
          uvicorn main:app --host 0.0.0.0 --port "${FLASK_RUN_PORT}" --reload &
      elif [ "$FLASK_ENV" = "PROD" ]; then
          uvicorn main:app --host 0.0.0.0 --port "${FLASK_RUN_PORT}" \
                 --workers "${GUNICORN_WORKERS}" --no-access-log &
      else
          echo "Unknown FLASK_ENV: ${FLASK_ENV}"
          exit 1
      fi
  elif [ "$ARCHIHUB_STACK" = "flask" ]; then
      if [ "$FLASK_ENV" = "DEV" ]; then
          echo "Running Flask in development mode"
          flask run --host=0.0.0.0 &
      elif [ "$FLASK_ENV" = "PROD" ]; then
          gunicorn -w ${GUNICORN_WORKERS} -b 0.0.0.0:${FLASK_RUN_PORT} app:app &
      else
          echo "Unknown FLASK_ENV: ${FLASK_ENV}"
          exit 1
      fi
  else
      echo "Unknown ARCHIHUB_STACK: ${ARCHIHUB_STACK} (expected 'flask' or 'fastapi')"
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