#!/bin/bash

cd /app

if [ "$FLASK_ENV" = "DEV" ]; then
    echo "Running Flask in development mode"
    flask run --host=0.0.0.0
    sleep 600000
elif [ "$FLASK_ENV" = "PROD" ]; then
    gunicorn -w 10 -b 0.0.0.0:${FLASK_RUN_PORT} --certfile=./keys/cert.crt --keyfile=./keys/key.key app:app
fi