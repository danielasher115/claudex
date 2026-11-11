"""Regenerate bundled JavaScript dependency notices from the installed production dependency tree."""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = subprocess.run(['npm.cmd' if os.name == 'nt' else 'npm', 'ls', '--omit=dev', '--all', '--long', '--json'],
                            cwd=ROOT / 'ui', capture_output=True, text=True, check=True)
    packages = {}
    def visit(tree):
        for name, item in tree.get('dependencies', {}).items():
            packages[(name, item['version'])] = item
            visit(item)
    visit(json.loads(result.stdout))
    for name in ('tailwindcss', '@tailwindcss/typography'):
        folder = ROOT / 'ui' / 'node_modules' / name
        manifest = json.loads((folder / 'package.json').read_text(encoding='utf-8'))
        packages[(name, manifest['version'])] = {'path': str(folder)}
    sections = ['Claudex third-party notices\n\nThe built dashboard includes software from these JavaScript dependencies and stylesheet generators.\n'
                'Python dependencies are installed separately and retain the notices in their own distributions.\n']
    missing = []
    for name, version in sorted(packages):
        folder = Path(packages[(name, version)]['path'])
        manifest = json.loads((folder / 'package.json').read_text(encoding='utf-8'))
        notices = [path for path in sorted(folder.iterdir()) if path.is_file() and re.match(r'(?i)^(licen[cs]e|copying|notice)(?:[.-]|$)', path.name)]
        if not notices:
            missing.append(name)
            continue
        sections.append('\n' + '=' * 72 + f'\n{name} {version}\nLicense: {manifest.get("license", "see text below")}\n')
        for path in notices:
            sections.append(f'\n{path.name}\n\n{path.read_text(encoding="utf-8", errors="replace").strip()}\n')
    if missing:
        raise SystemExit('Missing license texts: ' + ', '.join(missing))
    (ROOT / 'THIRD_PARTY_NOTICES.txt').write_text('\n'.join(sections), encoding='utf-8')
    print(f'Wrote notices for {len(packages)} bundled dependencies.')


if __name__ == '__main__':
    main()
