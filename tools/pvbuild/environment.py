"""The version is qualified explicitly, rather than inherited from the distro."""
import platform
import re
import subprocess

QEMU_VERSION = '10.2.2'


def qualification_environment():
    text = subprocess.check_output(['qemu-system-aarch64', '--version'], text=True)
    match = re.search(r'version ([0-9.]+)', text)
    if not match or match[1] != QEMU_VERSION:
        raise ValueError('Qualification requires QEMU ' + QEMU_VERSION + '; use build/Containerfile.tools')
    return {'qemu': text.splitlines()[0], 'hostKernel': platform.release(), 'hostArchitecture': platform.machine()}
