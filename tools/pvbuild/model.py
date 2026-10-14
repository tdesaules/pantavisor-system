"""System compositions. Component versions and provenance are independent."""
import copy
import hashlib
import ipaddress
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
REF_RE = r'ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}'


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def load(path): return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('Expected a canonical SemVer release number')
    return value


def merge(base, patch):
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None: result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict): result[key] = merge(result[key], value)
        else: result[key] = copy.deepcopy(value)
    return result


def integer(value, low, high, field):
    if type(value) is not int or not low <= value <= high: raise ValueError('Invalid ' + field)


def validate(composition):
    if set(composition) - {'schema', 'platform', 'architecture', 'bootContract', 'layers', 'bsp', 'containers', 'profile'}:
        raise ValueError('Unknown composition field')
    platform = composition.get('platform', '')
    if not re.fullmatch('[a-z0-9-]+', platform): raise ValueError('Invalid platform')
    board = load(ROOT / 'boards' / platform / 'board.json')
    if composition.get('schema') != 1 or composition.get('architecture') != board['architecture']:
        raise ValueError('Unsupported composition architecture/schema')
    if composition.get('bootContract') not in board['bootContracts']:
        raise ValueError('Unsupported board boot contract')
    containers = composition.get('containers', {})
    if 'core' not in containers or set(containers) - {'core', 'k0s'}:
        raise ValueError('Expected Core and optional k0s')
    for name, item in containers.items():
        if not re.fullmatch('[a-z][a-z0-9-]*', item.get('artifact', '')): raise ValueError('Invalid artifact key')
    core = containers['core']
    if set(core) != {'artifact', 'memoryMiB', 'cpuWeight', 'settings'}:
        raise ValueError('Unknown Core field; device credentials belong in private inputs')
    integer(core['memoryMiB'], 128, 4096, 'core.memoryMiB')
    integer(core['cpuWeight'], 1, 10000, 'core.cpuWeight')
    if set(core['settings']) != {'sshPort', 'ntpServers'}: raise ValueError('Unknown Core setting')
    integer(core['settings']['sshPort'], 1, 65535, 'sshPort')
    peers = core['settings']['ntpServers']
    if not isinstance(peers, list) or not peers or any(not re.fullmatch('[A-Za-z0-9.:-]+', p) for p in peers):
        raise ValueError('Invalid NTP peers')
    if 'k0s' in containers:
        cluster = containers['k0s']
        if set(cluster) != {'artifact', 'hostReserveMiB', 'kubeReserveMiB', 'evictionMiB', 'settings'}:
            raise ValueError('Unknown k0s field')
        integer(cluster['hostReserveMiB'], core['memoryMiB'] + 256, 8192, 'hostReserveMiB')
        integer(cluster['kubeReserveMiB'], 512, 8192, 'kubeReserveMiB')
        integer(cluster['evictionMiB'], 128, 4096, 'evictionMiB')
        settings = cluster['settings']
        if set(settings) != {'nodeName', 'clusterName', 'dnsServers', 'podCIDR', 'serviceCIDR', 'maxPods', 'disabledComponents'}:
            raise ValueError('Unknown k0s setting')
        for field in ('nodeName', 'clusterName'):
            if not re.fullmatch('[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?', settings[field]):
                raise ValueError('Invalid ' + field)
        if not settings['dnsServers']: raise ValueError('DNS servers are required')
        for server in settings['dnsServers']: ipaddress.ip_address(server)
        if ipaddress.IPv4Network(settings['podCIDR']).overlaps(ipaddress.IPv4Network(settings['serviceCIDR'])):
            raise ValueError('Overlapping cluster networks')
        integer(settings['maxPods'], 1, 110, 'maxPods')
        if any(not re.fullmatch('[a-z][a-z0-9-]*', x) for x in settings['disabledComponents']):
            raise ValueError('Invalid disabled component')
    return composition


def compose(profile='base', *, overrides=()):
    if profile not in ('base', 'k0s'): raise ValueError('Unknown profile')
    result = load(ROOT / 'compositions/base.json')
    definition = load(ROOT / 'compositions/profiles' / (profile + '.json'))
    for name in definition['layers']:
        layer = load(ROOT / 'compositions/layers' / (name + '.json'))
        if not set(layer['requires']) <= set(result['layers']): raise ValueError('Layer requirements not met')
        result = merge(result, layer['patch'])
        result['layers'].append(name)
    result = merge(result, definition.get('overrides', {}))
    for path in overrides:
        patch = load(path)
        if set(patch) - {'containers', 'bootContract'}: raise ValueError('Invalid override scope')
        result = merge(result, patch)
    result['profile'] = profile
    return validate(result)
