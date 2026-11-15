import json
import re
import subprocess
import sys

import pytest

from codex_sessions.claude_setup import install
from codex_sessions.claude_hook import LAUNCH_TOOL
from codex_sessions.guide import SKILL_PATH


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
    assert hook['matcher'] == LAUNCH_TOOL.pattern
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


def test_installed_matcher_covers_every_supported_caller_server_alias(tmp_path):
    settings = tmp_path / 'settings.json'
    install(tmp_path / 'data', settings)
    matcher = json.loads(settings.read_text())['hooks']['PreToolUse'][0]['matcher']
    for server in ('codex', 'codex-agents', 'codex_agents', 'codex-sessions', 'codex_sessions'):
        for tool in ('agents_launch', 'codex'):
            assert re.fullmatch(matcher, f'mcp__{server}__{tool}')
    assert not re.fullmatch(matcher, 'mcp__other__agents_launch')


def test_reinstall_migrates_legacy_exporters_without_losing_unrelated_hooks(tmp_path):
    settings = tmp_path / 'settings.json'
    data_dir = tmp_path / 'existing-state'
    check = {'type': 'command', 'command': 'existing-check'}
    legacy = {'type': 'command', 'command':
              'env PYTHONPATH=/old/private-checkout /old/venv/bin/python -m codex_sessions claude-hook --data-dir ' + str(data_dir),
              'timeout': 30}
    old_exec = {'type': 'command', 'command': '/old/venv/bin/python',
                'args': ['-m', 'codex_sessions', 'claude-hook', '--data-dir', str(data_dir)], 'timeout': 30}
    diagnostic = {'type': 'command', 'command': 'echo', 'args': old_exec['args']}
    original = {'hooks': {'PreToolUse': [
        {'matcher': '^mcp__codex__(agents_launch|codex)$', 'hooks': [check, legacy]},
        {'matcher': 'mcp__codex-agents__agents_launch', 'hooks': [old_exec]},
        {'matcher': 'Write', 'hooks': [check, diagnostic]}]}}
    settings.write_text(json.dumps(original))

    first = install(data_dir, settings)

    assert first['changed'] is True
    installed = json.loads(settings.read_text())['hooks']['PreToolUse']
    assert installed[:2] == [
        {'matcher': '^mcp__codex__(agents_launch|codex)$', 'hooks': [check]},
        {'matcher': 'Write', 'hooks': [check, diagnostic]}]
    assert len(installed) == 3
    assert installed[-1]['matcher'] == LAUNCH_TOOL.pattern
    handler = installed[-1]['hooks'][0]
    assert handler['command'] == sys.executable
    assert handler['args'][-1] == str(data_dir.resolve())
    assert json.loads(first['backup'].read_text()) == original
    assert install(data_dir, settings)['changed'] is False
    assert json.loads(settings.read_text())['hooks']['PreToolUse'] == installed


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


def test_install_copies_packaged_skill_under_selected_settings_root_and_is_idempotent(tmp_path):
    settings = tmp_path / 'claude-config' / 'settings.json'
    first = install(tmp_path / 'data', settings)
    skill = settings.parent / 'skills' / 'codex-agents' / 'SKILL.md'
    assert first['skill'] == skill
    assert first['skill_changed'] is True
    assert skill.read_bytes() == SKILL_PATH.read_bytes()
    assert not skill.is_symlink()
    second = install(tmp_path / 'data', settings)
    assert second['changed'] is False
    assert second['hook_changed'] is False
    assert second['skill_changed'] is False


def test_reinstall_updates_skill_when_hook_is_current_and_preserves_custom_contents(tmp_path, monkeypatch):
    settings = tmp_path / 'settings.json'
    install(tmp_path / 'data', settings)
    skill = tmp_path / 'skills' / 'codex-agents' / 'SKILL.md'
    custom = b'---\nname: codex-agents\n---\nMy custom workflow\n'
    skill.write_bytes(custom)
    monkeypatch.setattr('codex_sessions.claude_setup.read_skill', lambda: 'Updated packaged skill\n')

    result = install(tmp_path / 'data', settings)

    assert result['changed'] is True
    assert result['hook_changed'] is False
    assert result['skill_changed'] is True
    assert result['skill_backup'].read_bytes() == custom
    assert skill.read_text() == 'Updated packaged skill\n'
    assert install(tmp_path / 'data', settings)['changed'] is False
    skill.write_text('Second custom version\n')
    newer = install(tmp_path / 'data', settings)
    assert newer['skill_backup'] != result['skill_backup']
    assert newer['skill_backup'].read_text() == 'Second custom version\n'
    assert result['skill_backup'].read_bytes() == custom


@pytest.mark.skipif(sys.platform == 'win32', reason='Creating symlinks requires Windows privileges')
def test_install_rejects_linked_skill_directory_before_changing_settings(tmp_path):
    settings = tmp_path / 'settings.json'
    settings.write_text('{}')
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'SKILL.md').write_text('Keep custom skill\n')
    skills = tmp_path / 'skills'
    skills.mkdir()
    (skills / 'codex-agents').symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match='linked'):
        install(tmp_path / 'data', settings)

    assert settings.read_text() == '{}'
    assert (external / 'SKILL.md').read_text() == 'Keep custom skill\n'


def test_install_cli_reports_hook_and_skill_in_selected_claude_config(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    result = subprocess.run([sys.executable, '-m', 'codex_sessions', 'install-claude-context', '--data-dir', str(tmp_path / 'data')],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert str(tmp_path / 'claude' / 'settings.json') in result.stdout
    skill = tmp_path / 'claude' / 'skills' / 'codex-agents' / 'SKILL.md'
    assert str(skill) in result.stdout
    assert skill.read_bytes() == SKILL_PATH.read_bytes()
