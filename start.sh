#!/bin/bash

cd /app

# -------- WAIT FOR ELASTICSEARCH --------
echo "Waiting for Elasticsearch to be healthy..."

if [ -n "$ELASTIC_CERT" ]; then
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

# -------- START BACKEND --------
echo "Elasticsearch is up!"
if [ "$FLASK_ENV" = "DEV" ]; then
    echo "Running Flask in development mode"
    flask run --host=0.0.0.0
    sleep 600000
elif [ "$FLASK_ENV" = "PROD" ]; then
    gunicorn -w ${GUNICORN_WORKERS} -b 0.0.0.0:${FLASK_RUN_PORT} app:app
fi