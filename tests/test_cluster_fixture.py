import subprocess
import unittest
from unittest.mock import Mock, patch

from tools.pvbuild import cluster_test


class ClusterFixtureTests(unittest.TestCase):
    def test_transient_timeout_reuses_identical_validated_manifest(self):
        request = Mock(side_effect=[
            subprocess.CompletedProcess([], 1, '', 'OpenAPI: context deadline exceeded'),
            subprocess.TimeoutExpired('ssh', 15),
            subprocess.CompletedProcess([], 0, 'created', ''),
        ])
        with patch.object(cluster_test.time, 'monotonic', side_effect=[0, 0, 1, 2]), \
                patch.object(cluster_test.time, 'sleep'):
            cluster_test.apply_fixture(request, Mock(poll=Mock(return_value=None)), '{"kind":"List"}')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list, [request.call_args] * 3)
        self.assertIn('apply -f -', request.call_args.args[0])
        self.assertNotIn('--validate=false', request.call_args.args[0])

    def test_invalid_manifest_is_not_retried(self):
        request = Mock(return_value=subprocess.CompletedProcess([], 1, '', 'unknown field invalid'))
        with self.assertRaisesRegex(RuntimeError, 'unknown field invalid'):
            cluster_test.apply_fixture(request, Mock(poll=Mock(return_value=None)), '{}')
        request.assert_called_once()

    def test_persistent_timeout_remains_blocking(self):
        request = Mock(return_value=subprocess.CompletedProcess([], 1, '', 'context deadline exceeded'))
        with patch.object(cluster_test.time, 'monotonic', side_effect=[0, 0, 301]), \
                patch.object(cluster_test.time, 'sleep'), \
                self.assertRaisesRegex(RuntimeError, 'timed out: context deadline exceeded'):
            cluster_test.apply_fixture(request, Mock(poll=Mock(return_value=None)), '{}')
        request.assert_called_once()

    def test_exited_vm_is_not_retried(self):
        request = Mock()
        with self.assertRaisesRegex(RuntimeError, 'QEMU exited'):
            cluster_test.apply_fixture(request, Mock(poll=Mock(return_value=1)), '{}')
        request.assert_not_called()
