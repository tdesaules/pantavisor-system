import json
from pathlib import Path
import tempfile
import unittest
from tools.pvbuild.components import assemble, verify_bundle, verify_component
from tools.pvbuild.model import canonical, compose, digest


class ComponentsTests(unittest.TestCase):
    def component(self, parent, name, version='0.1.0', architecture='arm64', abi=None):
        directory = parent / name
        directory.mkdir()
        metadata = {'schema': 2, 'name': name, 'version': version, 'architecture': architecture,
                    'source': {'repository': 'test/' + name, 'commit': name}, 'features': []}
        if name == 'bsp':
            metadata.update(kind='bsp', bootContracts=['emmc-file-store-v1'])
            (directory / 'bsp.json').write_bytes(canonical({'bootContracts': ['emmc-file-store-v1']}))
            for name in ('kernel.img', 'initramfs-base.cpio.gz', 'vendor-root.tar.gz', 'selection.json', 'sbom.spdx.tar.zst'):
                (directory / name).write_bytes(b'fixture')
        else:
            abi = abi or ('pantavisor-k0s-v1' if name == 'k0s' else 'pantavisor-core-v1')
            metadata.update(kind='rootfs', runtimeABI=abi)
            if name == 'core': metadata['features'] = ['kubernetes-clients']
            (directory / 'runtime.json').write_bytes(canonical({'id': abi, 'architecture': architecture}))
            for filename in ('rootfs.squashfs', 'lxc.conf.in', 'packages.manifest', 'sbom.spdx.tar.zst'):
                (directory / filename).write_bytes(b'fixture')
            metadata['rootfsSha256'] = digest(directory / 'rootfs.squashfs')
        metadata['files'] = {p.name: {'sha256': digest(p), 'size': p.stat().st_size} for p in directory.iterdir()}
        (directory / 'component.json').write_bytes(canonical(metadata))
        return directory

    def test_different_versions_and_producer_commits_are_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bsp = self.component(root, 'bsp', '0.1.0')
            core = self.component(root, 'core', '0.8.1')
            k0s = self.component(root, 'k0s', '0.3.0')
            assemble(bsp, core, k0s, root / 'result', '0.2.0', 'k0s')
            bundle = verify_bundle(root / 'result')
            self.assertEqual(bundle['version'], '0.2.0')
            self.assertEqual(bundle['components']['core']['metadata']['version'], '0.8.1')
            self.assertEqual(bundle['components']['k0s']['metadata']['source']['commit'], 'k0s')

    def test_incompatible_architecture_and_abi_are_rejected(self):
        for architecture, abi in (('amd64', 'pantavisor-core-v1'), ('arm64', 'unknown-v9')):
            with self.subTest(architecture=architecture, abi=abi), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bsp = self.component(root, 'bsp')
                core = self.component(root, 'core', architecture=architecture, abi=abi)
                with self.assertRaisesRegex(ValueError, 'Incompatible'):
                    assemble(bsp, core, None, root / 'result', '0.1.0', 'base')
                self.assertFalse((root / 'result').exists())

    def test_modified_or_extra_component_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            core = self.component(Path(temporary), 'core')
            (core / 'private-key').write_text('not exportable')
            with self.assertRaises(ValueError): verify_component(core)
            (core / 'private-key').unlink()
            (core / 'rootfs.squashfs').write_text('modified')
            with self.assertRaises(ValueError): verify_component(core)

    def test_bundle_keeps_component_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assemble(self.component(root, 'bsp'), self.component(root, 'core'), None, root / 'result', '0.1.0', 'base')
            path = root / 'result/bundle.json'
            bundle = json.loads(path.read_text())
            bundle['components']['core']['metadata']['version'] = '9.0.0'
            path.write_bytes(canonical(bundle))
            with self.assertRaises(ValueError): verify_bundle(root / 'result')

    def test_profiles_share_the_same_core_and_only_k0s_adds_a_server(self):
        self.assertEqual(set(compose('base')['containers']), {'core'})
        self.assertEqual(set(compose('k0s')['containers']), {'core', 'k0s'})
        self.assertEqual(compose('base')['containers']['core'], compose('k0s')['containers']['core'])

    def test_private_state_is_not_a_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'component.json').write_bytes(canonical({'schema': 2, 'kind': 'private-device-state'}))
            with self.assertRaises(ValueError): verify_component(root)

    def test_all_profiles_require_the_unified_core(self):
        for profile in ('base', 'k0s'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bsp = self.component(root, 'bsp')
                core = self.component(root, 'core')
                cluster = self.component(root, 'k0s') if profile == 'k0s' else None
                metadata = json.loads((core / 'component.json').read_text())
                metadata['features'] = []
                (core / 'component.json').write_bytes(canonical(metadata))
                with self.assertRaisesRegex(ValueError, 'Kubernetes clients'):
                    assemble(bsp, core, cluster, root / 'result', '0.1.0', profile)

    def test_invalid_resource_and_network_overrides_are_rejected(self):
        for patch in ({'hostReserveMiB': 128}, {'settings': {'nodeName': 'bad node'}},
                      {'settings': {'podCIDR': '10.96.0.0/12'}}, {'settings': {'dnsServers': ['not-an-IP']}}):
            with self.subTest(patch=patch), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'override.json'
                path.write_bytes(canonical({'containers': {'k0s': patch}}))
                with self.assertRaises(ValueError): compose('k0s', overrides=[path])
