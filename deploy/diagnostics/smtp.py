"""Read-only relay diagnosis. Never print passwords or send messages."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys

config = json.load(sys.stdin)
for key in ('STALWART_ADMIN_AUTHORIZATION', 'STALWART_ADMIN_URL'):
    if key in config:
        os.environ[key] = config[key]
spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('install.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
client = installer.Client()
ids = client.call('x:MtaRoute/query', {})['ids']
routes = client.call('x:MtaRoute/get', {'ids': ids})['list']
for route in routes:
    if route.get('@type') != 'Relay':
        continue
    host, port = route.get('address', ''), route.get('port', 0)
    username = route.get('authUsername') or ''
    credential = route.get('authSecret') or {}
    secret = credential.get('secret') if isinstance(credential, dict) else None
    print('RELAY', json.dumps({
        'name': route.get('name'), 'host': host, 'port': port,
        'username_suffix': username[-4:], 'username_length': len(username),
        'username_is_access_key_id': username.startswith('AKIA') and len(username) == 20,
        'implicit_tls': route.get('implicitTls'),
        'allow_invalid_certs': route.get('allowInvalidCerts'),
        'secret_type': credential.get('@type') if isinstance(credential, dict) else 'unknown',
        'secret_length': len(secret) if isinstance(secret, str) else None,
        'secret_has_whitespace': any(c.isspace() for c in secret) if isinstance(secret, str) else None,
    }), flush=True)
    if not isinstance(secret, str) or not secret or set(secret) == {'*'}:
        print('AUTH_CHECK_SKIPPED: secret unavailable or masked', flush=True)
        continue
    try:
        decoded = base64.b64decode(secret, validate=True)
        print('PASSWORD_FORMAT', json.dumps({'classic_ses_format': len(decoded) == 33 and decoded[0] == 4}), flush=True)
    except Exception:
        print('PASSWORD_FORMAT not_classic_ses_base64', flush=True)
    # Only authenticate to the intended SES service. Send no envelope or DATA.
    if host != 'email-smtp.us-east-1.amazonaws.com' or port not in (465, 587):
        print('AUTH_CHECK_SKIPPED: endpoint not the configured SES target', flush=True)
        continue
    try:
        if route.get('implicitTls'):
            smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        try:
            code, _ = smtp.login(username, secret)
            print('SAVED_CREDENTIAL_AUTH_RESULT', code, flush=True)
        finally:
            smtp.close()
    except smtplib.SMTPAuthenticationError as error:
        print('SAVED_CREDENTIAL_AUTH_RESULT', error.smtp_code, flush=True)
    except Exception as error:
        print('SAVED_CREDENTIAL_AUTH_ERROR', type(error).__name__, flush=True)
