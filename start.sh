#!/bin/bash

cd /app
gunicorn -w 10 -b 0.0.0.0:${FLASK_RUN_PORT} app:app