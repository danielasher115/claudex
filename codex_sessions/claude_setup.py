"""Install the caller-context hook and bundled skill while preserving user configuration."""
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from .claude_hook import LAUNCH_TOOL
from .guide import read_skill


def _context_exporter(handler):
    if not isinstance(handler, dict) or handler.get('type') != 'command' or not isinstance(handler.get('command'), str):
        return False
    try:
        if 'args' in handler:
            args = handler['args']
            if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                return False
            argv = [handler['command'], *args]
        else:
            argv = shlex.split(handler['command'])
            if len(argv) > 2 and argv[0] == 'env' and argv[1].startswith('PYTHONPATH='):
                argv = argv[2:]
    except ValueError:
        return False
    return (len(argv) == 6 and argv[1:5] == ['-m', 'codex_sessions', 'claude-hook', '--data-dir']
            and bool(re.fullmatch(r'pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?', Path(argv[0]).name, re.IGNORECASE)))


def _install_skill(config_dir):
    directory = config_dir / 'skills' / 'codex-agents'
    path = directory / 'SKILL.md'
    for candidate in (directory.parent, directory, path):
        if candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
            raise ValueError(f'Cannot install the managed skill through a linked path: {candidate}')
    content = read_skill().encode('utf-8')
    original = path.read_bytes() if path.exists() else None
    result = {'skill': path, 'skill_changed': original != content, 'skill_backup': None}
    if original == content:
        return result
    directory.mkdir(parents=True, exist_ok=True)
    if original is not None:
        backup = path.with_name(path.name + '.before-claudex.' + hashlib.sha256(original).hexdigest())
        try:
            with backup.open('xb') as stream:
                stream.write(original)
        except FileExistsError:
            if backup.is_symlink() or backup.read_bytes() != original:
                raise ValueError(f'The existing skill backup could not be verified: {backup}')
        result['skill_backup'] = backup
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.claudex-skill-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def install(data_dir, settings_path=None):
    config_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude')).expanduser()
    path = Path(settings_path or config_dir / 'settings.json').expanduser().resolve()
    original = path.read_text(encoding="utf-8") if path.exists() else '{}'
    settings = json.loads(original)
    if not isinstance(settings, dict) or not isinstance(settings.get('hooks', {}), dict):
        raise ValueError('Claude settings and hooks must be JSON objects; the configuration was not changed')
    hooks = settings.setdefault('hooks', {})
    entries = hooks.setdefault('PreToolUse', [])
    if not isinstance(entries, list):
        raise ValueError('Claude PreToolUse hooks must be a list; the configuration was not changed')
    hook = {'matcher': LAUNCH_TOOL.pattern,
            'hooks': [{'type': 'command', 'command': sys.executable,
                       'args': ['-m', 'codex_sessions', 'claude-hook', '--data-dir', str(Path(data_dir).expanduser().resolve())],
                       'timeout': 30}]}
    backup = path.with_name(path.name + '.before-codex-context')
    updated = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
            updated.append(entry)
            continue
        remaining = [handler for handler in entry['hooks'] if not _context_exporter(handler)]
        if remaining or not entry['hooks']:
            updated.append({**entry, 'hooks': remaining})
    updated.append(hook)
    skill = _install_skill(path.parent)
    if updated == entries:
        return {'settings': path, 'backup': backup, 'changed': skill['skill_changed'], 'hook_changed': False, **skill}
    hooks['PreToolUse'] = updated
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as stream:
            stream.write(original)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.codex-context-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(settings, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'settings': path, 'backup': backup, 'changed': True, 'hook_changed': True, **skill}
