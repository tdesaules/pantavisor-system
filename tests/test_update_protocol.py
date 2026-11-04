from pathlib import Path
import tempfile
import unittest
from tools.pvbuild.model import load
from tools.pvbuild.update_protocol import stage, select, rollback


class UpdateProtocolTests(unittest.TestCase):
    def test_failure_at_every_boundary_preserves_a_complete_generation(self):
        for boundary in ('esp-durable', 'system-durable', 'manifest-durable', 'before-selector', 'selector-durable'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                system, esp, payload, efi = [root / p for p in ('system', 'esp', 'payload', 'efi')]
                payload.mkdir(); efi.mkdir()
                (payload / 'kernel').write_bytes(b'old kernel')
                (efi / 'boot.efi').write_bytes(b'old EFI')
                stage(system, esp, payload, efi, 'a' * 32)
                select(system, esp, 'a' * 32, None)
                (payload / 'kernel').write_bytes(b'new kernel')
                (efi / 'boot.efi').write_bytes(b'new EFI')

                def powercut(point):
                    if point == boundary: raise RuntimeError('power cut')

                with self.assertRaisesRegex(RuntimeError, 'power cut'):
                    stage(system, esp, payload, efi, 'b' * 32, powercut)
                    select(system, esp, 'b' * 32, 'a' * 32, powercut)
                active = load(system / 'selector.json')['active']
                self.assertEqual(active, ('b' if boundary == 'selector-durable' else 'a') * 32)
                self.assertEqual((system / 'generations' / active / 'kernel').read_bytes(),
                                 b'new kernel' if active == 'b' * 32 else b'old kernel')
                self.assertEqual((esp / 'generations' / active / 'boot.efi').read_bytes(),
                                 b'new EFI' if active == 'b' * 32 else b'old EFI')
                if active == 'b' * 32:
                    rollback(system, esp)
                    self.assertEqual(load(system / 'selector.json')['active'], 'a' * 32)

    def test_corrupt_esp_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'payload'; payload.mkdir()
            (payload / 'file').write_bytes(b'valid')
            system, esp = root / 'system', root / 'esp'
            stage(system, esp, payload, payload, 'a' * 32)
            (esp / 'generations' / ('a' * 32) / 'file').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                select(system, esp, 'a' * 32, None)
            self.assertFalse((system / 'selector.json').exists())
