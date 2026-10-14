"""Build and package only the System BSP; Core and k0s are external inputs."""
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
from .model import ROOT, canonical, digest, load, version
from .vendor import copy_selection
from .components import verify_component, verify_bundle

KAS_IMAGE = 'ghcr.io/siemens/kas/kas@sha256:22c70f90e21997c0b6743ad2d802cf1d942c28e974361c4733b8a026cb7ef809'


def run_build():
    for name in ('downloads', 'sstate-cache', 'build/system'):
        (ROOT / 'artifacts' / name).mkdir(parents=True, exist_ok=True)
    mounts = []
    for variable, target in (('DOWNLOAD_CACHE', 'downloads'), ('SSTATE_CACHE', 'sstate-cache')):
        if os.environ.get(variable): mounts += ['-v', str(Path(os.environ[variable]).resolve()) + ':/work/artifacts/' + target]
    subprocess.run(['podman', 'run', '--rm', '--userns=keep-id:uid=30000,gid=30000', '--security-opt', 'label=disable',
        '-v', str(ROOT) + ':/work', *mounts, '-w', '/work', '-e', 'KAS_WORK_DIR=/work',
        '-e', 'KAS_BUILD_DIR=/work/artifacts/build/system', KAS_IMAGE, 'build', '/work/kas/system.yml'], check=True)


def source_info():
    source = {'repository': load(ROOT / 'project.json')['repository']}
    try:
        source['commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
        source['epoch'] = int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT, text=True))
        source['dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
    except (subprocess.CalledProcessError, FileNotFoundError):
        source.update(commit=None, epoch=0, dirty=True)
    return source


def fetch_package(package, cache):
    path = cache / package['url'].rsplit('/', 1)[-1]
    if path.exists() and digest(path) == package['sha256']: return path
    partial = path.with_suffix(path.suffix + '.part')
    try:
        with urllib.request.urlopen(package['url'], timeout=120) as response, partial.open('wb') as target:
            shutil.copyfileobj(response, target)
        if digest(partial) != package['sha256']: raise ValueError('Vendor package checksum mismatch: ' + package['name'])
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def deterministic_tar(directory, destination, epoch):
    with destination.open('wb') as output:
        with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed:
            process = subprocess.Popen(['tar', '--sort=name', '--format=posix',
                '--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime',
                '--mtime=@' + str(epoch), '--owner=0', '--group=0', '--numeric-owner',
                '-C', str(directory), '-cf', '-', '.'], stdout=subprocess.PIPE)
            shutil.copyfileobj(process.stdout, compressed)
            process.stdout.close()
            if process.wait(): raise RuntimeError('Vendor archive creation failed')


def boot_tools(output):
    lock = load(ROOT / 'boards/ventuno-q/boot-tools.lock.json')['grubModules']
    cache = ROOT / 'artifacts/boot-tools-cache'
    cache.mkdir(parents=True, exist_ok=True)
    archive = fetch_package({'name': 'grub-efi-arm64-bin', **lock}, cache)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='boot-tools-') as temporary:
        members = subprocess.check_output(['ar', 't', str(archive)], text=True).splitlines()
        member, = [name for name in members if name.startswith('data.tar')]
        data = Path(temporary) / member
        with data.open('wb') as stream: subprocess.run(['ar', 'p', str(archive), member], stdout=stream, check=True)
        subprocess.run(['tar', '-xf', str(data), '--no-same-owner', '-C', str(output)], check=True)
    return output / lock['directory']


def package_bsp(destination, deploy=None):
    destination = Path(destination)
    deploy = Path(deploy) if deploy else ROOT / 'artifacts/build/system/tmp/deploy/images/ventuno-q'
    initramfs = deploy / 'pantavisor-initramfs-ventuno-q.cpio.gz'
    source = source_info()
    lock = load(ROOT / 'boards/ventuno-q/platform.lock.json')
    policy = load(ROOT / lock['selection'])
    cache = Path(os.environ.get('VENDOR_CACHE', str(ROOT / 'artifacts/vendor-packages')))
    cache.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=ROOT / 'artifacts', prefix='vendor-') as temporary:
        work = Path(temporary)
        unpacked = work / 'root'
        unpacked.mkdir()
        for package in lock['packages']:
            archive = fetch_package(package, cache)
            members = subprocess.check_output(['ar', 't', str(archive)], text=True).splitlines()
            member, = [name for name in members if name == 'data.tar' or name.startswith('data.tar.')]
            data = work / member
            with data.open('wb') as stream: subprocess.run(['ar', 'p', str(archive), member], stdout=stream, check=True)
            subprocess.run(['tar', '-xf', str(data), '--no-same-owner', '-C', str(unpacked)], check=True)
            data.unlink()
        release = lock['kernelRelease']
        if (unpacked / 'usr/lib/firmware').is_dir():
            shutil.copytree(unpacked / 'usr/lib/firmware', unpacked / 'lib/firmware', dirs_exist_ok=True, symlinks=True)
        if digest(unpacked / 'boot' / ('vmlinuz-' + release)) != lock['kernelSha256']:
            raise ValueError('Kernel checksum mismatch')
        if digest(unpacked / 'boot' / ('config-' + release)) != lock['kernelConfigSha256']:
            raise ValueError('Kernel configuration mismatch')
        subprocess.run(['depmod', '-b', str(unpacked), release], check=True)
        selected = work / 'selected'
        selection = copy_selection(unpacked, selected, 'lib', release, policy)
        for relative in ('boot', 'etc/modprobe.d', 'usr/lib/modprobe.d'):
            (selected / relative).mkdir(parents=True, exist_ok=True)
        notices = destination / 'licenses'
        notices.mkdir()
        for path in (unpacked / 'usr/share/doc').glob('*/copyright'):
            if path.is_file() and not path.is_symlink(): shutil.copyfile(path, notices / (path.parent.name + '.copyright'))
        for prefix in ('vmlinuz-', 'config-'):
            shutil.copyfile(unpacked / 'boot' / (prefix + release), selected / 'boot' / (prefix + release))
        subprocess.run(['depmod', '-b', str(selected), release], check=True)
        deterministic_tar(selected, destination / 'vendor-root.tar.gz', source['epoch'])
        shutil.copyfile(selected / 'boot' / ('vmlinuz-' + release), destination / 'kernel.img')
    shutil.copyfile(initramfs, destination / 'initramfs-base.cpio.gz')
    shutil.copyfile(deploy / 'pantavisor-initramfs-ventuno-q.spdx.tar.zst', destination / 'sbom.spdx.tar.zst')
    (destination / 'selection.json').write_bytes(canonical(policy))
    (destination / 'selection-result.json').write_bytes(canonical(selection))
    (destination / 'platform.lock.json').write_bytes(canonical(lock))
    from .cpio import newc_members
    names = {name for name, _, _ in newc_members(gzip.decompress(initramfs.read_bytes()))}
    if not {'usr/sbin/ventuno-native-guard', 'usr/sbin/ventuno-native-bootctl'} <= names:
        raise ValueError('Initramfs lacks native bootstrap support')
    contracts = ['emmc-file-store-v1']
    (destination / 'bsp.json').write_bytes(canonical({'schema': 1, 'kind': 'bsp-components', 'kernel_release': release,
        'kernel_sha256': lock['kernelSha256'], 'kernel_config_sha256': lock['kernelConfigSha256'],
        'archive': 'vendor-root.tar.gz', 'sha256': digest(destination / 'vendor-root.tar.gz'),
        'deviceProvisioningRequired': True, 'bootContract': contracts[0], 'bootContracts': contracts}))
    (destination / 'component.json').write_bytes(canonical({'schema': 2, 'kind': 'bsp', 'name': 'bsp',
        'version': version((ROOT / 'VERSION').read_text().strip()), 'architecture': 'arm64',
        'source': source, 'bootContracts': contracts,
        'files': {p.relative_to(destination).as_posix(): {'sha256': digest(p), 'size': p.stat().st_size}
                  for p in sorted(destination.rglob('*')) if p.is_file()}}))
    verify_component(destination)
    return destination / 'component.json'
