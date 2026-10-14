"""Host-tool portability for Fedora workstations and Ubuntu CI runners."""
from pathlib import Path
import shutil


def grub_checker():
    command = shutil.which('grub2-script-check') or shutil.which('grub-script-check')
    if not command: raise RuntimeError('Install grub-common / grub2-tools')
    return command


def efi_firmware():
    for name in ('/usr/share/edk2/aarch64/QEMU_EFI.fd', '/usr/share/qemu-efi-aarch64/QEMU_EFI.fd',
                 '/usr/share/AAVMF/QEMU_EFI.fd'):
        if Path(name).is_file(): return name
    raise RuntimeError('Install qemu-efi-aarch64 / edk2-aarch64')


def sparse_copy(source, target, length):
    zeros = bytes(1024 * 1024)
    while length:
        data = source.read(min(len(zeros), length))
        if not data: raise ValueError('Truncated disk image')
        if data == zeros[:len(data)]: target.seek(len(data), 1)
        else: target.write(data)
        length -= len(data)
