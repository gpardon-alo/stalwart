#!/usr/bin/env bash
# Upload through OpenSSH using the exact Ed25519 key pinned for Lightsail.
set -euo pipefail
bundle_dir=${1:?Usage: deploy/webmail/upload.sh BUNDLE_DIRECTORY}
: "${SSH_HOST:?Set SSH_HOST}" "${SSH_USER:?Set SSH_USER}" "${SSH_PRIVATE_KEY:?Set SSH_PRIVATE_KEY}"
: "${SSH_FINGERPRINT:?Set SSH_FINGERPRINT}" "${DEPLOY_SHA:?Set DEPLOY_SHA}" "${DEPLOY_RUN:?Set DEPLOY_RUN}"
[[ "$DEPLOY_SHA" =~ ^[a-f0-9]{40}$ && "$DEPLOY_RUN" =~ ^[0-9]+-[0-9]+$ ]] || exit 2
[[ "$SSH_HOST" =~ ^[A-Za-z0-9.-]+$ && "$SSH_USER" =~ ^[A-Za-z0-9_-]+$ ]] || exit 2
private_dir=$(mktemp -d)
trap 'rm -rf "$private_dir"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$private_dir/key"
chmod 600 "$private_dir/key"
ssh-keyscan -T 20 -t ed25519 "$SSH_HOST" > "$private_dir/known_hosts" 2>/dev/null
fingerprint=$(ssh-keygen -lf "$private_dir/known_hosts" -E sha256 | awk '{print $2}')
[ "$fingerprint" = "$SSH_FINGERPRINT" ] || {
  echo "The Lightsail Ed25519 host key does not match the trusted fingerprint (observed: $fingerprint)." >&2; exit 1;
}
options=(-i "$private_dir/key" -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$private_dir/known_hosts" -o HostKeyAlgorithms=ssh-ed25519 -o ConnectTimeout=20)
remote="$SSH_USER@$SSH_HOST"
staging="/tmp/stalwart-webmail-$DEPLOY_SHA-$DEPLOY_RUN"
ssh "${options[@]}" "$remote" "umask 077; mkdir -p '$staging'"
scp "${options[@]}" "$bundle_dir/"* "$remote:$staging/"
# Send admin configuration over encrypted stdin, rather than a remote command
# line or a credential file on Lightsail. The activation helper removes staging.
python3 - "$private_dir/config.json" <<'PYTHON'
import json, os, sys
keys = ('STALWART_ADMIN_AUTHORIZATION', 'STALWART_ADMIN_URL', 'STALWART_WEBMAIL_PREFIX')
with open(sys.argv[1], 'w') as output:
    json.dump({key: os.environ[key] for key in keys if key in os.environ}, output)
PYTHON
ssh "${options[@]}" "$remote" "python3 '$staging/activate.py' '$staging' '$DEPLOY_SHA'" < "$private_dir/config.json"
