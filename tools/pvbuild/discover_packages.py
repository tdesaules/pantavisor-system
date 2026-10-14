"""Print vendor-package candidates from an APT index; never edits the lock file."""
import argparse
import gzip
import json
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('packages', nargs='+')
    args = parser.parse_args()
    with urllib.request.urlopen(args.url, timeout=90) as response: data = response.read()
    if args.url.endswith('.gz'): data = gzip.decompress(data)
    matches = []
    for block in data.decode().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line and not line.startswith(' '))
        if fields.get('Package') in args.packages:
            matches.append({key: fields.get(key) for key in ('Package', 'Version', 'Filename', 'SHA256')})
    print(json.dumps(matches, indent=2))


if __name__ == '__main__': main()
