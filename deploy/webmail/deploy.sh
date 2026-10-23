#!/usr/bin/env bash
# Activate an uploaded webmail ZIP without rebuilding/restarting the mail server.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bundle=${1:?Usage: deploy/webmail/deploy.sh BUNDLE COMMIT_SHA}
revision=${2:?Usage: deploy/webmail/deploy.sh BUNDLE COMMIT_SHA}
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || { echo "Expected a full commit SHA." >&2; exit 2; }
[ -f "$bundle" ] || { echo "Webmail bundle not found." >&2; exit 1; }
bundle="$(cd "$(dirname "$bundle")" && pwd)/$(basename "$bundle")"
cd "${STALWART_DEPLOY_ROOT:-$HOME/stalwart}"
# Match the server deployer's lock, including manual deployments.
exec 9> .stalwart-deploy.lock
flock -w 900 9 || { echo "Another deployment still holds the lock." >&2; exit 1; }
# Discover the running service without changing or reading its Git checkout.
container=$(docker ps --filter label=com.docker.compose.project=stalwart --filter label=com.docker.compose.service=mail --format '{{.ID}}')
[ -n "$container" ] && [[ "$container" != *$'\n'* ]] || {
  echo "Expected exactly one running Stalwart mail container." >&2; exit 1;
}
python3 "$script_dir/install.py" --container "$container" --bundle "$bundle" --revision "$revision"
