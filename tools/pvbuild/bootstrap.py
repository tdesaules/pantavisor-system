#!/usr/bin/env python3
"""Combine a Yocto initramfs with the pinned board BSP as a second cpio member."""
import argparse
import gzip
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import uuid
from .cpio import newc_members
from .vendor import copy_selection, module_closure

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('initramfs', type=Path)
parser.add_argument('--bsp', type=Path, required=True)
parser.add_argument('--selection', type=Path, required=True)
parser.add_argument('--output-dir', type=Path, default=root / 'artifacts/prototype-1084')
parser.add_argument('--trial', action='store_true', help='Install bounded boot wrapper; requires a ventuno_trial token in GRUB')
parser.add_argument('--network-profile', type=Path, help='Enable default Ethernet DHCP and key-only diagnostic SSH')
parser.add_argument('--runtime', action='store_true', help='Run revision 0 from an isolated file-backed store in a private mount namespace')
parser.add_argument('--core', '--revision-network', dest='revision_network', action='store_true', help='Delegate DHCP and SSH to the core container (--revision-network is a compatibility alias)')
parser.add_argument('--pid1', action='store_true', help='Exec Pantavisor as host PID 1, with an independent bounded-trial guardian')
parser.add_argument('--nvme-profile', type=Path, help='Require the provisioned NVMe for application data')
parser.add_argument('--autonomous', action='store_true', help='Durable store and health watchdog; no runtime return timer')
parser.add_argument('--cgroup-v2', action='store_true', help='Use the unified hierarchy for nested container workloads')
parser.add_argument('--vendor-archive', type=Path, required=True, help='Verified public vendor archive')
parser.add_argument('--source-metadata', type=Path, required=True, help='Component bundle provenance')
parser.add_argument('--native', action='store_true', help='Use the Ubuntu-independent eMMC support filesystem')
args = parser.parse_args()
if args.native and not args.autonomous:
    raise ValueError('Native boot requires autonomous mode')
if args.autonomous and (args.trial or not (args.pid1 and args.runtime and args.revision_network)):
    raise ValueError('Autonomous mode requires --pid1 --runtime --core and excludes --trial')
supervised_boot = args.trial or args.autonomous
app_storage = json.loads(args.nvme_profile.read_text()) if args.nvme_profile else None
if app_storage:
    if not args.pid1:
        raise ValueError('External application storage requires the PID 1 trial')
    assert app_storage['schema'] == 1 and app_storage['name'] == 'nvme-apps'
    for key in ('filesystem_uuid', 'partition_uuid'):
        assert str(uuid.UUID(app_storage[key])) == app_storage[key]
    assert re.fullmatch('[a-zA-Z0-9_-]+', app_storage['serial'])
if args.pid1 and not (args.runtime and supervised_boot and args.revision_network):
    raise ValueError('PID 1 trial requires --trial --runtime --core')
if args.revision_network and not (args.runtime and args.network_profile):
    raise ValueError('Revision network requires --runtime and --network-profile')
if args.runtime and not supervised_boot:
    raise ValueError('Runtime trial requires the bounded supervisor')
manifest = json.loads(args.bsp.read_text())
selection = json.loads(args.selection.read_text())
release = manifest['kernel_release']
assert selection['kernel_release'] == release
network = json.loads(args.network_profile.read_text()) if args.network_profile else None
if network:
    if not supervised_boot:
        raise ValueError('Network diagnostic requires the bounded supervisor')
    assert re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', network['ethernet_mac'])
    assert isinstance(network['ssh_port'], int) and 1 <= network['ssh_port'] <= 65535
    assert network['client_public_key'].startswith('ssh-ed25519 ')
    assert network['host_public_key'].startswith('ssh-ed25519 ')
snapshot = args.vendor_archive
with snapshot.open('rb') as stream:
    assert hashlib.file_digest(stream, 'sha256').hexdigest() == manifest['sha256'], snapshot
output = args.output_dir
output.mkdir(parents=True, exist_ok=True)

base = args.initramfs.read_bytes()
members = {name: (mode, data) for name, mode, data in newc_members(gzip.decompress(base))}
assert 'init' in members, 'No /init in base image'
libdir = 'lib'
if 'lib' in members and stat.S_ISLNK(members['lib'][0]):
    target = members['lib'][1].decode().lstrip('/')
    if target != 'usr/lib':
        raise SystemExit(f'Unsupported /lib target: {target}')
    libdir = target

with tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='board-unpack-') as unpacked, tempfile.TemporaryDirectory(dir=root / 'artifacts', prefix='vendor-addon-') as temp:
    unpacked = Path(unpacked)
    staging = Path(temp)
    selected = [f'boot/vmlinuz-{release}', f'lib/modules/{release}', 'lib/firmware', 'etc/modprobe.d', 'usr/lib/modprobe.d']
    if args.cgroup_v2:
        selected.append(f'boot/config-{release}')
    # Public deterministic bundles use tar -C <root> ... ., while the older
    # board snapshots stored member names without the leading ./.
    if args.vendor_archive:
        selected = ['./' + name for name in selected]
    subprocess.run(['tar', '-xf', str(snapshot), '--no-same-owner', '-C', str(unpacked), *selected], check=True)
    selection_result = copy_selection(unpacked, staging, libdir, release, selection)
    if args.cgroup_v2:
        config_path = unpacked / 'boot' / f'config-{release}'
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == manifest['kernel_config_sha256']
        (staging / 'boot').mkdir(exist_ok=True)
        shutil.copyfile(config_path, staging / 'boot' / f'config-{release}')
    for relative in ('etc/modprobe.d', 'usr/lib/modprobe.d'):
        shutil.copytree(unpacked / relative, staging / relative, symlinks=True)
    if selection.get('wifi_enabled') is False:
        (staging / 'etc/modprobe.d/90-ventuno-no-wifi.conf').write_text(
            '# Onboard Wi-Fi is disabled for this Ethernet-only image.\n'
            'blacklist ath11k_pci\nblacklist ath11k\n'
            "install ath11k_pci /bin/sh -c 'exit 1'\ninstall ath11k /bin/sh -c 'exit 1'\n")
    # depmod expects /lib/modules even when the base image uses usrmerge.
    if libdir != 'lib':
        (staging / 'lib').symlink_to('usr/lib')
    subprocess.run(['depmod', '-b', str(staging), release], check=True)
    reduced_modules, _ = module_closure(staging, release, selection['module_seeds'])
    if reduced_modules != selection_result['modules']:
        raise ValueError('Reduced module dependency closure differs from source snapshot')
    print(f'Selected {len(reduced_modules)} modules and {len(selection_result["firmware"])} firmware files', flush=True)
    # Archive one root-level vendor manifest; no user credentials or host keys.
    (staging / 'etc').mkdir(exist_ok=True)
    if args.cgroup_v2:
        assert args.pid1
        (staging / 'etc/ventuno-cgroup-v2').write_text('unified-v1\n')
    (staging / 'etc/ventuno-vendor.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (staging / 'etc/ventuno-selection.json').write_text(json.dumps(selection_result, indent=2) + '\n')
    (staging / 'etc/mdev.conf').write_text('$MODALIAS=.* 0:0 0660 @/sbin/modprobe -b "$MODALIAS"\n')
    if app_storage:
        (staging / 'etc/ventuno-app-storage.conf').write_text(
            f"APPDATA_UUID='{app_storage['filesystem_uuid']}'\n"
            f"APPDATA_PARTUUID='{app_storage['partition_uuid']}'\n"
            f"APPDATA_SERIAL='{app_storage['serial']}'\n")
    commands = '\n'.join(f'modprobe {name} || failures=$((failures + 1))' for name in selection['early_modules'])
    helper = staging / 'usr/sbin/ventuno-load-drivers'
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text('#!/bin/sh\n# Diagnostic initramfs helper; no persistent storage writes.\nfailures=0\n' + commands + '\nmdev -s\nip link\nls /sys/class/block\nprintf "Module load failures: %s\\n" "$failures"\ntest "$failures" -eq 0\n')
    helper.chmod(0o755)
    if supervised_boot:
        for required in ('usr/sbin/ventuno-return-guard', 'usr/sbin/ventuno-trial-diagnostics'):
            if required not in members:
                raise ValueError(f'Trial component missing from Yocto base: {required}')
        supervisor = staging / 'init'
        supervisor.write_bytes(members['usr/sbin/ventuno-native-guard' if args.native else 'usr/sbin/ventuno-return-guard'][1])
        supervisor.chmod(0o755)
        if args.native:
            (staging / 'etc/ventuno-native').write_text('emmc-file-store-v1\n')
            bootctl = staging / 'usr/sbin/ventuno-bootctl'
            bootctl.write_bytes(members['usr/sbin/ventuno-native-bootctl'][1])
            bootctl.chmod(0o755)
        config = members['etc/pantavisor.config'][1].decode().splitlines()
        overrides = {'PV_DEBUG_SHELL': '0', 'PV_DEBUG_SHELL_AUTOLOGIN': '0',
                     'PV_DEBUG_SSH': '0', 'PV_SYSTEM_DRIVERS_LOAD_EARLY_AUTO': '0'}
        config = [line for line in config if line.split('=', 1)[0] not in overrides]
        config.extend(f'{key}={val}' for key, val in overrides.items())
        (staging / 'etc/ventuno-supervised.config').write_text('\n'.join(config) + '\n')
        if args.runtime:
            overrides.update({
                'PV_SYSTEM_INIT_MODE': 'embedded', 'PV_STORAGE_DEVICE': '/dev/loop32',
                'PV_STORAGE_MNTPOINT': '/storage', 'PV_STORAGE_FSTYPE': 'ext4',
                'PV_WDT_MODE': 'disabled', 'PV_LOOP_INDEX_BASE': '64',
                'PV_SECUREBOOT_MODE': 'disabled', 'PV_CONTROL_REMOTE': '0',
                'PV_STORAGE_FIRMWARE_VOL': '0', 'PV_LOG_CAPTURE_DMESG': '0',
                'PV_LOG_SERVER_OUTPUTS': 'filetree,stdout_direct', 'PV_LOG_LEVEL': '4',
            })
            config = [line for line in config if line.split('=', 1)[0] not in overrides]
            config = [line for line in config if not line.startswith('PV_OEM_NAME=')]
            config.extend(f'{key}={val}' for key, val in overrides.items())
            (staging / 'etc/ventuno-embedded.config').write_text('\n'.join(config) + '\n')
            if args.pid1:
                # Pantavisor intentionally ignores --config when running as PID 1.
                (staging / 'etc/pantavisor.config').write_text('\n'.join(config) + '\n')
                (staging / 'etc/ventuno-pid1').write_text('bounded-pid1-trial\n')
                if args.autonomous:
                    (staging / 'etc/ventuno-autonomous').write_text('durable-file-store-v1\n')
    if network and args.revision_network:
        # This marker never starts a host network service. Credentials belong
        # to revision objects and are not included in the host initramfs.
        (staging / 'etc/ventuno-revision-network').write_text('network\n')
        hostkey = output / 'network-host-key'
        hostkey.touch(mode=0o600, exist_ok=True)
        hostkey.chmod(0o600)
        shutil.copyfile(network['host_key_file'], hostkey)
    elif network:
        for required in ('usr/sbin/ventuno-network', 'usr/lib/ventuno/udhcpc.script', 'usr/sbin/dropbear'):
            if required not in members:
                raise ValueError(f'Network component missing: {required}')
        sshdir = staging / 'home/root/.ssh'
        sshdir.mkdir(parents=True)
        (staging / 'home/root').chmod(0o700)
        sshdir.chmod(0o700)
        (sshdir / 'authorized_keys').write_text(network['client_public_key'] + '\n')
        (sshdir / 'authorized_keys').chmod(0o600)
        hostkey = staging / 'etc/dropbear/dropbear_ed25519_host_key'
        hostkey.parent.mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(network['host_key_file'], hostkey)
        hostkey.chmod(0o600)
        (staging / 'etc/ventuno-network.conf').write_text(
            f"ETHERNET_MAC='{network['ethernet_mac']}'\nSSH_PORT=2222\n")
    paths = sorted(staging.rglob('*'))
    for path in paths:
        os.utime(path, (0, 0), follow_symlinks=False)
    names = b''.join(str(p.relative_to(staging)).encode() + b'\0' for p in paths)
    cpio = subprocess.run([
        'cpio', '--null', '-o', '--format=newc', '--owner=0:0', '--reproducible',
    ], input=names, cwd=staging, stdout=subprocess.PIPE, check=True).stdout
    addon = gzip.compress(cpio, compresslevel=6, mtime=0)
    (output / 'vendor-addon.cpio.gz').write_bytes(addon)
    # Linux supports concatenated compressed cpio members.
    (output / 'initramfs-ventuno-q.cpio.gz').write_bytes(base + addon)
    kernel = unpacked / 'boot' / f'vmlinuz-{release}'
    assert hashlib.sha256(kernel.read_bytes()).hexdigest() == manifest['kernel_sha256']
    shutil.copyfile(kernel, output / f'vmlinuz-{release}')
files = [output / f'vmlinuz-{release}', output / 'vendor-addon.cpio.gz', output / 'initramfs-ventuno-q.cpio.gz']
lines = []
for path in files:
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    lines.append(f'{checksum}  {path.name}\n')
    print(f'{path.name}: {path.stat().st_size} bytes; sha256={checksum}')
(output / 'SHA256SUMS').write_text(''.join(lines))
(output / 'build-inputs.json').write_text(json.dumps({
    'base_initramfs_sha256': hashlib.sha256(base).hexdigest(),
    'kernel_release': release,
    'status': 'standalone-prototype-not-boot-tested',
    'bounded_trial': args.trial,
    'autonomous_boot': args.autonomous,
    'embedded_runtime': args.runtime,
    'revision_network': args.revision_network,
    'management_container': 'core' if args.revision_network else None,
    'pid1_runtime': args.pid1,
    'cgroup_v2': args.cgroup_v2,
    'app_storage': app_storage,
    'trial_supervisor_version': (5 if args.autonomous else 4 if args.pid1 else 2) if supervised_boot else None,
    'trial_max_seconds': 0 if args.autonomous else 1800 if args.pid1 else 180,
    'network_profile': {key: val for key, val in network.items() if key != 'host_key_file'} if network else None,
    'bsp': manifest,
    'selection_policy': selection,
    'selection_result': selection_result,
    'kas_config_sha256': hashlib.sha256((root / 'kas/system.yml').read_bytes()).hexdigest(),
    'components': json.loads(args.source_metadata.read_text())['components'],
}, indent=2) + '\n')
