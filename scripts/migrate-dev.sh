#!/bin/bash
cd "$(dirname "$0")/.." || exit
export ENV_FILE=.env
python manage.py makemigrations
python manage.py migrate
python manage.py migrate --database=base_dev