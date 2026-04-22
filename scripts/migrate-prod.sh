#!/bin/bash
cd "$(dirname "$0")/.." || exit
export ENV_FILE=.env.prod
python3 manage.py makemigrations
python3 manage.py migrate
python3 manage.py migrate --database=default
python3 manage.py migrate --database=marchiquita
python3 manage.py migrate --database=carteles
python3 manage.py migrate --database=cobros_publivial

