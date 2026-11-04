import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools.pvbuild.cluster_test import resources
from tools.pvbuild.diagnostics import collect
from tools.pvbuild.environment import qualification_environment


class QualificationTests(unittest.TestCase):
    def test_old_qemu_is_rejected(self):
        with patch('subprocess.check_output', return_value='QEMU emulator version 8.2.2\n'):
            with self.assertRaisesRegex(ValueError, '10.2.2'): qualification_environment()

    def test_cluster_persistence_does_not_recreate_the_reboot_token(self):
        items = resources('test-fixture')['items']
        deployment = next(item for item in items if item['kind'] == 'Deployment')
        command = deployment['spec']['template']['spec']['containers'][0]['command'][-1]
        self.assertIn('test -f /data/www/index.html ||', command)
        self.assertNotIn('persisted-', command)
        pvc = next(item for item in items if item['kind'] == 'PersistentVolumeClaim')
        self.assertEqual(pvc['spec']['volumeName'], 'test-fixture')

    def test_diagnostics_survive_ssh_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('ssh', 30)):
                path = collect('root@board', 2222, Path('key'), Path('hosts'), Path(temporary) / 'capture', samples=1)
            self.assertEqual(json.loads(path.read_text())['error'], 'SSH timeout')
