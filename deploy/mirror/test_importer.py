import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from importer import ImportRejected, Jmap, Mirror, SameOriginRedirect, MAIL


class FakeJmap:
    def __init__(self):
        self.markers = set()
        self.imported = []
        self.fail = None
        self.upload = Mock(return_value='blob')

    def exists(self, marker):
        return marker in self.markers

    def import_blob(self, blob, marker):
        self.imported.append(marker)
        if self.fail == 'reject':
            raise ImportRejected()
        if self.fail == 'timeout-before':
            raise TimeoutError()
        self.markers.add(marker)
        if self.fail == 'timeout-after':
            raise TimeoutError()


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {'bucket': 'bucket', 'prefixes': ['taisventures/', 'boardly/'],
            'topic_arn': 'topic', 'jmap_url': 'https://mail.example.com',
            'mailboxes': {'a': {}, 'b': {}},
            'routes': {'alias@example.com': ['a'], 'bcc@example.com': ['b'], 'user@example.com': ['a']}}
        self.clients = {'a': FakeJmap(), 'b': FakeJmap()}
        self.s3 = Mock()
        self.s3.get_object.side_effect = lambda **kwargs: {'ContentLength': 100,
            'Body': io.BytesIO(b'From: sender@example.net\r\nTo: visible@example.net\r\n\r\nSecret body')}
        self.mirror = Mirror(self.config, self.s3, str(Path(self.temp.name) / 'state.db'),
            lambda url, credentials: self.clients['a' if credentials is self.config['mailboxes']['a'] else 'b'])
        self.addCleanup(self.mirror.db.close)
        self.event = {'notificationType': 'Received', 'receipt': {
            'recipients': ['alias@example.com', 'bcc@example.com', 'user@example.com'],
            'virusVerdict': {'status': 'PASS'},
            'action': {'type': 'S3', 'bucketName': 'bucket', 'objectKey': 'taisventures/message'}}}

    def test_alias_bcc_fanout_and_duplicate_notifications(self):
        self.mirror.process(self.event)
        self.mirror.process(self.event)
        self.assertEqual(len(self.clients['a'].imported), 1)
        self.assertEqual(len(self.clients['b'].imported), 1)
        self.assertEqual(self.s3.get_object.call_count, 1)

    def test_unknown_recipient_does_not_partially_import(self):
        self.event['receipt']['recipients'].append('unknown@example.com')
        with self.assertRaises(ValueError): self.mirror.process(self.event)
        self.s3.get_object.assert_not_called()

    def test_wrong_bucket_prefix_or_virus_are_held(self):
        for field, value in [('bucketName', 'other'), ('objectKey', 'other/message')]:
            event = json.loads(json.dumps(self.event))
            event['receipt']['action'][field] = value
            with self.assertRaises(ValueError): self.mirror.process(event)
        self.event['receipt']['virusVerdict']['status'] = 'FAIL'
        with self.assertRaises(ValueError): self.mirror.process(self.event)
        self.s3.get_object.assert_not_called()

    def test_timeout_after_commit_reconciles_without_duplicate(self):
        self.clients['a'].fail = 'timeout-after'
        with self.assertRaises(TimeoutError): self.mirror.process(self.event)
        self.clients['a'].fail = None
        self.mirror.process(self.event)
        self.assertEqual(len(self.clients['a'].imported), 1)
        self.assertEqual(len(self.clients['b'].imported), 1)

    def test_uncertain_import_does_not_blindly_retry(self):
        self.clients['a'].fail = 'timeout-before'
        with self.assertRaises(TimeoutError): self.mirror.process(self.event)
        self.clients['a'].fail = None
        with self.assertRaises(RuntimeError): self.mirror.process(self.event)
        self.assertEqual(len(self.clients['a'].imported), 1)

    def test_definitive_rejection_is_retryable(self):
        self.clients['a'].fail = 'reject'
        with self.assertRaises(ImportRejected): self.mirror.process(self.event)
        self.clients['a'].fail = None
        self.mirror.process(self.event)
        self.assertEqual(len(self.clients['a'].imported), 2)

    def test_sns_topic_allowlist(self):
        event = {'Type': 'Notification', 'TopicArn': 'wrong', 'Message': json.dumps(self.event)}
        with self.assertRaises(ValueError): self.mirror.process(event)
        event['TopicArn'] = 'topic'
        self.mirror.process(event)

    def test_upload_is_original_message_not_resubmission(self):
        self.mirror.process(self.event)
        raw = self.clients['a'].upload.call_args.args[0]
        self.assertIn(b'To: visible@example.net', raw)
        self.assertNotIn(b'bcc@example.com', raw)

    def test_cross_origin_urls_refuse_credentials(self):
        client = Jmap.__new__(Jmap)
        from urllib.parse import urlsplit
        client.origin = urlsplit('https://mail.example.com')
        with self.assertRaises(ValueError): client.request('https://attacker.example/upload')

    def test_redirects_stay_on_the_trusted_https_origin(self):
        from urllib.request import Request
        handler = SameOriginRedirect()
        request = Request('https://mail.example.com/.well-known/jmap')
        redirected = handler.redirect_request(request, None, 307, '', {}, 'https://mail.example.com/jmap/session')
        self.assertEqual(redirected.full_url, 'https://mail.example.com/jmap/session')
        for destination in ['https://other.example/session', 'http://mail.example.com/session']:
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 307, '', {}, destination)

    def test_real_jmap_request_shapes(self):
        http = Mock()
        http.open.side_effect = [io.BytesIO(json.dumps(value).encode()) for value in [
            {'primaryAccounts': {MAIL: 'a'}, 'apiUrl': 'https://mail.example.com/jmap/',
             'uploadUrl': 'https://mail.example.com/jmap/upload/{accountId}/'},
            {'methodResponses': [['Mailbox/query', {'ids': ['inbox']}, 'mirror']]},
            {'methodResponses': [['Email/query', {'ids': []}, 'mirror']]},
            {'blobId': 'blob'},
            {'methodResponses': [['Email/import', {'created': {'mirror': {'id': 'email'}}}, 'mirror']]}
        ]]
        with patch('importer.build_opener', return_value=http):
            client = Jmap('https://mail.example.com', {'username': 'user', 'password': 'test-only'})
            self.assertFalse(client.exists('marker'))
            blob = client.upload(b'From: test@example.net\r\n\r\nbody')
            client.import_blob(blob, 'marker')
        request = http.open.call_args.args[0]
        method = json.loads(request.data)['methodCalls'][0]
        self.assertEqual(method[0], 'Email/import')
        self.assertEqual(method[1]['emails']['mirror']['keywords'], {'marker': True})
        self.assertEqual(method[1]['emails']['mirror']['mailboxIds'], {'inbox': True})


if __name__ == '__main__': unittest.main()
