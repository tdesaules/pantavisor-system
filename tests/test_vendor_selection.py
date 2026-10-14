"""Firmware selection must respect kernel search priority and snapshot boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest

from tools.pvbuild.vendor import resolve_firmware, select_firmware


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, value=b'data'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_vendor_and_generic_search_candidates_are_preserved(self):
        self.write('radio/fw.bin', b'generic')
        self.write('updates/radio/fw.bin', b'vendor')
        selected, required = select_firmware(self.root, 'test', {
            'firmware_required': ['radio/fw.bin'], 'firmware_optional_patterns': [],
        })
        self.assertEqual(selected, ['radio/fw.bin', 'updates/radio/fw.bin'])
        self.assertEqual(required['radio/fw.bin'], ['updates/radio/fw.bin', 'radio/fw.bin'])

    def test_compressed_override_and_plain_generic_are_preserved(self):
        self.write('radio/fw.bin')
        self.write('updates/radio/fw.bin.zst')
        selected, _ = select_firmware(self.root, 'test', {
            'firmware_required': ['radio/fw.bin'], 'firmware_optional_patterns': [],
        })
        self.assertEqual(selected, ['radio/fw.bin', 'updates/radio/fw.bin.zst'])

    def test_absolute_symlink_is_resolved_inside_snapshot(self):
        self.write('updates/radio/fw.bin', b'vendor')
        (self.root / 'alias').symlink_to('/lib/firmware/updates/radio')
        self.assertEqual(resolve_firmware(self.root, 'alias/fw.bin').read_bytes(), b'vendor')

    def test_compressed_fallback(self):
        self.write('radio/fw.bin.zst')
        selected, _ = select_firmware(self.root, 'test', {
            'firmware_required': ['radio/fw.bin'], 'firmware_optional_patterns': [],
        })
        self.assertEqual(selected, ['radio/fw.bin.zst'])

    def test_escape_and_loop_rejected(self):
        (self.root / 'escape').symlink_to('../../etc/passwd')
        (self.root / 'loop').symlink_to('loop')
        for name in ('escape', 'loop'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                resolve_firmware(self.root, name)

    def test_missing_required_firmware_fails(self):
        with self.assertRaises(FileNotFoundError):
            select_firmware(self.root, 'test', {
                'firmware_required': ['missing'], 'firmware_optional_patterns': [],
            })


if __name__ == '__main__':
    unittest.main()
