#!/bin/bash

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

echo "Elasticsearch is up!"