"""System CLI: build BSP, consume components, render and qualify locally."""
import argparse
from pathlib import Path
import sys
from .model import ROOT, canonical, compose, load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('build', help='Build only the System initramfs with KAS')
    p = commands.add_parser('package-bsp')
    p.add_argument('--deploy-dir', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('boot-tools', help='Fetch checksum-pinned public ARM64 GRUB modules')
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('compose')
    p.add_argument('--profile', choices=('base', 'k0s'), default='base')
    p.add_argument('--override', type=Path, action='append', default=[])
    p = commands.add_parser('assemble', help='Consume independently versioned local component exports')
    p.add_argument('--bsp', type=Path, required=True)
    p.add_argument('--core', type=Path, required=True)
    p.add_argument('--k0s', type=Path)
    p.add_argument('--profile', choices=('base', 'k0s'), default='base')
    p.add_argument('--override', type=Path, action='append', default=[])
    p.add_argument('--version', default=(ROOT / 'VERSION').read_text().strip())
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('verify')
    p.add_argument('path', type=Path)
    p = commands.add_parser('fetch-component')
    p.add_argument('--reference', required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('render')
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--device', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('native-package')
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--system-uuid', required=True)
    p.add_argument('--esp-uuid')
    p.add_argument('--grub-modules', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--emulated', action='store_true')
    p.add_argument('--store-mib', type=int, default=4096)
    p = commands.add_parser('native-test', help='QEMU only: no board connection')
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--ssh-key', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scenario', action='append')
    p = commands.add_parser('integration', help='Isolated local VM with ephemeral inputs (base profile)')
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--grub-modules', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scenario', action='append')
    commands.add_parser('check')
    args = parser.parse_args()
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    if args.command == 'build':
        from .build import run_build
        run_build()
    elif args.command == 'package-bsp':
        from .build import package_bsp
        print(package_bsp(args.output, args.deploy_dir))
    elif args.command == 'boot-tools':
        from .build import boot_tools
        print(boot_tools(args.output))
    elif args.command == 'compose':
        sys.stdout.buffer.write(canonical(compose(args.profile, overrides=args.override)))
    elif args.command == 'assemble':
        from .components import assemble
        print(assemble(args.bsp, args.core, args.k0s, args.output, args.version, args.profile, args.override))
    elif args.command == 'verify':
        from .components import verify_bundle, verify_component
        metadata = verify_bundle(args.path) if (args.path / 'bundle.json').exists() else verify_component(args.path)
        print('PASS:', metadata['kind'], metadata['version'])
    elif args.command == 'fetch-component':
        from .fetch import fetch
        print(fetch(args.reference, args.output))
    elif args.command == 'render':
        from .render import render
        print(render(args.bundle, args.device, args.output))
    elif args.command == 'native-package':
        from .native import prepare
        print(prepare(args.state, args.system_uuid, args.grub_modules, args.output,
                      emulated=args.emulated, size_mib=args.store_mib, esp_uuid=args.esp_uuid))
    elif args.command == 'native-test':
        from .native_test import SCENARIOS, qualify
        qualify(args.package, args.ssh_key, load(args.state / 'device-state.json')['sshHostPublicKey'],
                args.output, scenarios=args.scenario or SCENARIOS)
    elif args.command == 'check':
        from .checks import check
        check()
    elif args.command == 'integration':
        from .integration import integration
        integration(args.bundle, args.grub_modules, args.output, args.scenario)


if __name__ == '__main__':
    try: main()
    except (ValueError, RuntimeError, KeyError, FileNotFoundError) as error: raise SystemExit(str(error)) from error
