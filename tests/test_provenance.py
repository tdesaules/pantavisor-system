from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools.pvbuild import provenance


class ProvenanceTests(unittest.TestCase):
    def test_build_identity_and_payload_are_checked_at_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'repo'; root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'source').write_text('input')
            deploy = Path(temporary) / 'deploy'
            names = ['pantavisor-initramfs-ventuno-q.' + suffix for suffix in ('cpio.gz', 'spdx.tar.zst')]
            builder = 'from pathlib import Path; ' + '; '.join(f'Path({str(deploy / name)!r}).write_bytes(b"output")' for name in names)
            with patch.object(provenance, 'ROOT', root), patch('tools.pvbuild.build.source_info', return_value={'commit': 'fixture', 'dirty': True}):
                provenance.run([sys.executable, '-c', builder], deploy, names)
                record = provenance.read(deploy)
                self.assertEqual(record['source']['commit'], 'fixture')
                (deploy / names[0]).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'payload changed'): provenance.read(deploy)

    def test_source_change_during_build_prevents_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'repo'; root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            source = root / 'source'; source.write_text('before')
            deploy = Path(temporary) / 'deploy'; deploy.mkdir()
            (deploy / 'build-provenance.json').write_text('old')
            command = [sys.executable, '-c', f'from pathlib import Path; Path({str(source)!r}).write_text("after")']
            with patch.object(provenance, 'ROOT', root), patch('tools.pvbuild.build.source_info', return_value={'commit': 'fixture'}):
                with self.assertRaisesRegex(ValueError, 'during build'):
                    provenance.run(command, deploy, [])
            self.assertFalse((deploy / 'build-provenance.json').exists())
