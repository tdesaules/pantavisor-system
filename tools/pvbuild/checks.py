"""Fast local checks; never access a board or registry."""
import re
import subprocess
import sys
from .model import ROOT, compose


def check():
    compose('base'); compose('k0s')
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT, check=True)
    subprocess.run([sys.executable, '-m', 'compileall', '-q', 'tools', 'tests'], cwd=ROOT, check=True)
    for path in [*(ROOT / 'runtime').rglob('*'), *(ROOT / 'recovery').rglob('*')]:
        if path.is_file() and path.read_bytes().startswith(b'#!/bin/sh'):
            subprocess.run(['sh', '-n', str(path)], check=True)
    for path in [ROOT / 'README.md', *(ROOT / 'docs').rglob('*.md')]:
        if not path.exists(): continue
        for target in re.findall(r'\]\(([^\s)]+)', path.read_text()):
            if target.startswith(('http:', 'https:', '#', 'mailto:')): continue
            target = target.split('#', 1)[0]
            if target and not (path.parent / target).exists(): raise ValueError(f'Broken link in {path}: {target}')
    print('PASS: System contracts, tests, syntax and documentation')
