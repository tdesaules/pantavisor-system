"""Isolated local base integration, with fresh SSH keys and a local NTP fixture."""
from pathlib import Path
import socketserver
import struct
import subprocess
import threading
import time
import uuid
from .components import verify_bundle
from .model import canonical, load


class NTPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, socket = self.request
        if len(packet) < 48: return
        def stamp():
            value = time.time() + 2208988800
            return struct.pack('!II', int(value), int((value % 1) * 2**32))
        reply = bytearray(48)
        reply[:4] = bytes((0x24, 1, 4, 236))
        reply[12:16] = b'LOCL'
        reply[16:24] = stamp()
        reply[24:32] = packet[40:48]
        reply[32:40] = stamp()
        reply[40:48] = stamp()
        socket.sendto(reply, self.client_address)


def integration(bundle_path, modules, output, scenarios=None):
    from .components import assemble
    from .render import render
    from .native import prepare
    from .native_test import SCENARIOS, qualify
    bundle_path, output = Path(bundle_path).resolve(), Path(output).resolve()
    bundle = verify_bundle(bundle_path)
    if bundle['profile'] != 'base': raise ValueError('This local harness currently qualifies the base profile')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    client = output / 'client'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(client)], check=True)
    system_uuid = str(uuid.uuid4())
    device = output / 'device.json'
    device.write_bytes(canonical({'schema': 1, 'rootUUID': system_uuid, 'memoryKiB': 2 * 1024**2,
        'ethernetMAC': '02:00:00:00:00:01', 'authorizedKeys': [client.with_suffix('.pub').read_text().strip()]}))
    server = socketserver.ThreadingUDPServer(('127.0.0.1', 0), NTPHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        override = output / 'ntp.json'
        override.write_bytes(canonical({'containers': {'core': {'settings': {
            'ntpServers': ['10.0.2.2:' + str(server.server_address[1])]}}}}))
        # A test-only composition consumes the same immutable component identities.
        bsp = bundle_path / bundle['components']['bsp']['directory']
        core_name = bundle['composition']['containers']['core']['artifact']
        core = bundle_path / bundle['components'][core_name]['directory']
        assembled = output / 'platform'
        assemble(bsp, core, None, assembled, bundle['version'], 'base', [override])
        state = output / 'state'
        render(assembled, device, state)
        package = output / 'package'
        prepare(state, system_uuid, Path(modules).resolve(), package, emulated=True, size_mib=1024)
        qualify(package, client, load(state / 'device-state.json')['sshHostPublicKey'], output / 'results',
                scenarios=scenarios or SCENARIOS)
    finally:
        server.shutdown(); server.server_close()
