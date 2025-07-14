#!/bin/bash

cd /app

# -------- WAIT FOR ELASTICSEARCH --------
echo "Waiting for Elasticsearch to be healthy..."

until curl -s -f "${ELASTIC_DOMAIN}:${ELASTIC_PORT}/_cluster/health?wait_for_status=yellow&timeout=1s"; do
  >&2 echo "Elasticsearch is unavailable - sleeping"
  sleep 5
done

# -------- START BACKEND --------
echo "Elasticsearch is up!"
if [ "$FLASK_ENV" = "DEV" ]; then
    echo "Running Flask in development mode"
    flask run --host=0.0.0.0
    sleep 600000
elif [ "$FLASK_ENV" = "PROD" ]; then
    gunicorn -w ${GUNICORN_WORKERS} -b 0.0.0.0:${FLASK_RUN_PORT} app:app
fi