"""Fetch a producer's rootfs/BSP artifact, without depending on its Git checkout."""
import os
from pathlib import Path
import re
import subprocess
from .components import verify_component
from .model import REF_RE


def fetch(reference, output):
    if not re.fullmatch(REF_RE, reference): raise ValueError('Require a digest-pinned GHCR artifact reference')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    command = ['oras', 'pull', reference, '--output', str(output)]
    if os.environ.get('REGISTRY_AUTH_FILE'): command += ['--registry-config', os.environ['REGISTRY_AUTH_FILE']]
    subprocess.run(command, check=True)
    verify_component(output)
    return output / 'component.json'
