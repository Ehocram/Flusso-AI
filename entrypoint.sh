#!/bin/sh
set -e
echo "[entrypoint] applico le migrazioni…"
python manage.py migrate --noinput
if [ "${SEED_DEMO:-0}" = "1" ]; then
  echo "[entrypoint] seed dimostrativo…"
  python manage.py seed_demo || true
fi
echo "[entrypoint] avvio gunicorn…"
exec gunicorn config.wsgi:application -c gunicorn.conf.py
