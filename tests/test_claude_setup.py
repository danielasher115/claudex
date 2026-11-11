import json
import subprocess
import sys

from codex_sessions.claude_setup import install


def test_install_preserves_settings_and_other_hooks_without_duplicate_registration(tmp_path):
    settings = tmp_path / 'settings.json'
    original = {'model': 'sonnet', 'permissions': {'deny': ['Read(.env)']},
                'hooks': {'PreToolUse': [{'matcher': 'Write', 'hooks': [{'type': 'command', 'command': 'existing-check'}]}]}}
    settings.write_text(json.dumps(original))
    first = install(tmp_path / 'data', settings)
    assert first['changed'] is True
    data = json.loads(settings.read_text())
    assert data['model'] == 'sonnet' and data['permissions'] == original['permissions']
    assert data['hooks']['PreToolUse'][0] == original['hooks']['PreToolUse'][0]
    hook = data['hooks']['PreToolUse'][1]
    assert hook['matcher'] == '^mcp__codex__(agents_launch|codex)$'
    assert 'permissionDecision' not in json.dumps(hook)
    assert 'claude-hook' in hook['hooks'][0]['args']
    assert json.loads(first['backup'].read_text()) == original
    assert install(tmp_path / 'data', settings)['changed'] is False
    assert len(json.loads(settings.read_text())['hooks']['PreToolUse']) == 2


def test_hook_cli_ignores_unrelated_tools_without_reading_context(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'codex_sessions', 'claude-hook', '--data-dir', str(tmp_path)],
                            input=json.dumps({'tool_name': 'mcp__other__agents_launch', 'tool_input': {}}), text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}


def test_installed_hook_uses_direct_execution_with_safe_argument_boundaries(tmp_path):
    folder = tmp_path / "path with spaces and 'quotes'"
    folder.mkdir()
    settings = folder / 'settings.json'
    install(folder / 'data', settings)
    handler = json.loads(settings.read_text())['hooks']['PreToolUse'][0]['hooks'][0]
    assert handler['command'] == sys.executable
    assert handler['args'][:3] == ['-m', 'codex_sessions', 'claude-hook']
    result = subprocess.run([handler['command'], *handler['args']],
                            input=json.dumps({'tool_name': 'unrelated', 'tool_input': {}}),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
