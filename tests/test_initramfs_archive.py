"""Exercise parsing against GNU cpio output, including Linux-style concatenation."""
import gzip
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.pvbuild.cpio import newc_members


class NewcTests(unittest.TestCase):
    def archive(self, name, content):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / name).write_bytes(content)
            return subprocess.run(
                ['cpio', '-o', '-H', 'newc', '--quiet'], cwd=temp,
                input=(name + '\n').encode(), stdout=subprocess.PIPE, check=True,
            ).stdout

    def test_concatenated_gzip_members(self):
        first = self.archive('base', b'base image')
        second = self.archive('addon', b'\0vendor\0')
        image = gzip.compress(first) + gzip.compress(second)
        entries = {name: data for name, _, data in newc_members(gzip.decompress(image))}
        self.assertEqual(entries, {'base': b'base image', 'addon': b'\0vendor\0'})

    def test_truncated_payload_is_rejected(self):
        archive = self.archive('payload', b'x' * 4096)
        with self.assertRaises(ValueError):
            list(newc_members(archive[:256]))

    def test_invalid_header_is_rejected(self):
        with self.assertRaises(ValueError):
            list(newc_members(b'not-cpio' * 20))


if __name__ == '__main__':
    unittest.main()
