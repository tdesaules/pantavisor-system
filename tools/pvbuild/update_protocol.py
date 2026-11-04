"""Offline A/B transaction prototype; not wired into the native v1 bootloader.

The ESP payload is immutable and durable before the ext4 selector changes.
Tests inject failures at each boundary. Native activation requires a new boot ABI.
"""
import os
from pathlib import Path
import re
import shutil
import uuid
from .model import canonical, digest, load


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def durable_write(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def inventory(directory):
    if directory.is_symlink() or any(p.is_symlink() for p in directory.rglob('*')):
        raise ValueError('Generation must contain regular files only')
    return {p.relative_to(directory).as_posix(): digest(p) for p in sorted(directory.rglob('*')) if p.is_file()}


def copy_durable(source, destination):
    expected = inventory(source)
    shutil.copytree(source, destination)
    for path in destination.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream: os.fsync(stream.fileno())
    for path in sorted((p for p in destination.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)
    sync_directory(destination)
    sync_directory(destination.parent)
    if inventory(destination) != expected: raise ValueError('Staged generation integrity failure')
    return expected


def stage(system_root, esp_root, system_payload, esp_payload, generation, checkpoint=lambda _: None):
    if not re.fullmatch('[a-f0-9]{32}', generation): raise ValueError('Expected a generation UUID hex')
    # The caller supplies ordinary offline directories, never mounted production
    # roots. A live updater must additionally lock, verify mount identities and
    # negotiate the boot ABI before using this protocol.
    for root in (system_root, esp_root):
        (root / 'generations').mkdir(parents=True, exist_ok=True)
        sync_directory(root)
    esp_files = copy_durable(esp_payload, esp_root / 'generations' / generation)
    checkpoint('esp-durable')
    system_files = copy_durable(system_payload, system_root / 'generations' / generation)
    checkpoint('system-durable')
    record = {'schema': 1, 'generation': generation, 'system': system_files, 'esp': esp_files}
    durable_write(system_root / 'generations' / generation / 'generation.json', canonical(record))
    checkpoint('manifest-durable')
    return record


def select(system_root, esp_root, generation, expected_active, checkpoint=lambda _: None):
    if not re.fullmatch('[a-f0-9]{32}', generation): raise ValueError('Invalid generation')
    selector = system_root / 'selector.json'
    current = load(selector) if selector.exists() else {'active': None, 'previous': None}
    if current['active'] != expected_active: raise ValueError('Active generation changed')
    record = load(system_root / 'generations' / generation / 'generation.json')
    actual = inventory(system_root / 'generations' / generation)
    actual.pop('generation.json', None)
    if record['generation'] != generation or actual != record['system'] or inventory(esp_root / 'generations' / generation) != record['esp']:
        raise ValueError('Generation integrity failure')
    checkpoint('before-selector')
    durable_write(selector, canonical({'active': generation, 'previous': current['active']}))
    checkpoint('selector-durable')


def rollback(system_root, esp_root):
    current = load(system_root / 'selector.json')
    if current['previous'] is None: raise ValueError('No previous generation')
    select(system_root, esp_root, current['previous'], current['active'])
