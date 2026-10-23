#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
image=${1:?Usage: deploy/lightsail/deploy.sh IMAGE_DIGEST}
# Restrict production images to this user's fork and immutable digests.
[[ "$image" =~ ^ghcr\.io/gpardon-alo/stalwart@sha256:[a-f0-9]{64}$ ]] || {
  echo "Expected a ghcr.io/gpardon-alo/stalwart image digest." >&2; exit 2;
}
[ -f .env.production ] || { echo "Create .env.production from the example first." >&2; exit 1; }
# Serialize manual and Actions deployments on the server.
exec 9> .stalwart-deploy.lock
flock -n 9 || { echo "Another deployment is in progress." >&2; exit 1; }
compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
"${compose[@]}" config --quiet
: "${DEPLOY_SHA:?Set DEPLOY_SHA to the deployed commit}"
python3 deploy/webmail/install.py --check
# Download before the maintenance window; a pull failure leaves mail running.
STALWART_IMAGE="$image" "${compose[@]}" pull mail
if [ -n "$("${compose[@]}" ps -a -q mail)" ]; then
  bash deploy/lightsail/backup.sh
fi
# Record what is running only after the new container is healthy.
STALWART_IMAGE="$image" "${compose[@]}" up -d --wait --wait-timeout 180 mail
python3 - "$image" <<'PYTHON'
import os, pathlib, sys
path = pathlib.Path('.env.production')
lines = path.read_text().splitlines()
lines = [line for line in lines if not line.startswith('STALWART_IMAGE=')]
lines.append('STALWART_IMAGE=' + sys.argv[1])
tmp = path.with_name('.env.production.tmp')
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write('\n'.join(lines) + '\n')
os.replace(tmp, path)
PYTHON
# Keep webmail activation under the same deployment lock as the mail server.
container=$("${compose[@]}" ps -q mail)
python3 deploy/webmail/install.py --container "$container" --revision "${DEPLOY_SHA:?Set DEPLOY_SHA to the deployed commit}"
"${compose[@]}" ps
