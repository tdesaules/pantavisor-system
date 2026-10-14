"""UEFI/QEMU qualification of a prepared native base installation."""
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

from .host import efi_firmware
from .model import canonical, digest, load


SCENARIOS = ('missing-system', 'unreadable-system', 'missing-store', 'invalid-env', 'normal', 'powercut', 'dedicated-recovery')


def qualify(package, key, public_key, output, scenarios=SCENARIOS):
    if not scenarios or set(scenarios) - set(SCENARIOS): raise ValueError('Unknown native test scenario')
    metadata = load(package / 'installation.json')
    if metadata.get('profile') != 'base':
        raise ValueError('The current native VM fixture supports the base profile only')
    mac = metadata['ethernetMAC']
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac): raise ValueError('Invalid fixture MAC')
    if not metadata['emulated']:
        raise ValueError('QEMU requires an explicitly emulated native package')
    for name, checksum in metadata['files'].items():
        if name.startswith('/') or '..' in Path(name).parts or digest(package / name) != checksum:
            raise ValueError('Native package checksum mismatch')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    known = output / 'known_hosts'
    known.write_text('ventuno-native-test ' + public_key + '\n')
    efi = output / 'efi/EFI/BOOT'
    efi.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    ssh = ['ssh', '-F', '/dev/null', '-i', str(key.resolve()), '-p', str(port),
           '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=3',
           '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=ventuno-native-test',
           '-o', 'UserKnownHostsFile=' + str(known.resolve()), 'root@127.0.0.1']

    def request(command):
        return subprocess.run(ssh + [command], capture_output=True, text=True, timeout=15)

    def wait(process, command, expected, seconds=240):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if process.poll() is not None: raise RuntimeError('QEMU exited before readiness')
            try:
                result = request(command)
                if result.returncode == 0 and expected in result.stdout: return result.stdout
            except subprocess.TimeoutExpired:
                pass
            time.sleep(2)
        raise RuntimeError('Native VM readiness timeout: ' + expected)

    results = {}
    for scenario in scenarios:
        name = 'recoveryaa64.efi' if scenario == 'dedicated-recovery' else 'bootaa64.efi'
        shutil.copyfile(package / 'esp/EFI/ventuno' / name, efi / 'BOOTAA64.EFI')
        support = output / (scenario + '.ext4')
        command = ['qemu-system-aarch64', '-machine', 'virt,gic-version=3', '-cpu', 'cortex-a57',
                   '-m', '2048', '-smp', '2', '-display', 'none', '-serial', 'stdio', '-monitor', 'none',
                   '-bios', efi_firmware()]
        if scenario == 'normal': command += ['-rtc', 'base=1970-01-01T00:00:00']
        if scenario not in ('missing-system', 'dedicated-recovery'):
            with support.open('xb') as stream:
                stream.truncate(64 * 1024**2 if scenario == 'unreadable-system' else
                                (package / 'system/boot/pantavisor/pvstore.ext4').stat().st_size + 1024**3)
            if scenario != 'unreadable-system':
                with tempfile.TemporaryDirectory(dir=output, prefix='seed-') as temporary:
                    seed = Path(temporary) / 'system'
                    subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always', str(package / 'system'), str(seed)], check=True)
                    if scenario == 'missing-store': (seed / 'boot/pantavisor/pvstore.ext4').unlink()
                    if scenario == 'invalid-env': (seed / 'boot/pantavisor/state/grubenv').write_bytes(b'invalid')
                    subprocess.run(['mkfs.ext4', '-q', '-F', '-U', metadata['systemUUID'],
                        '-E', 'lazy_itable_init=0,lazy_journal_init=0', '-d', str(seed), str(support)], check=True)
            command += ['-drive', f'file={support},format=raw,if=virtio']
        command += ['-drive', f'file=fat:ro:{efi.parents[1]},format=raw,if=virtio,readonly=on',
                    '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:2222',
                    '-device', f'virtio-net-pci,netdev=net0,romfile=,mac={mac}']
        console = output / (scenario + '.log')
        with console.open('wb') as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            try:
                if scenario == 'powercut':
                    wait(process, 'cat /trial-status/health.status', 'ready=1')
                    # Cut before the 120-second stability window clears the latch.
                    guard = request('cat /trial-status/guard.status').stdout
                    values = dict(line.split('=', 1) for line in guard.splitlines() if '=' in line)
                    if int(values['elapsed']) >= 100: raise RuntimeError('Missed the early power-cut window')
                    process.kill(); process.wait(timeout=10)
                    process.stdin.close()
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
                if scenario == 'normal':
                    ready = wait(process, 'cat /trial-status/health.status', 'ready=1')
                    wait(process, 'test -s /run/platform/time-ready && echo TIME_READY', 'TIME_READY')
                    synchronized = int(request('date +%s').stdout.strip())
                    if abs(synchronized - time.time()) > 60:
                        raise RuntimeError('Core did not synchronize the VM clock')
                    first = request('cat /proc/sys/kernel/random/boot_id; cat /data/boots').stdout.strip()
                    request('pvcontrol cmd reboot')
                    time.sleep(10)
                    wait(process, 'test "$(cat /data/boots)" = 2 && echo SECOND_BOOT', 'SECOND_BOOT')
                    wait(process, 'cat /trial-status/health.status', 'ready=1')
                    second = request('cat /proc/sys/kernel/random/boot_id; cat /data/boots').stdout.strip()
                    if first.splitlines()[0] == second.splitlines()[0]: raise RuntimeError('Reboot did not change boot ID')
                    request("set -e; printf 'recovery\\n' > /run/platform/next; pvcontrol cmd reboot")
                    time.sleep(10)
                    results['normal-reboot'] = {'first': first, 'second': second, 'health': ready}
                observed = wait(process, 'test -f /run/ventuno-recovery && echo RECOVERY_SSH; cat /proc/mounts; e2fsck -V 2>&1', 'RECOVERY_SSH')
                if '/mnt/pv-system ' in observed or '/mnt/appdata ' in observed:
                    raise RuntimeError('Recovery unexpectedly mounted persistent storage')
                if 'VENTUNO_RECOVERY_READY' not in console.read_text(errors='replace'):
                    raise RuntimeError('Recovery bootstrap did not finish')
                process.stdin.write(b"printf 'VENTUNO_CONSOLE_%s\\n' READY\n")
                process.stdin.flush()
                until = time.monotonic() + 15
                while time.monotonic() < until:
                    text = console.read_text(errors='replace')
                    if 'setsid: not found' in text:
                        raise RuntimeError('Recovery console cannot start: setsid is missing')
                    if 'VENTUNO_CONSOLE_READY' in text:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError('Recovery serial console did not execute the test command')
                results[scenario] = observed
            finally:
                process.stdin.close()
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        if scenario == 'normal':
            subprocess.run(['e2fsck', '-f', '-n', str(support)], check=True)
            store = output / 'store-after.ext4'
            subprocess.run(['debugfs', '-R', f'dump /boot/pantavisor/pvstore.ext4 {store}', str(support)], check=True)
            subprocess.run(['e2fsck', '-f', '-n', str(store)], check=True)
    (output / 'results.json').write_bytes(canonical(results))
    print('PASS: native UEFI scenarios', ', '.join(scenarios), ':', output)
