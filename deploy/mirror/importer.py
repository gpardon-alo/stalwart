"""Copy SES S3 deliveries into explicitly mapped JMAP inboxes; never resend mail."""
import base64
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CORE = 'urn:ietf:params:jmap:core'
MAIL = 'urn:ietf:params:jmap:mail'
LOG = logging.getLogger('mail-mirror')


class ImportRejected(Exception):
    pass


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original, destination = urlsplit(req.full_url), urlsplit(newurl)
        if (original.scheme, original.netloc) != (destination.scheme, destination.netloc):
            raise ValueError('Cross-origin redirects are not allowed for authenticated requests')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Jmap:
    def __init__(self, url, credentials):
        self.origin = urlsplit(url)
        if self.origin.scheme != 'https' or self.origin.username or self.origin.query or self.origin.fragment:
            raise ValueError('JMAP requires a trusted HTTPS base URL')
        self.base = url.rstrip('/')
        self.authorization = 'Basic ' + base64.b64encode(
            (credentials['username'] + ':' + credentials['password']).encode()).decode()
        self.http = build_opener(SameOriginRedirect())
        self.session = self.request(self.base + '/.well-known/jmap')
        self.account = self.session['primaryAccounts'][MAIL]
        self.api = self.session['apiUrl']
        inboxes = self.call('Mailbox/query', {'filter': {'role': 'inbox'}})['ids']
        if len(inboxes) != 1:
            raise ValueError('Expected exactly one Inbox')
        self.inbox = inboxes[0]

    def request(self, url, data=None, content_type='application/json'):
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) != (self.origin.scheme, self.origin.netloc):
            raise ValueError('Refusing to send credentials to another origin')
        request = Request(url, data=data, headers={
            'Authorization': self.authorization, 'Content-Type': content_type})
        try:
            with self.http.open(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            # Do not include response bodies, authentication data or message contents.
            raise RuntimeError(f'JMAP HTTP status {exc.code}') from None

    def call(self, method, arguments):
        result = self.request(self.api, json.dumps({
            'using': [CORE, MAIL],
            'methodCalls': [[method, {'accountId': self.account, **arguments}, 'mirror']]
        }).encode())
        name, response, call_id = result['methodResponses'][0]
        if name != method or call_id != 'mirror':
            raise RuntimeError('JMAP method failed')
        return response

    def exists(self, marker):
        return bool(self.call('Email/query', {'filter': {'hasKeyword': marker}, 'limit': 1})['ids'])

    def upload(self, raw):
        url = self.session['uploadUrl'].replace('{accountId}', quote(self.account, safe=''))
        return self.request(url, raw, 'message/rfc822')['blobId']

    def import_blob(self, blob, marker):
        result = self.call('Email/import', {'emails': {'mirror': {
            'blobId': blob, 'mailboxIds': {self.inbox: True}, 'keywords': {marker: True}
        }}})
        if 'mirror' not in result.get('created', {}):
            raise ImportRejected('Email/import rejected the message')


class Mirror:
    def __init__(self, config, s3, database, factory=Jmap):
        self.config, self.s3, self.factory = config, s3, factory
        self.db = sqlite3.connect(database)
        self.db.execute('CREATE TABLE IF NOT EXISTS deliveries (marker TEXT PRIMARY KEY, status TEXT NOT NULL)')
        self.clients = {}

    def process(self, event):
        if event.get('Type') == 'Notification':
            if event['TopicArn'] != self.config['topic_arn']:
                raise ValueError('Unexpected SNS topic')
            event = json.loads(event['Message'])
        if event.get('notificationType') != 'Received':
            raise ValueError('Expected an SES receipt notification')
        receipt = event['receipt']
        action = receipt['action']
        if action.get('type') != 'S3':
            raise ValueError('Expected the SES S3-action notification, not an S3 ObjectCreated event')
        bucket, key = action['bucketName'], action['objectKey']
        if bucket != self.config['bucket'] or not any(key.startswith(p) for p in self.config['prefixes']):
            raise ValueError('Object outside configured bucket/prefix allowlist')
        if receipt.get('virusVerdict', {}).get('status') != 'PASS':
            raise ValueError('Virus scan did not pass; message held for review')
        # Envelope recipients include aliases and BCC; never guess from To/Cc headers.
        recipients = receipt['recipients']
        if not recipients:
            raise ValueError('Missing envelope recipients')
        targets = set()
        for recipient in recipients:
            routes = self.config['routes'].get(recipient.lower())
            if not routes:
                raise ValueError('Unmapped envelope recipient; held for explicit mapping')
            targets.update(routes)
        if not targets:
            raise ValueError('No mapped mailbox targets')
        raw = None
        for target in sorted(targets):
            marker = 'mirror-' + hashlib.sha256(json.dumps([bucket, key, target]).encode()).hexdigest()
            previous = self.db.execute('SELECT status FROM deliveries WHERE marker=?', (marker,)).fetchone()
            if previous == ('done',):
                continue
            if target not in self.clients:
                self.clients[target] = self.factory(self.config['jmap_url'], self.config['mailboxes'][target])
            client = self.clients[target]
            if client.exists(marker):
                self.set_status(marker, 'done')
                continue
            if previous == ('pending',):
                # A lost response could mean the server imported it. Never blindly retry.
                raise RuntimeError('Uncertain prior import; reconcile marker ' + marker)
            if raw is None:
                obj = self.s3.get_object(Bucket=bucket, Key=key)
                if obj['ContentLength'] > self.config.get('max_message_bytes', 40_000_000):
                    obj['Body'].close()
                    raise ValueError('Message exceeds configured import limit')
                with obj['Body'] as stream:
                    raw = stream.read(self.config.get('max_message_bytes', 40_000_000) + 1)
                if len(raw) > self.config.get('max_message_bytes', 40_000_000):
                    raise ValueError('Message exceeds configured import limit')
                if b'\x00' in raw[:200] or b'\n' not in raw[:8192]:
                    raise ValueError('Not a plaintext RFC822 message; check SES client-side encryption')
            blob = client.upload(raw)
            self.set_status(marker, 'pending')
            try:
                client.import_blob(blob, marker)
            except ImportRejected:
                self.db.execute('DELETE FROM deliveries WHERE marker=?', (marker,))
                self.db.commit()
                raise
            self.set_status(marker, 'done')

    def set_status(self, marker, status):
        self.db.execute('INSERT OR REPLACE INTO deliveries VALUES (?,?)', (marker, status))
        self.db.commit()


def main():
    import boto3
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    os.umask(0o077)
    state = Path('/state')
    state.mkdir(exist_ok=True)
    # One importer per persistent state volume; a second instance fails closed.
    lock = (state / 'worker.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = json.loads(Path('/run/secrets/mailboxes.json').read_text())
    if not config.get('topic_arn') or not config.get('queue_url') or not config.get('routes'):
        raise ValueError('Configure topic, queue and explicit recipient mappings first')
    sqs = boto3.client('sqs', region_name='us-east-1')
    s3 = boto3.client('s3', region_name=config.get('bucket_region', 'us-west-1'))
    mirror = Mirror(config, s3, str(state / 'deliveries.sqlite3'))
    dry_run = os.environ.get('MIRROR_DRY_RUN', 'true').lower() != 'false'
    LOG.info('Mirror started; dry_run=%s', dry_run)
    while True:
        try:
            if dry_run:
                # Do not receive: even an unacknowledged receive spends the DLQ retry budget.
                attributes = sqs.get_queue_attributes(QueueUrl=config['queue_url'],
                    AttributeNames=['ApproximateNumberOfMessages'])['Attributes']
                LOG.info('Dry run; queued notifications=%s', attributes['ApproximateNumberOfMessages'])
                time.sleep(60)
                continue
            messages = sqs.receive_message(QueueUrl=config['queue_url'], MaxNumberOfMessages=1,
                WaitTimeSeconds=20, VisibilityTimeout=900).get('Messages', [])
            for message in messages:
                try:
                    mirror.process(json.loads(message['Body']))
                except Exception as exc:
                    # Exception text from SDKs can include sensitive URLs; log only its type.
                    LOG.error('Delivery held for retry/review (%s), notification=%s',
                              type(exc).__name__, message['MessageId'])
                    continue
                sqs.delete_message(QueueUrl=config['queue_url'], ReceiptHandle=message['ReceiptHandle'])
                LOG.info('Notification imported or already present; notification=%s', message['MessageId'])
        except Exception as exc:
            LOG.error('Queue polling failed (%s)', type(exc).__name__)
            time.sleep(30)


if __name__ == '__main__':
    main()
