"""Prepare a private native eMMC tree and a self-contained ARM64 EFI rescue.

This module only writes a new output directory, never a block device or NVRAM.
The support filesystem remains a file-store host, without an Ubuntu userspace.
"""
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from .cpio import newc_members, resolve_member
from .model import ROOT, canonical, digest, load

from .adapters.grub import grub_default, grub_entry, grubenv
from .host import grub_checker


def recovery_config():
    return '''menuentry 'Ventuno independent recovery' --id ventuno-recovery {
    echo 'VENTUNO_RECOVERY_SELECTED'
    linux (memdisk)/recovery/kernel rdinit=/init console=tty0 console=ttyMSM0,115200n8 console=ttyAMA0 panic=10
    initrd (memdisk)/recovery/initramfs
}
'''


def esp_bootstrap(esp_uuid, efi_directory='ventuno'):
    """Dispatcher used by the factory-recognized loader, independent of p71."""
    if not re.fullmatch('[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}', esp_uuid):
        raise ValueError('Expected a FAT filesystem UUID')
    if not re.fullmatch('[A-Za-z0-9_-]+', efi_directory):
        raise ValueError('Invalid EFI directory')
    return f'''# Native Pantavisor dispatcher; all bootstrap inputs are on the ESP.
insmod part_gpt
insmod fat
insmod chain
search --no-floppy --fs-uuid --set=ventuno_esp {esp_uuid}
if chainloader ($ventuno_esp)/EFI/{efi_directory}/bootaa64.efi; then
    boot
fi
echo 'VENTUNO_PRIMARY_EFI_UNAVAILABLE'
if chainloader ($ventuno_esp)/EFI/{efi_directory}/recoveryaa64.efi; then
    boot
fi
echo 'VENTUNO_RECOVERY_EFI_UNAVAILABLE'
halt
'''


def native_grub(system_uuid, install_id, *, emulated=False, recovery_only=False):
    system_uuid = str(uuid.UUID(system_uuid))
    header = 'set timeout=5\nset default=0\n'
    if recovery_only:
        return header + recovery_config()
    policy = grub_default(system_uuid)
    entry = grub_entry(system_uuid, install_id, emulated=emulated)
    # Fallback must not read the support filesystem it is recovering.
    entry = entry.replace('configfile ($root)/boot/grub/grub.cfg',
                          'configfile (memdisk)/boot/grub/grub.cfg')
    text = header + policy + recovery_config() + entry
    return text.replace('ventuno_force_ubuntu', 'ventuno_force_recovery').replace(
        'Ubuntu recovery', 'Independent recovery').replace('return to Ubuntu', 'return to recovery')


def append_overlay(base, files):
    with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='native-overlay-') as temporary:
        root = Path(temporary)
        for name, (mode, data) in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)
        paths = sorted(root.rglob('*'))
        for path in paths:
            os.utime(path, (0, 0))
        names = b''.join(str(path.relative_to(root)).encode() + b'\0' for path in paths)
        archive = subprocess.check_output(['cpio', '--null', '-o', '--format=newc', '--owner=0:0', '--reproducible'],
                                          input=names, cwd=root)
        return base + gzip.compress(archive, mtime=0)


def require_recovery_tools(members):
    for command in ('setsid', 'e2fsck'):
        for directory in ('usr/bin', 'usr/sbin', 'bin', 'sbin'):
            try:
                name = resolve_member(members, directory + '/' + command)
            except FileNotFoundError:
                continue
            mode, _ = members[name]
            if stat.S_ISREG(mode) and mode & 0o111:
                break
        else:
            raise ValueError(f'Native rescue requires {command} in the initramfs; rebuild the base bundle')


def prepare(state_directory, system_uuid, modules, output, *, emulated=False, size_mib=4096, esp_uuid=None):
    system_uuid = str(uuid.UUID(system_uuid))
    if esp_uuid is not None: esp_bootstrap(esp_uuid)
    metadata = load(state_directory / 'device-state.json')
    if metadata['bootContract'] != 'emmc-file-store-v1':
        raise ValueError('Render a native System composition first')
    if digest(state_directory / 'state.json') != metadata['stateSha256']:
        raise ValueError('Private state checksum mismatch')
    if not 1024 <= size_mib <= 32768:
        raise ValueError('Store size must be between 1024 and 32768 MiB')
    state = load(state_directory / 'state.json')
    revision = metadata['revision']
    from .adapters.revisions import valid_revision
    if not valid_revision(revision): raise ValueError('Invalid revision')
    objects = {}
    for name, value in state.items():
        if name == '#spec' or isinstance(value, dict):
            continue
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Invalid object digest')
        path = state_directory / 'objects' / value
        if digest(path) != value:
            raise ValueError('Private object checksum mismatch: ' + name)
        objects[name] = path
    for name in state:
        if name != '#spec' and (not re.fullmatch('[A-Za-z0-9_./-]+', name) or name.startswith('/') or '..' in Path(name).parts):
            raise ValueError('Invalid state path')
    initramfs = objects['bsp/pantavisor'].read_bytes()
    members = {name: (mode, data) for name, mode, data in newc_members(gzip.decompress(initramfs))}
    if 'etc/ventuno-native' not in members:
        raise ValueError('Bootstrap is not native')
    require_recovery_tools(members)
    if not (modules / 'linux.mod').is_file():
        raise ValueError('Provide the ARM64 EFI GRUB module directory')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    support = output / 'system'
    directory = support / 'boot/pantavisor'
    (directory / 'state').mkdir(parents=True)
    (directory / 'logs').mkdir()
    (support / 'boot/grub').mkdir()
    install_id = uuid.uuid4().hex
    (directory / 'install-id').write_text(install_id + '\n')
    (directory / 'state/grubenv').write_bytes(grubenv({'pv_rev': revision}))
    (directory / 'state/boot-budget').write_bytes(grubenv({'ventuno_boots': '0', 'ventuno_recovery': '0'}))
    (support / 'boot/grub/grubenv').write_bytes(grubenv({}))
    shutil.copyfile(objects['bsp/kernel.img'], directory / 'kernel')
    shutil.copyfile(objects['bsp/pantavisor'], directory / 'initramfs')
    with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='native-store-') as temporary:
        root = Path(temporary)
        for name in ('objects', 'cache', 'config', 'logs', 'disks', f'trails/{revision}/.pvr', f'trails/{revision}/.pv'):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / 'boot').symlink_to('/mnt/pv-system/boot/pantavisor/state')
        if 'k0s/run.json' in state:
            (root / 'pv/volume-disk').mkdir(parents=True)
            (root / 'pv/volume-disk/nvme-apps').symlink_to('/mnt/appdata')
        trail = root / 'trails' / revision
        for name, value in state.items():
            if name == '#spec': continue
            target = trail / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, dict):
                target.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')))
            else:
                obj = root / 'objects' / value
                if not obj.exists():
                    shutil.copyfile(objects[name], obj)
                    obj.chmod(0o600)
                os.link(obj, target)
        (trail / '.pvr/json').write_bytes(canonical(state))
        (trail / '.pvr/config').write_text('{"#spec":"pvr-repo@1"}\n')
        image = directory / 'pvstore.ext4'
        with image.open('xb') as stream: stream.truncate(size_mib * 1024**2)
        subprocess.run(['mkfs.ext4', '-q', '-F', '-L', 'pvstore', '-E', 'lazy_itable_init=0,lazy_journal_init=0',
                        '-d', str(root), str(image)], check=True)
        # mkfs -d preserves builder ownership: normalize every populated inode
        # in the filesystem image, without requiring host root privileges.
        commands = ''.join(f'set_inode_field /{path.relative_to(root)} {field} 0\n'
                           for path in sorted(root.rglob('*')) for field in ('uid', 'gid'))
        subprocess.run(['debugfs', '-w', '-f', '-', str(image)], input=commands, text=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    recovery = output / 'recovery'
    recovery.mkdir()
    overlay = {'init': (0o755, (ROOT / 'recovery/init').read_bytes()),
               'etc/ventuno-revision-network': (0o644, b'recovery\n')}
    for name in ('home/root/.ssh/authorized_keys', 'etc/dropbear/dropbear_ed25519_host_key'):
        overlay[name] = (0o600, objects['_config/core/' + name].read_bytes())
    # Recovery keeps its own host-side network implementation; it never starts Core.
    overlay['etc/ventuno-network.conf'] = (0o600, objects['_config/core/etc/pantavisor/network.conf'].read_bytes())
    (recovery / 'initramfs').write_bytes(append_overlay(initramfs, overlay))
    shutil.copyfile(directory / 'kernel', recovery / 'kernel')
    efi = output / 'esp/EFI/ventuno'
    efi.mkdir(parents=True)
    maker = shutil.which('grub-mkstandalone') or shutil.which('grub2-mkstandalone')
    if not maker: raise ValueError('Install grub-mkstandalone')
    for name, only in (('bootaa64.efi', False), ('recoveryaa64.efi', True)):
        config = recovery / (name + '.cfg')
        config.write_text(native_grub(system_uuid, install_id, emulated=emulated, recovery_only=only))
        subprocess.run([grub_checker(), str(config)], check=True)
        subprocess.run([maker, '-O', 'arm64-efi', '-d', str(modules), '--locales=', '--fonts=', '--themes=',
                        '-o', str(efi / name), 'boot/grub/grub.cfg=' + str(config),
                        'recovery/kernel=' + str(recovery / 'kernel'),
                        'recovery/initramfs=' + str(recovery / 'initramfs')], check=True)
    if esp_uuid is not None:
        dispatcher = output / 'esp-dispatcher.cfg'
        dispatcher.write_text(esp_bootstrap(esp_uuid, 'ventuno-native-' + install_id))
        subprocess.run([grub_checker(), str(dispatcher)], check=True)
    (output / 'installation.json').write_bytes(canonical({
        'schema': 1, 'bootContract': 'emmc-file-store-v1', 'private': True,
        'systemUUID': system_uuid, 'espUUID': esp_uuid, 'installId': install_id, 'revision': revision,
        'profile': metadata['composition']['profile'],
        'ethernetMAC': metadata['ethernetMAC'],
        'stateSha256': metadata['stateSha256'], 'emulated': emulated,
        'files': {str(path.relative_to(output)): digest(path) for path in sorted(output.rglob('*')) if path.is_file()}}))
    return output / 'installation.json'
