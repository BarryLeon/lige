#!/bin/bash
cd "$(dirname "$0")/.." || exit
export ENV_FILE=.env.prod
exec python manage.py runserver 0.0.0.0:8000 "$@"