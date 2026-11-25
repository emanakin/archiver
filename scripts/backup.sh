#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db pg_dump -U archive -d archive -Fc > "backups/archive-$stamp.dump"
test -s "backups/archive-$stamp.dump"
echo "Created backups/archive-$stamp.dump; copy to encrypted off-host storage."
