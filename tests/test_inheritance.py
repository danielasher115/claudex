import asyncio
import hashlib
import json
import uuid

import pytest

from codex_sessions.context import merge, render
from codex_sessions.inheritance import conversation_inputs
from test_protocol import AgentRuntime
from test_smoke import service


def conversation(messages):
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'version': 1, 'source': 'claude', 'session_id': str(uuid.uuid4()), 'cwd': '/project',
            'transcript_path': '/claude/projects/project/session.jsonl', 'message_count': len(messages),
            'content_sha256': hashlib.sha256(payload).hexdigest(), 'messages': messages}


def test_exact_history_survives_merge_as_user_evidence_not_developer_instructions(tmp_path):
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'The earlier decision was to preserve v1.'}]}]
    bundle = merge({}, {'conversation': conversation(messages), 'caller': {'provider': 'claude', 'permission_mode': 'default'}})
    assert bundle['conversation']['messages'] == messages
    assert 'The earlier decision' not in render(bundle)
    inputs = conversation_inputs(bundle, tmp_path)
    assert 'The earlier decision was to preserve v1.' in inputs[0]['text']
    assert 'reference evidence' in inputs[0]['text']


def test_snapshot_integrity_and_location_are_checked_before_launch(tmp_path):
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'All of the earlier requirements.'}]}]
    data = conversation(messages)
    raw = json.dumps({'version': 1, 'messages': data.pop('messages')}).encode()
    sha = hashlib.sha256(raw).hexdigest()
    folder = tmp_path / 'context-snapshots'; folder.mkdir()
    path = folder / f'{sha}.json'; path.write_bytes(raw)
    data['snapshot'] = {'path': str(path), 'sha256': sha, 'bytes': len(raw)}
    result = conversation_inputs({'conversation': data}, tmp_path)
    assert str(path) in result[0]['text'] and sha in result[0]['text']
    path.write_text('tampered')
    with pytest.raises(ValueError, match='integrity'):
        conversation_inputs({'conversation': data}, tmp_path)
    path.write_bytes(raw)
    data['snapshot']['path'] = str(tmp_path / 'unrelated.json')
    with pytest.raises(ValueError, match='snapshot'):
        conversation_inputs({'conversation': data}, tmp_path)


def test_context_reaches_children_and_claude_plan_mode_cannot_gain_writes(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        messages = [{'role': 'assistant', 'content': [{'type': 'text', 'text': 'The shared decision is durable.'}]}]
        bundle = {'instructions': [{'source': 'CLAUDE.md', 'text': 'Follow the project contract.'}],
                  'memories': [{'source': 'MEMORY.md', 'text': 'Keep backwards compatibility.'}],
                  'caller': {'provider': 'claude', 'permission_mode': 'plan'}, 'conversation': conversation(messages)}
        parent = await instance.protocol.launch({'prompt': 'Inspect the change', 'cwd': str(tmp_path), 'context': bundle})
        child = await instance.protocol.launch({'prompt': 'Inspect another file', 'parent_id': parent['thread_id']})
        for index in (0, 1):
            assert instance.rpc.launched[index]['sandbox'] == 'read-only'
            assert 'Follow the project contract.' in instance.rpc.launched[index]['developerInstructions']
            assert 'Keep backwards compatibility.' in instance.rpc.launched[index]['developerInstructions']
            assert 'The shared decision is durable.' not in instance.rpc.launched[index]['developerInstructions']
            assert 'The shared decision is durable.' in json.dumps(instance.rpc.starts[index][1]['input'])
            assert 'Local agent gateway context:' in instance.rpc.starts[index][1]['input'][0]['text']
        assert child['inherited_context']['conversation_messages'] == 1
        with pytest.raises(ValueError, match='plan'):
            await instance.protocol.launch({'prompt': 'Write', 'cwd': str(tmp_path), 'sandbox': 'workspace-write', 'context': bundle})
    asyncio.run(check())


def test_portable_effort_preference_applies_without_overriding_explicit_choices(tmp_path):
    async def check():
        instance = service(tmp_path); instance.rpc = AgentRuntime()
        context = {'settings': {'effortLevel': 'low'}}
        inherited = await instance.protocol.launch({'prompt': 'Quick task', 'cwd': str(tmp_path), 'context': context})
        assert inherited['effort'] == 'low'
        explicit = await instance.protocol.launch({'prompt': 'Deep task', 'cwd': str(tmp_path), 'context': context, 'effort': 'high'})
        assert explicit['effort'] == 'high'
        maximum = await instance.protocol.launch({'prompt': 'Maximum supported effort', 'cwd': str(tmp_path), 'context': {'settings': {'effortLevel': 'max'}}})
        assert maximum['effort'] == 'high'
    asyncio.run(check())
