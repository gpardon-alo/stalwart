#!/usr/bin/env bash
# Build the customized Bulwark Lite bundle in a disposable checkout.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_revision=d1f4ccaccff840a43471d40e645453e0dcfcfef4
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/stalwart-webmail.XXXXXX")"
trap 'rm -rf "$build_dir"' EXIT

git clone https://github.com/bulwarkmail/webmail.git "$build_dir/source"
cd "$build_dir/source"
git checkout --detach "$source_revision"
git apply --check "$script_dir/collapsed-authentication.patch"
git apply "$script_dir/collapsed-authentication.patch"
npm ci
npx vitest run lib/__tests__/email-headers.test.ts components/email/__tests__/viewer-plugin-slot-remount.test.tsx
npx tsc --noEmit
CI=true LITE_TARGET=stalwart GIT_COMMIT="$source_revision" npm run build:lite
# Give the deployed entry page an exact revision marker for the health check.
python3 - "$build_dir/source/bulwark-lite-stalwart.zip" <<'PYTHON'
import os, re, sys, zipfile
from pathlib import Path
archive = Path(sys.argv[1])
revision = os.environ.get('WEBMAIL_DEPLOY_REVISION', 'local')
if not re.fullmatch(r'[a-f0-9]{40}|local', revision):
    raise SystemExit('WEBMAIL_DEPLOY_REVISION must be a full Git commit SHA')
with zipfile.ZipFile(archive) as source:
    entries = [(entry, source.read(entry)) for entry in source.infolist()]
if not any(entry.filename == 'index.html' for entry, _ in entries):
    raise SystemExit('Webmail bundle has no index.html')
with zipfile.ZipFile(archive, 'w') as output:
    for entry, data in entries:
        if entry.filename == 'index.html':
            marker = f'<meta name="stalwart-webmail-revision" content="{revision}">'
            text = data.decode()
            if '</head>' not in text:
                raise SystemExit('Webmail entry page has no closing head')
            data = text.replace('</head>', marker + '</head>', 1).encode()
        output.writestr(entry, data)
PYTHON
mkdir -p "$script_dir/dist"
cp bulwark-lite-stalwart.zip "$script_dir/dist/"
printf 'Built %s/dist/bulwark-lite-stalwart.zip\n' "$script_dir"
