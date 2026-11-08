#!/usr/bin/env python3
"""Repair URL forwarding for the user's existing Zotero desktop entry."""
import argparse
from pathlib import Path
import shutil
import time
import subprocess


def repair(path):
    path = Path(path).expanduser()
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if not line.startswith('Exec='):
            continue
        found = True
        if ' -url ' in line or ' --url ' in line:
            continue
        if '%u' not in line and '%U' not in line:
            raise ValueError('Existing Zotero entry has no URI placeholder; inspect it manually')
        lines[i] = line.replace('%u', '-url %u').replace('%U', '-url %U')
    if not found:
        raise ValueError('No Exec entry found')
    replacement = ''.join(lines)
    if replacement != original:
        shutil.copy2(path, path.with_suffix('.desktop.backup-' + time.strftime('%Y%m%d-%H%M%S')))
        path.write_text(replacement)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(path.parent)], check=True, capture_output=True)
    return replacement != original


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', type=Path, default=Path.home()/'.local/share/applications/zotero.desktop')
    args = parser.parse_args()
    print('Desktop URI forwarding repaired' if repair(args.desktop) else 'Desktop URI forwarding already correct')
