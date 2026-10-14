from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from tools.pvbuild.host import grub_checker
from tools.pvbuild.native import native_grub, esp_bootstrap, require_recovery_tools


class NativeBootTests(unittest.TestCase):
    def test_efi_configuration_and_independent_rescue(self):
        for rescue in (False, True):
            text = native_grub('11111111-1111-4111-8111-111111111111', '1' * 32,
                               recovery_only=rescue, emulated=True)
            self.assertNotIn('configfile ($root)', text)
            if rescue:
                self.assertNotIn('load_env', text)
                self.assertIn('linux (memdisk)/recovery/kernel', text)
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'grub.cfg'
                path.write_text(text)
                subprocess.run([grub_checker(), str(path)], check=True)

    def test_missing_setsid_is_rejected(self):
        members = {'usr/sbin/e2fsck': (stat.S_IFREG | 0o755, b'elf')}
        with self.assertRaisesRegex(ValueError, 'setsid'): require_recovery_tools(members)

    def test_dispatcher_does_not_read_system_partition(self):
        text = esp_bootstrap('1234-ABCD')
        self.assertNotIn('/boot/grub', text)
        with self.assertRaises(ValueError): esp_bootstrap('invalid')
