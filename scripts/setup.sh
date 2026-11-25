#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
[ ! -e .env ] || { echo '.env exists; edit it directly'; exit 1; }
echo 'Choose your dashboard password; copy the bcrypt output.'
docker run --rm -it caddy:2-alpine@sha256:953131cfea8e12bfe1c631a36308e9660e4389f0c3dfb3be957044d3ac92d446 caddy hash-password
printf 'Paste bcrypt hash: '
read -r dashboard_hash
case "$dashboard_hash" in '$2'*) ;; *) echo 'Expected bcrypt hash'; exit 1;; esac
umask 077
python3 - "$dashboard_hash" <<'PYINNER'
from pathlib import Path
import secrets,sys
s=Path('.env.example').read_text().replace('POSTGRES_PASSWORD=replace-with-random-hex','POSTGRES_PASSWORD='+secrets.token_hex(32)).replace('INTERNAL_API_TOKEN=replace-with-random-hex','INTERNAL_API_TOKEN='+secrets.token_hex(32)).replace('replace-with-caddy-bcrypt-hash',sys.argv[1])
Path('.env').write_text(s)
PYINNER
