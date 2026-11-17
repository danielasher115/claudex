import asyncio
import copy

import pytest

from codex_sessions.rpc import RpcError
from test_protocol import AgentRuntime
from test_smoke import message, service


class WorkerRuntime(AgentRuntime):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.calls = []

    async def call(self, method, params=None):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'config/read':
            return {'config': self.config}
        return await super().call(method, params)


def test_launch_disables_only_gateway_mcp_and_uses_native_subagents(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({'mcp_servers': {
            'codex': {'command': 'bridge', 'args': ['mcp']},
            'claudex': {'url': 'http://localhost/bridge'},
            'alias': {'command': '/bin/claudex', 'args': ['mcp']},
            'bridge.alias': {'command': 'claudex', 'args': ['mcp']},
            'python_alias': {'command': 'python', 'args': ['-m', 'codex_sessions', 'mcp']},
            'wsl_alias': {'command': 'wsl.exe', 'args': ['--distribution', 'Ubuntu', '--exec', '/home/user/.local/bin/claudex', 'mcp']},
            'uv_alias': {'command': 'uv', 'args': ['run', 'claudex', 'mcp']},
            'nested_wrapper': {'command': 'wsl.exe', 'args': ['-d', 'Ubuntu', '--exec', 'uv', 'run', 'python', '-m', 'codex_sessions', 'mcp']},
            'unrelated_wrapper': {'command': 'uv', 'args': ['run', 'python', '-m', 'other_server', 'mcp']},
            'unrelated_wrapper_arg': {'command': 'wsl.exe', 'args': ['--exec', 'other_server', '--label', 'claudex', 'mcp']},
            'windows_alias': {'command': r'C:\Tools\codex-sessions.exe', 'args': ['mcp']},
            'github': {'command': 'github-mcp-server', 'args': ['stdio']},
            'native_codex': {'command': 'codex', 'args': ['mcp-server']},
            'unrelated_module_hint': {'command': 'node', 'args': ['-m', 'codex_sessions', 'mcp']},
            'similarly_named': {'command': 'claudex-helper', 'args': ['mcp']},
        }})
        before = copy.deepcopy(instance.rpc.config)
        context = {'provider': 'claude', 'instructions': [{'source': 'caller', 'text': 'Keep caller rules'}],
                   'skills': [{'name': 'codex-agents', 'path': '/caller/codex-agents/SKILL.md'},
                              {'name': 'review', 'path': '/caller/review/SKILL.md'}]}
        await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path), 'context': context,
                                        'developer-instructions': 'Keep explicit developer instructions',
                                        'config': {'model_verbosity': 'low'}})
        params = instance.rpc.launched[0]
        assert not params.get('dynamicTools')
        config = params['config']
        for name in ('codex', 'claudex', 'alias', 'bridge.alias', 'python_alias', 'windows_alias', 'wsl_alias', 'uv_alias', 'nested_wrapper'):
            assert config['mcp_servers'][name]['enabled'] is False
        assert not any(name in config['mcp_servers'] for name in ('github', 'native_codex', 'unrelated_module_hint', 'similarly_named', 'unrelated_wrapper', 'unrelated_wrapper_arg'))
        assert config['model_verbosity'] == 'low'
        assert instance.rpc.config == before
        instructions = params['developerInstructions']
        assert 'spawn_agent' in instructions and 'send_input' in instructions and 'wait' in instructions and 'close_agent' in instructions
        assert 'Keep explicit developer instructions' in instructions and 'Keep caller rules' in instructions
        assert '/caller/codex-agents/SKILL.md' in instructions and '/caller/review/SKILL.md' in instructions
        assert 'agents_launch for delegation' not in instructions
        assert 'Prefer these tools for delegation' not in str(instance.rpc.starts)
        assert ('config/read', {'cwd': str(tmp_path), 'includeLayers': False}) in instance.rpc.calls
    asyncio.run(check())


def test_worker_overrides_cannot_reenable_or_rename_gateway_mcp(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({'mcp_servers': {'codex': {'command': 'claudex', 'args': ['mcp']}}})
        overrides = {'mcp_servers.codex.enabled': True,
                     'mcp_servers.alias.command': 'claudex', 'mcp_servers.alias.args': ['mcp'],
                     'mcp_servers': {'nested_alias': {'command': 'python', 'args': ['-m', 'codex_sessions', 'mcp']},
                                     'github': {'enabled': True}}}
        before = copy.deepcopy(overrides)
        await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path), 'inherit_context': False, 'config': overrides})
        config = instance.rpc.launched[0]['config']
        assert config['mcp_servers']['codex']['enabled'] is False
        assert config['mcp_servers']['alias']['enabled'] is False
        assert config['mcp_servers']['nested_alias']['enabled'] is False
        assert config['mcp_servers']['github']['enabled'] is True
        assert overrides == before
    asyncio.run(check())


def test_owned_idle_resume_reapplies_current_mcp_policy_and_keeps_handoff(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({'mcp_servers': {}})
        launched = await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path), 'inherit_context': False,
                                                  'developer-instructions': 'Keep this instruction',
                                                  'context': {'session_summary': 'Keep this handoff'},
                                                  'config': {'model_verbosity': 'low'}})
        sid = launched['thread_id']
        instance.rpc.sessions[sid] = 'idle'
        instance.rpc.config = {'mcp_servers': {'new_alias': {'command': 'claudex', 'args': ['mcp']}}}
        sent = await instance.send(sid, message())
        queued = instance.db.execute('SELECT * FROM messages WHERE id=?', (sent['id'],)).fetchone()
        await instance.start_queued(queued)
        params = next(params for method, params in instance.rpc.calls if method == 'thread/resume')
        assert params['config']['mcp_servers']['new_alias']['enabled'] is False
        assert params['config']['model_verbosity'] == 'low'
        assert 'spawn_agent' in params['developerInstructions']
        assert 'Keep this instruction' in params['developerInstructions'] and 'Keep this handoff' in params['developerInstructions']
        assert 'dynamicTools' not in params
        assert instance.db.execute('SELECT status FROM messages WHERE id=?', (sent['id'],)).fetchone()[0] == 'delivered'
    asyncio.run(check())


def test_unowned_resume_preserves_native_configuration(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({'mcp_servers': {'codex': {'enabled': True}}})
        sent = await instance.send('a', message())
        await instance.start_queued(instance.db.execute('SELECT * FROM messages WHERE id=?', (sent['id'],)).fetchone())
        assert not any(method == 'config/read' for method, _ in instance.rpc.calls)
        params = next(params for method, params in instance.rpc.calls if method == 'thread/resume')
        assert params == {'threadId': 'a', 'excludeTurns': True, 'config': {'thread_unload_delay_secs': 1}}
    asyncio.run(check())


def test_mcp_policy_read_failure_does_not_start_unisolated_worker(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({})
        original = instance.rpc.call
        async def call(method, params=None):
            if method == 'config/read':
                raise RpcError('Config unavailable', -32000)
            return await original(method, params)
        instance.rpc.call = call
        with pytest.raises(RpcError, match='Config unavailable'):
            await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path), 'inherit_context': False})
        assert not instance.rpc.launched
    asyncio.run(check())


def test_legacy_dynamic_delegation_returns_native_guidance_without_launching(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({})
        worker = await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path), 'inherit_context': False})
        reply = await instance.protocol.native_call({'method': 'item/tool/call', 'params': {
            'threadId': worker['thread_id'], 'tool': 'agents_launch', 'arguments': {'prompt': 'Child', 'inherit_context': False}}})
        assert reply['success'] is False
        assert 'spawn_agent' in reply['contentItems'][0]['text']
        assert len(instance.rpc.launched) == 1
    asyncio.run(check())


def test_worker_preserves_native_config_instructions_with_inherited_context(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({'developer_instructions': 'Native configured instructions', 'mcp_servers': {}})
        await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path),
                                        'context': {'session_summary': 'Inherited handoff'}})
        instructions = instance.rpc.launched[0]['developerInstructions']
        assert 'Native configured instructions' in instructions and 'Inherited handoff' in instructions
        assert 'spawn_agent' in instructions
    asyncio.run(check())


def test_owned_resume_policy_failure_preserves_queued_message(tmp_path):
    async def check():
        instance = service(tmp_path)
        instance.rpc = WorkerRuntime({})
        worker = await instance.protocol.launch({'prompt': 'Review', 'cwd': str(tmp_path), 'inherit_context': False})
        sid = worker['thread_id']
        instance.rpc.sessions[sid] = 'idle'
        sent = await instance.send(sid, message())
        original = instance.rpc.call
        async def call(method, params=None):
            if method == 'config/read':
                raise RpcError('Config unavailable')
            return await original(method, params)
        instance.rpc.call = call
        with pytest.raises(RpcError, match='Config unavailable'):
            await instance.start_queued(instance.db.execute('SELECT * FROM messages WHERE id=?', (sent['id'],)).fetchone())
        assert instance.db.execute('SELECT status FROM messages WHERE id=?', (sent['id'],)).fetchone()[0] == 'queued'
        assert not any(method == 'thread/resume' for method, _ in instance.rpc.calls)
        assert len(instance.rpc.starts) == 1
    asyncio.run(check())
