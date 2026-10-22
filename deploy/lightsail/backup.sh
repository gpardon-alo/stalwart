#!/usr/bin/env bash
# Offline snapshot of this stack's local RocksDB configuration and mail volumes.
set -euo pipefail
cd "$(dirname "$0")/../.."
umask 077
compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
mkdir -p backups
work=$(mktemp -d "$PWD/backups/.snapshot.XXXXXX")
archive="$PWD/backups/stalwart-$(date -u +%Y%m%dT%H%M%SZ)-${work##*.}.tar.gz"
restart=0
cleanup() {
  result=$?
  trap - EXIT
  if [ "$restart" = 1 ]; then
    if ! "${compose[@]}" start mail; then
      echo "Stalwart restart failed; start it manually immediately." >&2
      result=1
    fi
  fi
  rm -rf "$work"
  exit "$result"
}
trap cleanup EXIT
container=$("${compose[@]}" ps -a -q mail)
[ -n "$container" ] || { echo "No existing mail container to back up." >&2; exit 1; }
if [ "$(docker inspect --format '{{.State.Running}}' "$container")" = true ]; then
  restart=1
  "${compose[@]}" stop mail
fi
"${compose[@]}" cp mail:/etc/stalwart "$work/config"
"${compose[@]}" cp mail:/var/lib/stalwart "$work/data"
cp .env.production docker-compose.production.yml "$work/"
docker inspect --format '{{.Config.Image}}' "$container" > "$work/image.txt"
tar -czf "$work/archive.tar.gz" -C "$work" config data .env.production docker-compose.production.yml image.txt
mv "$work/archive.tar.gz" "$archive"
echo "Offline backup created: $archive"
