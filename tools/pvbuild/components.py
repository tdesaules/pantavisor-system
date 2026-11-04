"""Verify independent producer artifacts and assemble a System-owned bundle."""
from pathlib import Path
import re
import shutil
from .model import ROOT, canonical, compose, digest, load, validate, version


def verify_files(directory, inventory, descriptor):
    if directory.is_symlink() or any(p.is_symlink() for p in directory.rglob('*')):
        raise ValueError('Symbolic links are not allowed in artifacts')
    expected = set(inventory) | {descriptor}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != expected: raise ValueError('Unexpected or missing artifact files')
    for name, info in inventory.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts: raise ValueError('Unsafe artifact path')
        if type(info.get('size')) is not int or info['size'] < 0 or not re.fullmatch('[a-f0-9]{64}', info.get('sha256', '')):
            raise ValueError('Invalid artifact inventory entry')
        path = directory / relative
        if path.is_symlink() or path.stat().st_size != info['size'] or digest(path) != info['sha256']:
            raise ValueError('Artifact integrity failure: ' + name)


def verify_component(directory):
    directory = Path(directory)
    metadata = load(directory / 'component.json')
    if metadata.get('schema') != 2 or metadata.get('kind') not in ('rootfs', 'bsp'):
        raise ValueError('Expected an independent schema-2 component')
    version(metadata['version'])
    source = metadata['source']
    if not isinstance(source.get('repository'), str) or not isinstance(source.get('commit'), str) or type(source.get('dirty')) is not bool or not re.fullmatch('[a-f0-9]{64}', source.get('treeSha256', '')):
        raise ValueError('Invalid build provenance identity')
    if 'build-provenance.json' not in metadata['files']: raise ValueError('Build provenance required')
    if not re.fullmatch('[a-z][a-z0-9-]*', metadata['name']): raise ValueError('Invalid component name')
    verify_files(directory, metadata['files'], 'component.json')
    built = load(directory / 'build-provenance.json')
    if built.get('schema') != 1 or any(source.get(key) != value for key, value in built['source'].items()):
        raise ValueError('Build provenance identity mismatch')
    for field in ('provides', 'requires') if metadata['kind'] == 'rootfs' else ('provides',):
        values = metadata.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not re.fullmatch('[a-z][a-z0-9-]*', v) for v in values) or len(set(values)) != len(values):
            raise ValueError('Invalid runtime capabilities')
    if metadata['kind'] == 'rootfs':
        for required in ('rootfs.squashfs', 'rootfs.tar.gz', 'runtime.json', 'lxc.conf.in', 'packages.manifest', 'sbom.spdx.tar.zst'):
            if required not in metadata['files']: raise ValueError('Missing required rootfs metadata')
        runtime = load(directory / 'runtime.json')
        if runtime['id'] != metadata['runtimeABI'] or runtime['architecture'] != metadata['architecture']:
            raise ValueError('Runtime contract mismatch')
        if runtime.get('schema') != 1 or runtime.get('requires') != metadata.get('requires') or runtime.get('provides', []) != metadata.get('provides'):
            raise ValueError('Runtime capabilities mismatch')
        if digest(directory / 'rootfs.squashfs') != metadata['rootfsSha256']:
            raise ValueError('Rootfs checksum mismatch')
    else:
        for required in ('bsp.json', 'kernel.img', 'initramfs-base.cpio.gz', 'vendor-root.tar.gz', 'selection.json', 'sbom.spdx.tar.zst'):
            if required not in metadata['files']: raise ValueError('Missing required BSP payload: ' + required)
        bsp = load(directory / 'bsp.json')
        if bsp['bootContracts'] != metadata['bootContracts']:
            raise ValueError('BSP boot contract mismatch')
    return metadata


def verify_bundle(directory):
    directory = Path(directory)
    bundle = load(directory / 'bundle.json')
    if bundle.get('schema') != 2 or bundle.get('kind') != 'platform-bundle':
        raise ValueError('Expected a System platform bundle, not a private state')
    version(bundle['version'])
    validate(bundle['composition'])
    verify_files(directory, bundle['files'], 'bundle.json')
    for name, item in bundle['components'].items():
        if not re.fullmatch('[a-z][a-z0-9-]*', name): raise ValueError('Invalid component key')
        if item['directory'] != 'components/' + name: raise ValueError('Invalid component directory')
        component = directory / item['directory']
        metadata = verify_component(component)
        if metadata != item['metadata'] or 'sha256:' + digest(component / 'component.json') != item['digest']:
            raise ValueError('Component identity differs from the composition lock')
    compatibility(bundle['composition'], {name: item['metadata'] for name, item in bundle['components'].items()})
    return bundle


def compatibility(composition, metadata):
    expected = {composition['bsp']} | {v['artifact'] for v in composition['containers'].values()}
    if set(metadata) != expected: raise ValueError('Composition components are incomplete or unused')
    for name, item in metadata.items():
        if item['architecture'] != composition['architecture']: raise ValueError('Incompatible architecture: ' + name)
    bsp = metadata[composition['bsp']]
    if bsp['kind'] != 'bsp' or composition['bootContract'] not in bsp['bootContracts']:
        raise ValueError('Incompatible boot contract')
    provided = set(bsp.get('provides', []))
    for item in metadata.values():
        if item['kind'] == 'rootfs': provided.update(item.get('provides', []))
    for name, container in composition['containers'].items():
        component = metadata[container['artifact']]
        if component['kind'] != 'rootfs' or component['runtimeABI'] != 'pantavisor-' + name + '-v1':
            raise ValueError('Incompatible runtime ABI: ' + name)
        missing = set(component.get('requires', [])) - provided
        if missing: raise ValueError('Missing platform capabilities: ' + ', '.join(sorted(missing)))
    core = metadata[composition['containers']['core']['artifact']]
    if 'kubernetes-clients' not in core['features']:
        raise ValueError('Core must provide Kubernetes clients')


def assemble(bsp, core, cluster, output, system_version, profile, overrides=()):
    from .build import source_info
    composition = compose(profile, overrides=overrides)
    system_version = version(system_version)
    paths = [Path(bsp), Path(core)] + ([Path(cluster)] if cluster else [])
    metadata = {}
    locations = {}
    for path in paths:
        item = verify_component(path)
        if item['name'] in metadata: raise ValueError('Duplicate component')
        metadata[item['name']] = item
        locations[item['name']] = path
    compatibility(composition, metadata)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    components = {}
    for name, item in metadata.items():
        destination = output / 'components' / name
        shutil.copytree(locations[name], destination)
        components[name] = {'directory': 'components/' + name, 'kind': item['kind'], 'metadata': item,
                            'digest': 'sha256:' + digest(destination / 'component.json')}
    bundle = {'schema': 2, 'kind': 'platform-bundle', 'version': system_version, 'profile': profile,
              'composition': composition, 'components': components,
              'source': source_info(),
              'files': {p.relative_to(output).as_posix(): {'sha256': digest(p), 'size': p.stat().st_size}
                        for p in sorted(output.rglob('*')) if p.is_file()}}
    (output / 'bundle.json').write_bytes(canonical(bundle))
    verify_bundle(output)
    return output / 'bundle.json'
