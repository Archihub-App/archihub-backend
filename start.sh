#!/bin/bash

cd /app
gunicorn -w 4 -b 0.0.0.0:80 app:app