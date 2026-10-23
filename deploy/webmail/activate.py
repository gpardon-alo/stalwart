#!/usr/bin/env python3
"""Receive admin configuration over SSH stdin and activate an uploaded bundle."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def activate(staging, revision, config):
    staging = Path(staging)
    if not re.fullmatch(r'[a-f0-9]{40}', revision) or not re.fullmatch(
        r'stalwart-webmail-' + revision + r'-[0-9]+-[0-9]+', staging.name
    ):
        raise ValueError('Unexpected staging directory or deployment revision')
    if staging.is_symlink() or staging.resolve().parent != Path('/tmp').resolve():
        raise ValueError('Staging must be a direct temporary directory')
    allowed = {'STALWART_ADMIN_AUTHORIZATION', 'STALWART_ADMIN_URL', 'STALWART_WEBMAIL_PREFIX'}
    if not isinstance(config, dict) or set(config) - allowed or not all(isinstance(v, str) for v in config.values()):
        raise ValueError('Unexpected deployment configuration')
    try:
        subprocess.run([
            'bash', str(staging / 'deploy.sh'), str(staging / 'bulwark-lite-stalwart.zip'), revision,
        ], env={**os.environ, **config}, check=True)
    finally:
        shutil.rmtree(staging)


if __name__ == '__main__':
    activate(sys.argv[1], sys.argv[2], json.load(sys.stdin))
