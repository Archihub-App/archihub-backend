#!/bin/bash

cd /app

if [ "$FLASK_ENV" = "DEV" ]; then
    echo "Running Flask in development mode"
    flask run --host=0.0.0.0
    sleep 600000
else if [ "$FLASK_ENV" = "PROD" ]; then
    gunicorn -w 10 -b 0.0.0.0:${FLASK_RUN_PORT} app:app