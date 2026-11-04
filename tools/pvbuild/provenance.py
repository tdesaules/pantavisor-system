"""Capture source identity before KAS and bind it to successful deploy outputs."""
import hashlib
import subprocess
import time
from .model import ROOT, canonical, digest, load


def snapshot():
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    files = {name: digest(ROOT / name) if (ROOT / name).is_file() else None for name in sorted(set(names)) if name}
    return hashlib.sha256(canonical(files)).hexdigest()


def run(command, deploy, names):
    from .build import source_info
    deploy.mkdir(parents=True, exist_ok=True)
    record = deploy / 'build-provenance.json'
    record.unlink(missing_ok=True)
    source = {**source_info(), 'treeSha256': snapshot()}
    started = time.time()
    subprocess.run(command, check=True)
    if snapshot() != source['treeSha256']: raise ValueError('Sources changed during build')
    record.write_bytes(canonical({'schema': 1, 'source': source, 'durationSeconds': round(time.time() - started, 3),
                                  'files': {name: digest(deploy / name) for name in names}}))


def read(deploy):
    record = load(deploy / 'build-provenance.json')
    if record['schema'] != 1 or record['source']['treeSha256'] != snapshot():
        raise ValueError('Sources changed since build; rebuild before packaging')
    for name, checksum in record['files'].items():
        if '/' in name or name in ('.', '..') or digest(deploy / name) != checksum:
            raise ValueError('Build payload changed: ' + name)
    required = {'pantavisor-initramfs-ventuno-q.' + suffix for suffix in ('cpio.gz', 'spdx.tar.zst')}
    if not required <= set(record['files']): raise ValueError('Incomplete build provenance')
    return record
