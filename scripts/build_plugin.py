#!/usr/bin/env python3
"""Build a complete XPI; optionally copy it into a profile with a backup.

Do not edit extensions.json. The running addon reconciles registration through
AddonManager on its next startup if the registered version is stale.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output=None):
    source = ROOT / 'zotero-plugin'
    manifest = json.loads((source / 'manifest.json').read_text())
    version = manifest['version']
    rdf = (source / 'install.rdf').read_text()
    bootstrap = (source / 'bootstrap.js').read_text()
    if f'<em:version>{version}</em:version>' not in rdf or f"const BRIDGE_VERSION = '{version}'" not in bootstrap:
        raise ValueError('Plugin version declarations disagree')
    for locale in ('en-US', 'zh-CN'):
        if not (source / 'locale' / locale / 'evidence-bridge.ftl').is_file():
            raise ValueError('Missing locale: ' + locale)
    output = Path(output or ROOT / 'dist' / f'evidence-bridge-{version}.xpi')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            # Zotero's locale directory enumeration requires real ZIP directory entries.
            archive.write(path, path.relative_to(source))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError('Corrupt XPI')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--install-profile', type=Path)
    args = parser.parse_args()
    package = build(args.output)
    print(package)
    if args.install_profile:
        target = args.install_profile.expanduser() / 'extensions' / 'evidence-bridge@searchtools.org.xpi'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, target.with_suffix('.xpi.backup-' + time.strftime('%Y%m%d-%H%M%S')))
        shutil.copy2(package, target)
        print('Installed with backup. Restart Zotero to load the package.')


if __name__ == '__main__':
    main()
