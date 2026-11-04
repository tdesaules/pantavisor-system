"""Publish a validated public BSP as an ORAS component."""
import re
import subprocess
from .components import verify_component


def publish(directory, registry):
    directory = directory.resolve()
    metadata = verify_component(directory)
    if metadata['source']['dirty']: raise ValueError('Release requires a clean source build')
    if 'build-provenance.json' not in metadata['files']: raise ValueError('Build provenance required')
    if not re.fullmatch(r'ghcr\.io/[a-z0-9._/-]+', registry): raise ValueError('Expected a GHCR repository without tag')
    reference = registry + ':' + metadata['version']
    subprocess.run(['oras', 'push', '--artifact-type', 'application/vnd.pantavisor.component.v2', reference,
                    'component.json', *sorted(metadata['files'])], cwd=directory, check=True)
    return registry + '@' + subprocess.check_output(['oras', 'resolve', reference], text=True).strip()
