"""Offline module dependency closure and snapshot-rooted firmware resolution."""
import fnmatch
from pathlib import Path, PurePosixPath
import posixpath
import shlex
import shutil
import subprocess


def module_closure(root, release, seeds):
    modules = root / 'lib/modules' / release
    selected = set()
    builtins = set()
    for seed in seeds:
        text = subprocess.check_output([
            'modprobe', '-C', '/dev/null', '-d', str(root), '-S', release,
            '--show-depends', seed,
        ], text=True)
        for line in text.splitlines():
            fields = shlex.split(line)
            if fields[0] == 'insmod':
                selected.add(Path(fields[1]).relative_to(modules).as_posix())
            elif fields[0] == 'builtin':
                builtins.add(fields[1])
            else:
                raise ValueError(f'Unexpected modprobe action: {line}')
    return sorted(selected), sorted(builtins)


def resolve_firmware(root, relative):
    """Resolve all symlink components inside the snapshot, never the host /lib."""
    for _ in range(40):
        relative = posixpath.normpath(relative)
        if relative.startswith('/') or '..' in PurePosixPath(relative).parts:
            raise ValueError(f'Firmware path escapes snapshot: {relative}')
        parts = PurePosixPath(relative).parts
        for i in range(1, len(parts) + 1):
            path = root.joinpath(*parts[:i])
            if path.is_symlink():
                target = str(path.readlink())
                if target.startswith('/'):
                    prefixes = ('/lib/firmware/', '/usr/lib/firmware/')
                    prefix = next((p for p in prefixes if target.startswith(p)), None)
                    if not prefix:
                        raise ValueError(f'Unexpected absolute firmware symlink: {target}')
                    target = target[len(prefix):]
                else:
                    target = posixpath.join(*parts[:i-1], target)
                relative = posixpath.join(target, *parts[i:])
                break
        else:
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(path)
            return path
    raise ValueError(f'Firmware symlink loop: {relative}')


def select_firmware(root, release, policy):
    selected = set()
    resolved_required = {}
    for request in policy['firmware_required']:
        found = []
        for prefix in (f'updates/{release}/', 'updates/', f'{release}/', ''):
            for suffix in ('', '.zst', '.xz'):
                candidate = prefix + request + suffix
                try:
                    resolve_firmware(root, candidate)
                except FileNotFoundError:
                    continue
                found.append(candidate)
        if not found:
            raise FileNotFoundError(f'Required firmware missing: {request}')
        # Preserve candidates and let the target kernel apply its search and
        # compression ordering; do not emulate firmware_class precedence here.
        selected.update(found)
        resolved_required[request] = found
    for path in root.rglob('*'):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatchcase(relative, p) for p in policy['firmware_optional_patterns']):
            # Optional patterns are not permission to copy dangling links.
            resolve_firmware(root, relative)
            selected.add(relative)
    return sorted(selected), resolved_required


def copy_selection(unpacked, staging, libdir, release, policy):
    src = unpacked / 'lib/modules' / release
    modules, builtins = module_closure(unpacked, release, policy['module_seeds'])
    dst = staging / libdir / 'modules' / release
    dst.mkdir(parents=True)
    for relative in modules:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / relative, target)
    for name in ('modules.builtin', 'modules.builtin.modinfo', 'modules.order'):
        shutil.copyfile(src / name, dst / name)
    fwroot = unpacked / 'lib/firmware'
    firmware, required = select_firmware(fwroot, release, policy)
    for relative in firmware:
        target = staging / libdir / 'firmware' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Flatten aliases at their requested name, preserving kernel lookup paths.
        shutil.copyfile(resolve_firmware(fwroot, relative), target)
    return {'modules': modules, 'builtins': builtins, 'firmware': firmware, 'firmware_required': required}
