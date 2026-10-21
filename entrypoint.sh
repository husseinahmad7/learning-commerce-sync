#!/bin/sh
set -eu

if [ "$1" = "web" ]; then
    python manage.py migrate --noinput
    python manage.py seed_demo
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --worker-tmp-dir /tmp --access-logfile - --error-logfile -
fi

exec "$@"
