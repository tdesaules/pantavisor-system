"""Render device-private state using independently built component contracts."""
import base64
import hashlib
from pathlib import Path
import re
import shlex
import shutil
from string import Template
import subprocess
import sys
import tempfile
import uuid
from .components import verify_bundle
from .model import ROOT, canonical, digest, load


def validate_device(device, composition):
    if device.get('schema') != 1: raise ValueError('Unsupported device schema')
    uuid.UUID(device['rootUUID'])
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', device['ethernetMAC']): raise ValueError('Invalid MAC')
    if type(device['memoryKiB']) is not int or device['memoryKiB'] < 1024**2: raise ValueError('Invalid memory')
    if not re.fullmatch('[a-zA-Z0-9_.:-]*', device.get('ethernetInterface', '')): raise ValueError('Invalid interface')
    if not device['authorizedKeys']: raise ValueError('SSH public keys required')
    for key in device['authorizedKeys']:
        fields = key.split()
        if '\n' in key or '\r' in key or len(fields) < 2 or fields[0] != 'ssh-ed25519':
            raise ValueError('Expected a single-line Ed25519 public key')
        base64.b64decode(fields[1], validate=True)
    storage = device.get('appStorage')
    if 'k0s' in composition['containers'] and not storage: raise ValueError('This board requires application storage for k0s')
    if storage:
        uuid.UUID(storage['uuid']); uuid.UUID(storage['partuuid'])
        if not re.fullmatch('[a-zA-Z0-9_-]+', storage['serial']): raise ValueError('Invalid disk serial')


def render(directory, device_path, output):
    directory, output = Path(directory), Path(output)
    bundle = verify_bundle(directory)
    composition = bundle['composition']
    device = load(device_path)
    validate_device(device, composition)
    board = load(ROOT / 'boards' / composition['platform'] / 'board.json')
    components = {name: directory / item['directory'] for name, item in bundle['components'].items()}
    core = composition['containers']['core']
    core_dir = components[core['artifact']]
    bsp_dir = components[composition['bsp']]
    bsp = load(bsp_dir / 'bsp.json')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    (output / 'objects').mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='device-key-') as temporary:
        root = Path(temporary) / 'root'
        subprocess.run(['unsquashfs', '-no-progress', '-d', str(root), str(core_dir / 'rootfs.squashfs')],
                       stdout=subprocess.DEVNULL, check=True)
        key = output / 'ssh-host-key'
        generator = ['qemu-aarch64', '-L', str(root), str(root / 'usr/sbin/dropbearkey')]
        if device.get('sshHostKeyFile'): shutil.copyfile(device['sshHostKeyFile'], key)
        else: subprocess.run([*generator, '-t', 'ed25519', '-f', str(key)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        key.chmod(0o600)
        public = subprocess.check_output([*generator, '-y', '-f', str(key)], text=True)
        public = next(line for line in public.splitlines() if line.startswith('ssh-ed25519 '))
    network = {'ethernet_mac': device['ethernetMAC'], 'ssh_port': core['settings']['sshPort'],
               'client_public_key': device['authorizedKeys'][0], 'host_public_key': public,
               'host_key_file': str(key.resolve())}
    network_path = output / 'network-profile.json'
    network_path.write_bytes(canonical(network))
    command = [sys.executable, '-m', 'tools.pvbuild.bootstrap', str(bsp_dir / 'initramfs-base.cpio.gz'),
        '--bsp', str(bsp_dir / 'bsp.json'), '--vendor-archive', str(bsp_dir / 'vendor-root.tar.gz'),
        '--selection', str(bsp_dir / 'selection.json'), '--source-metadata', str(directory / 'bundle.json'),
        '--output-dir', str(output / 'bootstrap'), '--autonomous', '--pid1', '--runtime', '--core',
        '--cgroup-v2', '--native', '--network-profile', str(network_path)]
    if device.get('appStorage'):
        storage = device['appStorage']
        nvme = {'schema': 1, 'name': 'nvme-apps', 'filesystem_uuid': storage['uuid'],
                'partition_uuid': storage['partuuid'], 'serial': storage['serial']}
        path = output / 'nvme-profile.json'
        path.write_bytes(canonical(nvme))
        command += ['--nvme-profile', str(path)]
    subprocess.run(command, cwd=ROOT, check=True)
    state = {'#spec': 'pantavisor-service-system@1',
             'bsp/run.json': {'linux': 'kernel.img', 'initrd': 'pantavisor'},
             'device.json': {'groups': [{'name': 'root', 'status_goal': 'READY', 'restart_policy': 'container', 'timeout': 45}]},
             'core/run.json': {'#spec': 'service-manifest-run@1', 'name': 'core', 'type': 'lxc', 'group': 'root',
                 'config': 'lxc.container.conf', 'root-volume': 'root.squashfs', 'volumes': [], 'roles': ['mgmt'],
                 'status_goal': 'READY', 'restart_policy': 'container',
                 'storage': {'lxc-overlay': {'persistence': 'boot'}, 'data': {'persistence': 'permanent'}}}}

    def put(name, data):
        sha = hashlib.sha256(data).hexdigest()
        target = output / 'objects' / sha
        target.write_bytes(data); target.chmod(0o600)
        state[name] = sha

    put('bsp/kernel.img', (bsp_dir / 'kernel.img').read_bytes())
    put('bsp/pantavisor', (output / 'bootstrap/initramfs-ventuno-q.cpio.gz').read_bytes())
    put('core/root.squashfs', (core_dir / 'rootfs.squashfs').read_bytes())
    bindings = {'memory_max': core['memoryMiB'] * 1024**2, 'cpu_weight': core['cpuWeight'],
                'control_source': board['controlSource']}
    config = Template((core_dir / 'lxc.conf.in').read_text()).substitute(bindings)
    config += f"lxc.mount.entry = {board['statusSource']} trial-status none bind,ro,create=dir 0 0\n"
    config += 'lxc.mount.entry = /mnt/pv-system/boot/pantavisor/state boot-state none bind,ro,create=dir 0 0\n'
    if 'k0s' in composition['containers']:
        config += f"lxc.mount.entry = {board['applicationSource']}/access k0s-access none bind,ro,optional,create=dir 0 0\n"
    put('core/lxc.container.conf', config.encode())
    put('_config/core/home/root/.ssh/authorized_keys', ('\n'.join(device['authorizedKeys']) + '\n').encode())
    put('_config/core/etc/dropbear/dropbear_ed25519_host_key', key.read_bytes())
    put('_config/core/etc/pantavisor/network.conf',
        f"ETHERNET_MAC='{device['ethernetMAC']}'\nNETWORK_INTERFACE='{device.get('ethernetInterface', '')}'\nSSH_PORT={core['settings']['sshPort']}\n".encode())
    put('_config/core/etc/pantavisor/time.conf', ('NTP_SERVERS=' + shlex.quote(' '.join(core['settings']['ntpServers'])) + '\n').encode())
    if 'k0s' in composition['containers']:
        cluster = composition['containers']['k0s']
        settings = cluster['settings']
        cluster_dir = components[cluster['artifact']]
        limit = (device['memoryKiB'] // 1024 - cluster['hostReserveMiB']) * 1024**2
        if limit <= (cluster['kubeReserveMiB'] + cluster['evictionMiB']) * 1024**2:
            raise ValueError('Insufficient device memory')
        bindings = {'memory_max': limit, 'memory_high': limit - 256 * 1024**2,
                    'node_name': settings['nodeName'], 'kernel_release': bsp['kernel_release'],
                    'control_source': board['controlSource']}
        for name, subdir in (('data', 'data'), ('workloads', 'workloads'), ('access', 'access'), ('log', 'log')):
            bindings[name + '_source'] = board['applicationSource'] + '/' + subdir
        put('k0s/lxc.container.conf', Template((cluster_dir / 'lxc.conf.in').read_text()).substitute(bindings).encode())
        put('k0s/root.squashfs', (cluster_dir / 'rootfs.squashfs').read_bytes())
        state['device.json']['groups'].append({'name': 'app', 'status_goal': 'STARTED', 'restart_policy': 'container', 'timeout': 60})
        state['k0s/run.json'] = {'#spec': 'service-manifest-run@1', 'name': 'k0s', 'type': 'lxc', 'group': 'app',
            'config': 'lxc.container.conf', 'root-volume': 'root.squashfs', 'volumes': [], 'roles': ['nobody'],
            'status_goal': 'STARTED', 'restart_policy': 'container', 'storage': {'lxc-overlay': {'persistence': 'boot'}}}
        env = {'K0S_HOST_RESERVE_MIB': cluster['hostReserveMiB'], 'K0S_KUBE_RESERVE_MIB': cluster['kubeReserveMiB'],
               'K0S_EVICTION_MIB': cluster['evictionMiB'], 'K0S_MAX_PODS': settings['maxPods'],
               'K0S_POD_CIDR': settings['podCIDR'], 'K0S_SERVICE_CIDR': settings['serviceCIDR'],
               'K0S_NODE_NAME': settings['nodeName'], 'K0S_CLUSTER_NAME': settings['clusterName'],
               'K0S_DNS_SERVERS': ' '.join(settings['dnsServers']),
               'K0S_DISABLED_COMPONENTS': ','.join(settings['disabledComponents'])}
        put('_config/k0s/etc/pantavisor/k0s.env', ''.join(f'{k}={shlex.quote(str(v))}\n' for k, v in env.items()).encode())
    (output / 'state.json').write_bytes(canonical(state))
    (output / 'device-state.json').write_bytes(canonical({'schema': 1, 'kind': 'private-device-state',
        'version': bundle['version'], 'revision': 'locals/' + bundle['version'], 'composition': composition,
        'ethernetMAC': device['ethernetMAC'],
        'components': {name: {'version': item['metadata']['version'], 'digest': item['digest'], 'source': item['metadata']['source']}
                       for name, item in bundle['components'].items()},
        'stateSha256': digest(output / 'state.json'), 'sshHostPublicKey': public,
        'bootContract': composition['bootContract'], 'installationRequired': True}))
    return 'locals/' + bundle['version']
