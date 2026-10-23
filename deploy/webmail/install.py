#!/usr/bin/env python3
"""Activate the bundled webmail for the existing Stalwart Application."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the admin credential to a redirected host.


class Client:
    def __init__(self):
        self.base = os.environ.get('STALWART_ADMIN_URL', 'http://127.0.0.1:8080').rstrip('/')
        url = urllib.parse.urlsplit(self.base)
        if url.scheme not in ('http', 'https') or url.username or url.password or url.path not in ('', '/'):
            raise RuntimeError('STALWART_ADMIN_URL must be an HTTP(S) origin without credentials')
        if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise RuntimeError('Use HTTPS when the admin API is not on loopback')
        self.authorization = os.environ.get('STALWART_ADMIN_AUTHORIZATION', '')
        if not self.authorization.startswith(('Bearer ', 'Basic ')) or any(c in self.authorization for c in '\r\n'):
            raise RuntimeError('Set STALWART_ADMIN_AUTHORIZATION to a Basic or Bearer admin authorization header')
        self.opener = urllib.request.build_opener(NoRedirect)
        # Stalwart redirects discovery to this path. Request it directly so
        # admin credentials never need to follow an HTTP redirect.
        session = self.request('/jmap/session', authenticated=True)
        self.account = session.get('primaryAccounts', {}).get('urn:ietf:params:jmap:mail')
        if not self.account:
            accounts = session.get('accounts', {})
            personal = [key for key, value in accounts.items() if value.get('isPersonal')]
            self.account = personal[0] if personal else next(iter(accounts), None)
        if not self.account:
            raise RuntimeError('Admin session has no JMAP account')
        api = urllib.parse.urlsplit(session.get('apiUrl', '/jmap'))
        self.api_path = api.path or '/jmap'

    def request(self, path, payload=None, authenticated=False):
        headers = {'X-Forwarded-For': '127.0.0.1'}
        if authenticated:
            headers['Authorization'] = self.authorization
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        with self.opener.open(request, timeout=90) as response:
            body = response.read()
        return json.loads(body) if authenticated else body.decode()

    def call(self, method, args):
        args = {'accountId': self.account, **args}
        result = self.request(self.api_path, {
            'using': ['urn:ietf:params:jmap:core', 'urn:stalwart:jmap'],
            'methodCalls': [[method, args, 'deploy']],
        }, authenticated=True)
        responses = result.get('methodResponses', [])
        if len(responses) != 1 or responses[0][0] != method or responses[0][2] != 'deploy':
            raise RuntimeError(f'{method} returned a JMAP error or unexpected response')
        value = responses[0][1]
        if any(value.get(key) for key in ('notCreated', 'notUpdated', 'notDestroyed')):
            raise RuntimeError(f'{method} refused the requested change')
        return value

    def application(self, prefix):
        query = self.call('x:Application/query', {'calculateTotal': True})
        ids = query.get('ids', [])
        if query.get('total', len(ids)) > len(ids):
            raise RuntimeError('Application list is paginated; select the application manually')
        applications = self.call('x:Application/get', {'ids': ids}).get('list', []) if ids else []
        matches = [app for app in applications if prefix in {
            '/' + item.strip('/') for item, enabled in app.get('urlPrefix', {}).items() if enabled
        }]
        if len(matches) != 1:
            raise RuntimeError('Expected exactly one existing webmail Application at ' + prefix)
        if not matches[0].get('enabled', True):
            raise RuntimeError('The selected webmail Application is disabled')
        return matches[0]

    def activate(self, app, resource):
        value = self.call('x:Application/set', {'update': {app['id']: {'resourceUrl': resource}}})
        if app['id'] not in value.get('updated', {}):
            raise RuntimeError('Application update was not acknowledged')
        value = self.call('x:Action/set', {'create': {'reload': {'@type': 'UpdateApps'}}})
        if 'reload' not in value.get('created', {}):
            raise RuntimeError('UpdateApps was not acknowledged')


def install(client, app, prefix, container, revision, bundle_path=None):
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise RuntimeError('Deployment revision must be a full Git commit SHA')
    source = '/usr/local/share/stalwart/webmail.zip'
    bundle = (Path(bundle_path).read_bytes() if bundle_path else
              subprocess.run(['docker', 'exec', container, 'cat', source], check=True, capture_output=True).stdout)
    marker = f'<meta name="stalwart-webmail-revision" content="{revision}">'
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        if archive.testzip() is not None or marker not in archive.read('index.html').decode():
            raise RuntimeError('Bundled webmail does not match this deployment revision')
    # Persistent, revision-specific files keep the previous bundle available
    # for rollback, even after replacing the container image.
    directory = '/var/lib/stalwart/custom-webmail'
    target = directory + '/' + revision + '.zip'
    subprocess.run(['docker', 'exec', container, 'mkdir', '-p', directory], check=True)
    # Write as the container's normal user, then rename atomically. This
    # avoids unreadable ownership from docker cp and keeps partial uploads
    # from replacing a previously installed revision.
    subprocess.run([
        'docker', 'exec', '-i', container, 'sh', '-c',
        'umask 022; cat > "$1" && mv "$1" "$2"',
        'sh', target + '.upload', target,
    ], input=bundle, check=True)
    old_resource = app['resourceUrl']
    try:
        client.activate(app, 'file://' + target)
        for _ in range(12):
            try:
                if marker in client.request(prefix + '/'):
                    print('Customized webmail is serving deployment ' + revision)
                    return
            except (OSError, urllib.error.HTTPError):
                pass
            time.sleep(5)
        raise RuntimeError('Webmail did not serve the expected deployment revision')
    except Exception:
        try:
            client.activate(app, old_resource)
            print('Restored the previous webmail resource URL.', file=sys.stderr)
        except Exception:
            print('Webmail rollback failed; inspect the Application resource URL.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--container')
    parser.add_argument('--revision')
    parser.add_argument('--bundle', help='Uploaded webmail ZIP; does not require a bundled server image')
    args = parser.parse_args()
    prefix = '/' + os.environ.get('STALWART_WEBMAIL_PREFIX', '/webmail').strip('/')
    if not re.fullmatch(r'/[A-Za-z0-9_-]+', prefix) or prefix in ('/admin', '/account', '/jmap', '/auth', '/api'):
        raise RuntimeError('STALWART_WEBMAIL_PREFIX must identify the existing webmail application')
    client = Client()
    app = client.application(prefix)
    if args.check:
        print('Admin API and existing webmail Application are reachable.')
        return
    if not args.container or not args.revision:
        parser.error('--container and --revision are required unless --check is used')
    install(client, app, prefix, args.container, args.revision, args.bundle)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print request headers, response bodies, or credentials.
        print('Webmail deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
