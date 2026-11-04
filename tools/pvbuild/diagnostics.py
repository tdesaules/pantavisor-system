"""Read-only SSH sampling for reset diagnosis; no reboot or disk writes on target."""
import datetime
import json
from pathlib import Path
import subprocess
import time

COMMAND = r'''for item in /proc/sys/kernel/random/boot_id /proc/uptime /proc/cmdline /proc/meminfo /proc/pressure/* /trial-status/*.status /run/pantavisor-time.status /sys/class/watchdog/watchdog*/identity /sys/class/watchdog/watchdog*/state /sys/class/watchdog/watchdog*/timeout /sys/class/thermal/thermal_zone*/temp /sys/fs/pstore/*; do
    if [ -f "$item" ] && [ -r "$item" ]; then printf '\n### %s\n' "$item"; cat "$item"; fi
done
printf '\n### kernel\n'; uname -a
printf '\n### network\n'; ip -s link; ip route
printf '\n### kernel ring buffer\n'; dmesg 2>&1
'''


def collect(host, port, key, known_hosts, output, samples=60, interval=10):
    if host.startswith('-') or not host or any(c.isspace() for c in host): raise ValueError('Invalid SSH host')
    if not 1 <= port <= 65535 or samples < 1 or interval < 1: raise ValueError('Invalid sampling parameters')
    output = Path(output)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    command = ['ssh', '-F', '/dev/null', '-i', str(key), '-p', str(port), '-o', 'BatchMode=yes',
               '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
               '-o', 'UserKnownHostsFile=' + str(known_hosts), host, COMMAND]
    with (output / 'samples.jsonl').open('x') as report:
        for number in range(samples):
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                record = {'sample': number, 'time': timestamp, 'returncode': result.returncode,
                          'stdout': result.stdout, 'stderr': result.stderr}
            except subprocess.TimeoutExpired:
                record = {'sample': number, 'time': timestamp, 'error': 'SSH timeout'}
            report.write(json.dumps(record) + '\n')
            report.flush()
            if number + 1 < samples: time.sleep(interval)
    return output / 'samples.jsonl'
