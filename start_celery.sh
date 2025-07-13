#!/bin/bash

# -------- WAIT FOR ELASTICSEARCH --------
echo "Waiting for Elasticsearch to be healthy..."

until curl -s -f "${ELASTIC_DOMAIN}:${ELASTIC_PORT}/_cluster/health?wait_for_status=yellow&timeout=1s"; do
  >&2 echo "Elasticsearch is unavailable - sleeping"
  sleep 5
done

echo "Elasticsearch is up!"