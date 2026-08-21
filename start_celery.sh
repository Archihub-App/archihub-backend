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
  
  until curl -s -f -u "${ELASTIC_USER}:${ELASTIC_PASSWORD}" "${ELASTIC_DOMAIN}:${ELASTIC_PORT}/_cluster/health?wait_for_status=yellow&timeout=1s" --cacert "$ELASTIC_CERT"; do
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

echo "Elasticsearch is up!"

CELERY_RUN_MODE="${CELERY_RUN_MODE:-worker}"
CELERY_LOGLEVEL="${CELERY_LOGLEVEL:-INFO}"
CELERY_QUEUES="${CELERY_QUEUES:-}"
CELERY_APP="${CELERY_APP:-archihub.worker.celery_app}"
CELERY_BEAT_SCHEDULE_FILE="${CELERY_BEAT_SCHEDULE_FILE:-/tmp/celerybeat-schedule}"
CELERY_EXTRA_ARGS="${CELERY_EXTRA_ARGS:-}"

build_celery_command() {
  if [[ "$CELERY_RUN_MODE" == "beat" ]]; then
    echo "CELERY_WORKER=1 celery --app ${CELERY_APP} beat --loglevel ${CELERY_LOGLEVEL} --schedule ${CELERY_BEAT_SCHEDULE_FILE} ${CELERY_EXTRA_ARGS}"
  else
    if [[ -z "$CELERY_QUEUES" ]]; then
      echo "CELERY_WORKER=1 celery --app ${CELERY_APP} worker --loglevel ${CELERY_LOGLEVEL} ${CELERY_EXTRA_ARGS}"
    else
      echo "CELERY_WORKER=1 celery --app ${CELERY_APP} worker -Q ${CELERY_QUEUES} --loglevel ${CELERY_LOGLEVEL} ${CELERY_EXTRA_ARGS}"
    fi
  fi
}

while true; do
  command="$(build_celery_command)"
  echo "Starting Celery with mode ${CELERY_RUN_MODE}, app ${CELERY_APP}"
  eval "$command" &
  child_pid=$!
  wait "$child_pid"
  exit_code=$?
  child_pid=""

  if [[ "$restart_requested" -eq 1 ]]; then
    echo "Restart request received, starting Celery again"
    restart_requested=0
    continue
  fi

  if [[ "$stop_requested" -eq 1 ]]; then
    exit "$exit_code"
  fi

  exit "$exit_code"
done