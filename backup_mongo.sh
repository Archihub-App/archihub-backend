#!/bin/bash

# >&2 echo "Script to be started"

# INDEX_ARR=(sim-backend-$ENVIRONMENT_NAME)

# for index in "${INDEX_ARR[@]}"
# do
#   curl --cacert /stash/elastic_certs/ca/ca.crt -XPUT "https://$ELASTIC_DOMAIN:$ELASTIC_PORT/$index?pretty" -u $ELASTIC_USER:$ELASTIC_PASSWORD
#   curl -XPUT "http://$ELASTIC_DOMAIN:$ELASTIC_PORT/$index?pretty" -u $ELASTIC_USER:$ELASTIC_PASSWORD
#   >&2 echo "Index $index created"
# done

# SETTINGS_ARR=(sim-backend-$ENVIRONMENT_NAME)

# for settings in "${SETTINGS_ARR[@]}"
# do
#   curl -XPOST "http://$ELASTIC_DOMAIN:$ELASTIC_PORT/$settings/_close?pretty" -u $ELASTIC_USER:$ELASTIC_PASSWORD
#   curl -XPUT "http://$ELASTIC_DOMAIN:$ELASTIC_PORT/$settings/_settings?pretty" -H 'Content-Type: application/json' -d "@./elastic_mappings/settings-$settings.json" -u $ELASTIC_USER:$ELASTIC_PASSWORD
#   curl -XPOST "http://$ELASTIC_DOMAIN:$ELASTIC_PORT/$settings/_open?pretty" -u $ELASTIC_USER:$ELASTIC_PASSWORD
#   >&2 echo "Settings for $settings updated"
# done

# MAPPINGS_ARR=(sim-backend-$ENVIRONMENT_NAME)

# for mapping in "${MAPPINGS_ARR[@]}"
# do
#   curl -XPUT "http://$ELASTIC_DOMAIN:$ELASTIC_PORT/$mapping/_mapping?pretty" -H 'Content-Type: application/json' -d "@./elastic_mappings/mapping-$mapping.json" -u $ELASTIC_USER:$ELASTIC_PASSWORD
#   >&2 echo "Mappings for $mapping setted"
# done

# >&2 echo "Iniciando el servidor de procesos"
# python -m nltk.downloader stopwords
# python app.py

cd /app
flask run --host=0.0.0.0

sleep 60000