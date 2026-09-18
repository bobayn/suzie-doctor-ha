#!/usr/bin/with-contenv bashio
set -euo pipefail
echo "Suzie Doctor run.sh entered"
exec /usr/bin/python3 /app/main.py
