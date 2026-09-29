#!/bin/sh
set -e

echo "Applying migrations..."
alembic upgrade head

if [ "${SEED_ON_START:-false}" = "true" ]; then
  python seed_data.py --if-empty
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
