import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('webmail_activate', Path(__file__).with_name('activate.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REVISION = 'a' * 40


class ActivationTests(unittest.TestCase):
    def staging(self):
        return Path(tempfile.mkdtemp(prefix=f'stalwart-webmail-{REVISION}-', dir='/tmp')).rename(
            Path('/tmp') / f'stalwart-webmail-{REVISION}-123-{id(self)}'
        )

    @patch.object(module.subprocess, 'run')
    def test_secret_is_only_passed_in_environment_and_staging_is_removed(self, run):
        staging = self.staging()
        config = {'STALWART_ADMIN_AUTHORIZATION': 'Basic test-secret'}
        module.activate(staging, REVISION, config)
        command = run.call_args.args[0]
        self.assertNotIn('Basic test-secret', str(command))
        self.assertEqual(run.call_args.kwargs['env']['STALWART_ADMIN_AUTHORIZATION'], 'Basic test-secret')
        self.assertFalse(staging.exists())

    @patch.object(module.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'bash'))
    def test_failed_activation_still_removes_staging(self, run):
        staging = self.staging()
        with self.assertRaises(subprocess.CalledProcessError):
            module.activate(staging, REVISION, {})
        self.assertFalse(staging.exists())

    @patch.object(module.subprocess, 'run')
    def test_unexpected_environment_overrides_are_rejected(self, run):
        staging = self.staging()
        try:
            with self.assertRaisesRegex(ValueError, 'Unexpected deployment configuration'):
                module.activate(staging, REVISION, {'PATH': '/untrusted'})
            run.assert_not_called()
        finally:
            staging.rmdir()


if __name__ == '__main__':
    unittest.main()
