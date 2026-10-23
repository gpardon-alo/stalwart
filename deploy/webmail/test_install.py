import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

spec = importlib.util.spec_from_file_location('webmail_install', Path(__file__).with_name('install.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

REVISION = 'a' * 40
MARKER = f'<meta name="stalwart-webmail-revision" content="{REVISION}">'
APP = {'id': 'webmail', 'resourceUrl': 'https://example.test/previous.zip'}


def bundle(marker=MARKER):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('index.html', '<html><head>' + marker + '</head></html>')
    return subprocess.CompletedProcess([], 0, stdout=data.getvalue())


class DeploymentTests(unittest.TestCase):
    @patch.object(module.subprocess, 'run')
    def test_matching_bundle_activates_and_checks_served_revision(self, run):
        run.return_value = bundle()
        client = Mock()
        client.request.return_value = MARKER
        module.install(client, APP, '/webmail', 'container', REVISION)
        client.activate.assert_called_once_with(APP, f'file:///var/lib/stalwart/custom-webmail/{REVISION}.zip')
        client.request.assert_called_once_with('/webmail/')
        self.assertEqual(run.call_count, 3)

    @patch.object(module.subprocess, 'run')
    def test_wrong_bundle_cannot_change_application(self, run):
        run.return_value = bundle('wrong revision')
        client = Mock()
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            module.install(client, APP, '/webmail', 'container', REVISION)
        client.activate.assert_not_called()
        self.assertEqual(run.call_count, 1)

    @patch.object(module.time, 'sleep')
    @patch.object(module.subprocess, 'run')
    def test_old_page_after_reload_restores_previous_resource(self, run, sleep):
        run.return_value = bundle()
        client = Mock()
        client.request.return_value = 'previous page'
        with self.assertRaisesRegex(RuntimeError, 'expected deployment'):
            module.install(client, APP, '/webmail', 'container', REVISION)
        self.assertEqual(client.activate.call_args_list[-1].args, (APP, APP['resourceUrl']))
        self.assertEqual(client.request.call_count, 12)

    @patch.object(module.subprocess, 'run')
    def test_rejected_activation_restores_previous_resource(self, run):
        run.return_value = bundle()
        client = Mock()
        client.activate.side_effect = [RuntimeError('reload failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'reload failed'):
            module.install(client, APP, '/webmail', 'container', REVISION)
        self.assertEqual(client.activate.call_args_list[-1].args, (APP, APP['resourceUrl']))

    @patch.object(module.subprocess, 'run')
    def test_uploaded_bundle_works_without_an_image_bundle(self, run):
        data = bundle().stdout
        client = Mock()
        client.request.return_value = MARKER
        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            archive.write(data)
            archive.flush()
            module.install(client, APP, '/webmail', 'running-container', REVISION, archive.name)
        self.assertEqual(run.call_count, 2)  # mkdir, atomic write; no image read.
        write = run.call_args_list[-1]
        self.assertEqual(write.kwargs['input'], data)
        self.assertEqual(write.args[0][:4], ['docker', 'exec', '-i', 'running-container'])
        client.activate.assert_called_once()

    @patch.object(module.subprocess, 'run')
    def test_corrupt_uploaded_bundle_never_touches_container_or_application(self, run):
        client = Mock()
        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            archive.write(b'broken upload')
            archive.flush()
            with self.assertRaises(zipfile.BadZipFile):
                module.install(client, APP, '/webmail', 'running-container', REVISION, archive.name)
        run.assert_not_called()
        client.activate.assert_not_called()

    def test_ambiguous_application_is_rejected(self):
        client = object.__new__(module.Client)
        client.call = Mock(side_effect=[{'ids': ['one', 'two'], 'total': 2}, {'list': [
            {'id': 'one', 'urlPrefix': {'/webmail': True}},
            {'id': 'two', 'urlPrefix': {'webmail': True}},
        ]}])
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            client.application('/webmail')

    def test_existing_prefix_and_oauth_configuration_are_preserved(self):
        client = object.__new__(module.Client)
        client.call = Mock(side_effect=[{'updated': {'webmail': None}}, {'created': {'reload': {'id': 'a1'}}}])
        client.activate(APP, 'file:///new.zip')
        self.assertEqual(client.call.call_args_list[0].args, (
            'x:Application/set', {'update': {'webmail': {'resourceUrl': 'file:///new.zip'}}},
        ))

    def test_jmap_set_failure_is_not_treated_as_success(self):
        client = object.__new__(module.Client)
        client.account, client.api_path = 'admin', '/jmap'
        client.request = Mock(return_value={'methodResponses': [[
            'x:Application/set', {'notUpdated': {'webmail': {'type': 'forbidden'}}}, 'deploy',
        ]]})
        with self.assertRaisesRegex(RuntimeError, 'refused'):
            client.call('x:Application/set', {})

    def test_redirects_never_forward_admin_authorization(self):
        self.assertIsNone(module.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))


if __name__ == '__main__':
    unittest.main()
