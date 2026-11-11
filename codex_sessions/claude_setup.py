"""Install the narrowly scoped caller-context hook without replacing user settings."""
import json
import os
import sys
import tempfile
from pathlib import Path


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
    hook = {'matcher': '^mcp__codex__(agents_launch|codex)$',
            'hooks': [{'type': 'command', 'command': sys.executable,
                       'args': ['-m', 'codex_sessions', 'claude-hook', '--data-dir', str(Path(data_dir).expanduser().resolve())],
                       'timeout': 30}]}
    backup = path.with_name(path.name + '.before-codex-context')
    if hook in entries:
        return {'settings': path, 'backup': backup, 'changed': False}
    entries.append(hook)
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
    return {'settings': path, 'backup': backup, 'changed': True}
