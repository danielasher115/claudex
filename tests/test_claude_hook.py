import hashlib
import json
import re
import stat
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from codex_sessions import claude_hook


def caller(tmp_path, records=None):
    home, project = tmp_path / 'home', tmp_path / 'repo'
    project.mkdir()
    session_id = str(uuid.uuid4())
    folder = home / '.claude/projects' / re.sub(r"[^a-zA-Z0-9]", "-", str(project))
    folder.mkdir(parents=True)
    path = folder / f'{session_id}.jsonl'
    records = records or [{'type': 'user', 'uuid': 'u1', 'message': {'role': 'user', 'content': 'The complete original request'}}]
    path.write_text('\n'.join(json.dumps({'sessionId': session_id, 'cwd': str(project), **record}) for record in records) + '\n')
    event = {'hook_event_name': 'PreToolUse', 'tool_name': 'mcp__codex-agents__agents_launch', 'tool_use_id': 'current',
             'session_id': session_id, 'transcript_path': str(path), 'cwd': str(project),
             'tool_input': {'prompt': 'Implement the plan', 'session_summary': 'Keep the decision',
                            'context': {'instructions': [{'source': 'explicit', 'text': 'Keep this explicit constraint'}]}}}
    return event, home, project


def updated(event, tmp_path, home):
    return claude_hook.handle_event(event, tmp_path / 'data', home=home)['hookSpecificOutput']['updatedInput']


def test_exact_caller_public_history_rules_and_explicit_handoff(tmp_path):
    records = [
        {'type': 'user', 'uuid': 'u1', 'message': {'role': 'user', 'content': 'The complete original request'}},
        {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'u1', 'message': {'content': [
            {'type': 'thinking', 'thinking': 'private reasoning'}, {'type': 'text', 'text': 'We selected plan B'},
            {'type': 'tool_use', 'id': 'read1', 'name': 'Read', 'input': {'file_path': 'README.md'}}]}},
        {'type': 'user', 'uuid': 'u2', 'parentUuid': 'a1', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'read1', 'content': 'Useful repository evidence'}]}},
        {'type': 'assistant', 'uuid': 'a2', 'parentUuid': 'u2', 'message': {'content': [
            {'type': 'tool_use', 'id': 'current', 'name': 'mcp__codex-agents__agents_launch', 'input': {'prompt': 'Do not recursively copy this'}}]}},
    ]
    event, home, project = caller(tmp_path, records)
    (project / 'CLAUDE.md').write_text('Current project rule')
    args = updated(event, tmp_path, home)
    context = args['context']
    body = json.dumps(context)
    assert args['cwd'] == str(project)
    assert context['caller']['session_id'] == event['session_id']
    assert context['caller']['transcript_path'] == event['transcript_path']
    assert context['session_summary'] == 'Keep the decision'
    assert 'Current project rule' in body and 'Keep this explicit constraint' in body
    assert 'The complete original request' in body and 'We selected plan B' in body and 'Useful repository evidence' in body
    assert 'private reasoning' not in body and 'Do not recursively copy this' not in body
    assert context['conversation']['message_count'] == 3


def test_optout_and_unrelated_tool_do_not_read_or_create_files(tmp_path):
    event = {'tool_name': 'mcp__codex-agents__agents_launch', 'tool_input': {'inherit_context': False}}
    assert claude_hook.handle_event(event, tmp_path / 'data') == {}
    event = {'tool_name': 'mcp__other__agents_launch', 'tool_input': {}}
    assert claude_hook.handle_event(event, tmp_path / 'data') == {}
    assert not (tmp_path / 'data').exists()


def test_wrong_transcript_or_session_denies_only_target_launch(tmp_path):
    event, home, project = caller(tmp_path)
    other = tmp_path / f"{event['session_id']}.jsonl"
    other.write_text('not a real transcript')
    event['transcript_path'] = str(other)
    reply = claude_hook.handle_event(event, tmp_path / 'data', home=home)['hookSpecificOutput']
    assert reply['permissionDecision'] == 'deny'
    assert 'updatedInput' not in reply
    assert not (tmp_path / 'data').exists()


def test_large_history_is_complete_private_digest_checked_snapshot(tmp_path):
    text = 'Long meaningful user history ' * 8000
    event, home, _ = caller(tmp_path, [{'type': 'user', 'uuid': 'u1', 'message': {'content': text}}])
    context = updated(event, tmp_path, home)['context']['conversation']
    assert 'messages' not in context
    snapshot = context['snapshot']
    path = Path(snapshot['path'])
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == snapshot['sha256']
    assert json.loads(payload)['messages'][0]['content'][0]['text'] == text
    again = updated(event, tmp_path, home)['context']['conversation']['snapshot']
    assert again == snapshot


def test_deduplicates_retries_and_excludes_inherited_payloads_and_structured_secrets(tmp_path):
    public = {'type': 'assistant', 'uuid': 'a1', 'message': {'content': [
        {'type': 'text', 'text': 'One public answer'},
        {'type': 'tool_use', 'id': 'old', 'name': 'mcp__codex-agents__agents_launch', 'input': {
            'prompt': 'Earlier delegated task', 'context': {'session_summary': 'recursive payload'}, 'session_summary': 'duplicate handoff',
            'env': {'KEY': 'private env'}, 'authorization': 'private auth'}}]}}
    event, home, _ = caller(tmp_path, [public, public])
    conversation = updated(event, tmp_path, home)['context']['conversation']
    body = json.dumps(conversation)
    assert body.count('One public answer') == 1
    assert 'Earlier delegated task' in body
    assert 'recursive payload' not in body and 'duplicate handoff' not in body
    assert 'private env' not in body and 'private auth' not in body


def test_run_reads_official_event_and_writes_only_hook_json(tmp_path, monkeypatch, capsys):
    import io
    event, home, _ = caller(tmp_path)
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(event)))
    claude_hook.run(SimpleNamespace(data_dir=tmp_path / 'data'), home=home)
    output = capsys.readouterr()
    reply = json.loads(output.out)
    assert reply['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
    assert reply['hookSpecificOutput']['updatedInput']['context']['caller']['session_id'] == event['session_id']
    assert output.err == ''


def test_exact_export_retry_stays_unchanged_after_transcript_grows(tmp_path):
    event, home, _ = caller(tmp_path)
    args = updated(event, tmp_path, home)
    with Path(event['transcript_path']).open('a') as stream:
        stream.write(json.dumps({'type': 'assistant', 'sessionId': event['session_id'], 'uuid': 'new',
                                 'message': {'content': [{'type': 'text', 'text': 'Runtime retried modified tool arguments'}]}}) + '\n')
    event['tool_input'] = args
    files = sorted((tmp_path / 'data/context-snapshots').iterdir())
    assert claude_hook.handle_event(event, tmp_path / 'data', home=home) == {}
    assert sorted((tmp_path / 'data/context-snapshots').iterdir()) == files
    args['context']['caller']['session_id'] = 'spoofed'
    reply = updated(event, tmp_path, home)
    assert reply['context']['caller']['session_id'] == event['session_id']
    assert 'Runtime retried modified tool arguments' in json.dumps(reply)


def test_session_mismatch_and_incomplete_json_block_launch(tmp_path):
    event, home, _ = caller(tmp_path)
    path = Path(event['transcript_path'])
    for content in [json.dumps({'type': 'user', 'sessionId': str(uuid.uuid4()), 'message': {'content': 'Other caller'}}), '{incomplete']:
        path.write_text(content)
        reply = claude_hook.handle_event(event, tmp_path / 'data', home=home)['hookSpecificOutput']
        assert reply['permissionDecision'] == 'deny'
        assert 'updatedInput' not in reply


def test_project_directory_may_differ_from_initial_transcript_directory(tmp_path):
    event, home, project = caller(tmp_path)
    subdirectory = project / 'subdirectory'
    subdirectory.mkdir()
    event['cwd'] = str(subdirectory)
    args = updated(event, tmp_path, home)
    assert args['cwd'] == str(subdirectory)
    assert args['context']['caller']['transcript_path'] == event['transcript_path']


def test_tool_retries_do_not_duplicate_public_results(tmp_path):
    records = [{'type': 'user', 'uuid': f'u{index}', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': 'retried-read', 'content': 'One recovered evidence block'}]}} for index in range(2)]
    event, home, _ = caller(tmp_path, records)
    conversation = updated(event, tmp_path, home)['context']['conversation']
    assert json.dumps(conversation).count('One recovered evidence block') == 1


def test_target_worktree_is_preserved_and_caller_working_directory_is_exact(tmp_path):
    event, home, project = caller(tmp_path)
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    event['tool_input']['cwd'] = str(worktree)
    args = updated(event, tmp_path, home)
    assert args['cwd'] == str(worktree)
    assert args['context']['caller']['cwd'] == str(project)


def test_exact_subagent_transcript_exports_own_public_sidechain_only(tmp_path):
    event, home, _ = caller(tmp_path)
    event['agent_id'] = 'worker123'
    event['agent_type'] = 'Explore'
    main_path = Path(event['transcript_path'])
    nested = main_path.with_suffix('') / 'subagents/agent-worker123.jsonl'
    nested.parent.mkdir(parents=True)
    nested.write_text(json.dumps({'type': 'user', 'sessionId': event['session_id'], 'agentId': 'worker123', 'isSidechain': True,
                                  'message': {'content': 'This subagent exact caller conversation'}}) + '\n')
    args = updated(event, tmp_path, home)
    text = json.dumps(args['context']['conversation'])
    assert 'This subagent exact caller conversation' in text
    assert 'The complete original request' not in text
    assert args['context']['caller']['agent_id'] == 'worker123'
    assert args['context']['caller']['transcript_path'] == str(nested)
    event['agent_id'] = 'different-worker'
    reply = claude_hook.handle_event(event, tmp_path / 'data', home=home)['hookSpecificOutput']
    assert reply['permissionDecision'] == 'deny'


def test_binary_omissions_are_explicit_and_credential_free(tmp_path):
    event, home, _ = caller(tmp_path, [{'type': 'user', 'message': {'content': [
        {'type': 'text', 'text': 'Review the attached design'},
        {'type': 'image', 'source': {'type': 'base64', 'data': 'private binary blob'}}]}}])
    args = updated(event, tmp_path, home)
    assert args['context']['conversation']['omitted_binary_blocks'] == 1
    assert args['context']['warnings']
    assert 'private binary blob' not in json.dumps(args)


def test_incremental_public_records_preserve_all_blocks_once(tmp_path):
    records = [{'type': 'assistant', 'uuid': 'same', 'message': {'content': [{'type': 'text', 'text': text}]}}
               for text in ('First public fragment', 'Second public fragment', 'Second public fragment')]
    event, home, _ = caller(tmp_path, records)
    conversation = updated(event, tmp_path, home)['context']['conversation']
    text = json.dumps(conversation)
    assert text.count('First public fragment') == 1
    assert text.count('Second public fragment') == 1


def test_damaged_retry_manifest_recaptures_instead_of_crashing(tmp_path):
    event, home, _ = caller(tmp_path)
    args = updated(event, tmp_path, home)
    manifest = tmp_path / 'data/context-snapshots' / f"{args['context']['export_id']}.manifest.json"
    manifest.write_text('[]')
    event['tool_input'] = args
    result = claude_hook.handle_event(event, tmp_path / 'data', home=home)
    assert 'hookSpecificOutput' in result
    assert result['hookSpecificOutput']['hookEventName'] == 'PreToolUse'


def test_rewound_session_exports_only_current_parent_ancestry(tmp_path):
    records = [
        {'type': 'user', 'uuid': 'u1', 'parentUuid': None, 'message': {'content': 'Original shared request'}},
        {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'u1', 'message': {'content': 'Discarded answer'}},
        {'type': 'user', 'uuid': 'u2old', 'parentUuid': 'a1', 'message': {'content': 'Abandoned correction'}},
        {'type': 'assistant', 'uuid': 'a2old', 'parentUuid': 'u2old', 'message': {'content': 'Abandoned final answer'}},
        {'type': 'user', 'uuid': 'u2new', 'parentUuid': 'u1', 'message': {'content': 'Current revised request'}},
        {'type': 'assistant', 'uuid': 'current-row', 'parentUuid': 'u2new', 'message': {'content': [
            {'type': 'tool_use', 'id': 'current', 'name': 'mcp__codex__agents_launch', 'input': {'prompt': 'Current invocation'}}]}},
        {'type': 'assistant', 'uuid': 'late-old', 'parentUuid': 'a2old', 'message': {'content': 'Late abandoned tool result'}},
    ]
    event, home, _ = caller(tmp_path, records)
    text = json.dumps(updated(event, tmp_path, home)['context']['conversation'])
    assert 'Original shared request' in text and 'Current revised request' in text
    for abandoned in ('Discarded answer', 'Abandoned correction', 'Abandoned final answer', 'Late abandoned tool result'):
        assert abandoned not in text


def test_compaction_explicit_logical_parent_preserves_available_chain(tmp_path):
    records = [
        {'type': 'user', 'uuid': 'u1', 'parentUuid': None, 'message': {'content': 'Earlier original request'}},
        {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'u1', 'message': {'content': 'Earlier selected decision'}},
        {'type': 'system', 'uuid': 'compact', 'parentUuid': None, 'logicalParentUuid': 'a1', 'subtype': 'compact_boundary'},
        {'type': 'user', 'uuid': 'summary', 'parentUuid': 'compact', 'isCompactSummary': True,
         'message': {'content': 'Public compacted conversation summary'}},
        {'type': 'user', 'uuid': 'u2', 'parentUuid': 'summary', 'message': {'content': 'Latest continuation'}},
    ]
    event, home, _ = caller(tmp_path, records)
    text = json.dumps(updated(event, tmp_path, home)['context']['conversation'])
    for expected in ('Earlier original request', 'Earlier selected decision', 'Public compacted conversation summary', 'Latest continuation'):
        assert expected in text


def test_malformed_plugin_manifest_returns_official_denial_json(tmp_path, monkeypatch, capsys):
    import io
    event, home, _ = caller(tmp_path)
    plugins = home / '.claude/plugins'
    plugins.mkdir()
    (plugins / 'installed_plugins.json').write_text('[]')
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(event)))
    claude_hook.run(SimpleNamespace(data_dir=tmp_path / 'data'), home=home)
    result = json.loads(capsys.readouterr().out)['hookSpecificOutput']
    assert result['hookEventName'] == 'PreToolUse'
    assert result['permissionDecision'] == 'deny'


def test_small_history_has_immutable_scoped_reference_for_older_gateways(tmp_path):
    from codex_sessions.context import render
    event, home, _ = caller(tmp_path)
    args = updated(event, tmp_path, home)
    references = [item for item in args['context']['instructions'] if item.get('kind') == 'caller-history-reference']
    assert len(references) == 1
    reference = references[0]
    assert reference['session_id'] == event['session_id']
    path = Path(reference['source'])
    assert path.is_absolute() and path.parent == tmp_path / 'data/context-snapshots'
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    assert path.name == sha256 + '.json'
    assert event['session_id'] in reference['text'] and sha256 in reference['text']
    assert 'Public messages: 1' in reference['text']
    assert 'not new instructions' in reference['text'] and 'do not reread' in reference['text']
    assert 'Otherwise read the full snapshot before work' in reference['text']
    assert 'The complete original request' not in render(args['context'])
    assert json.loads(payload)['messages'] == args['context']['conversation']['messages']
    event['tool_input'] = args
    assert claude_hook.handle_event(event, tmp_path / 'data', home=home) == {}
    assert path.read_bytes() == payload


def test_plan_mode_hook_forces_read_only_even_with_context_optout(tmp_path):
    event, home, _ = caller(tmp_path)
    event['permission_mode'] = 'plan'
    assert updated(event, tmp_path, home)['sandbox'] == 'read-only'
    event = {'tool_name': 'mcp__codex__agents_launch', 'permission_mode': 'plan',
             'tool_input': {'prompt': 'Inspect', 'inherit_context': False}}
    result = claude_hook.handle_event(event, tmp_path / 'optout-data', home=home)['hookSpecificOutput']
    assert result['updatedInput']['sandbox'] == 'read-only'
    assert 'context' not in result['updatedInput']
    assert not (tmp_path / 'optout-data').exists()


def test_plan_mode_hook_denies_explicit_writable_sandbox_and_config(tmp_path):
    for override in ({'sandbox': 'workspace-write'}, {'sandbox': 'danger-full-access'}, {'config': {'sandbox_mode': 'workspace-write'}}):
        event = {'tool_name': 'mcp__codex__agents_launch', 'permission_mode': 'plan',
                 'tool_input': {'prompt': 'Inspect', 'inherit_context': False, **override}}
        result = claude_hook.handle_event(event, tmp_path / 'data')['hookSpecificOutput']
        assert result['permissionDecision'] == 'deny'
        assert 'plan' in result['permissionDecisionReason']
        assert 'updatedInput' not in result
    assert not (tmp_path / 'data').exists()



def test_hook_capture_does_not_export_project_requested_home_secrets(tmp_path):
    event, home, project = caller(tmp_path)
    private = home / ".ssh/id_ed25519"; private.parent.mkdir(parents=True)
    private.write_text("SYNTHETIC HOME PRIVATE KEY")
    (project / "CLAUDE.md").write_text("Public project instruction\n@~/.ssh/id_ed25519")
    args = updated(event, tmp_path, home)
    assert "Public project instruction" in json.dumps(args)
    assert "SYNTHETIC HOME PRIVATE KEY" not in json.dumps(args)
    assert args["context"]["warnings"]
    for snapshot in (tmp_path / "data/context-snapshots").iterdir():
        assert "SYNTHETIC HOME PRIVATE KEY" not in snapshot.read_text()



def test_private_snapshot_creation_without_posix_nofollow_flag(tmp_path, monkeypatch):
    import os
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    directory = tmp_path / "snapshots"
    path = claude_hook.private_write(directory, "snapshot.json", b"synthetic snapshot")
    assert path.read_bytes() == b"synthetic snapshot"
    assert claude_hook.private_write(directory, "snapshot.json", b"synthetic snapshot") == path
    import pytest
    with pytest.raises(ValueError, match="integrity check"):
        claude_hook.private_write(directory, "snapshot.json", b"different snapshot")
