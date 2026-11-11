import asyncio
import json
from collections import Counter

import pytest
from starlette.testclient import TestClient

from codex_sessions.rpc import RpcError
from codex_sessions.server import create_app
from test_smoke import Runtime


class DetailsRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail = set()

    async def call(self, method, params=None):
        self.calls.append((method, params))
        if method in self.fail:
            raise RpcError('Do not disclose API_KEY=hidden-server-secret', -32601)
        if method == 'thread/read':
            return {'thread': {'id': 'a', 'cwd': '/project', 'model': 'recorded-model', 'modelProvider': 'openai',
                               'reasoningEffort': 'high', 'status': {'type': 'idle'}, 'source': 'appServer'}}
        if method == 'config/read':
            assert params == {'cwd': '/project', 'includeLayers': True}
            return {'config': {'model': 'current-default', 'model_reasoning_effort': 'low', 'env': {'API_KEY': 'hidden-env'},
                               'model_providers': {'private': {'api_key': 'hidden-provider'}}, 'sandbox_workspace_write':
                               {'writable_roots': ['/project'], 'network_access': False, 'secret': 'hidden-nested'}},
                    'layers': [{'name': {'type': 'user', 'file': '/codex/config.toml'}, 'version': '1',
                                'config': {'model': 'current-default', 'api_key': 'hidden-layer'}}]}
        if method == 'skills/list':
            return {'data': [{'cwd': '/project', 'errors': [], 'skills': [{'name': 'review', 'path': '/skills/review/SKILL.md',
                            'description': 'Review code', 'enabled': True, 'scope': 'user', 'api_key': 'hidden-skill'}]}]}
        if method == 'mcpServerStatus/list':
            assert params.get('threadId') == 'a'
            return {'data': [{'name': 'ado', 'authStatus': 'oAuth', 'runtimeStatus': 'connected',
                              'tools': {'query': {'name': 'query', 'description': 'Search', 'inputSchema': {'secret': 'hidden-schema'}}},
                              'resources': [], 'resourceTemplates': [], 'env': {'secret': 'hidden-mcp'}}], 'nextCursor': None}
        if method == 'plugin/list':
            return {'marketplaces': [{'name': 'local', 'plugins': [{'id': 'review@local', 'name': 'review', 'installed': True,
                                      'enabled': True, 'source': {'secret': 'hidden-source'}}]}]}
        if method == 'app/list':
            return {'data': [{'id': 'ado', 'name': 'Azure DevOps', 'isEnabled': True, 'isAccessible': True,
                              'installUrl': 'https://example.test/?token=hidden-url'}], 'nextCursor': None}
        return await super().call(method, params)


def instance(tmp_path, records=()):
    app = create_app(tmp_path, tmp_path / 'socket', tmp_path / 'data')
    service = app.state.service
    service.rpc = DetailsRuntime()
    folder = tmp_path / 'sessions'; folder.mkdir()
    rollout = folder / 'session.jsonl'
    rollout.write_text(''.join(json.dumps(record) + '\n' for record in records))
    service.history.sessions = lambda: [{'id': 'a', 'rollout_path': str(rollout), 'cwd': '/project', 'tokens_used': 77,
                                          'model': 'old-model', 'reasoning_effort': 'medium', 'approval_mode': 'on-request'}]
    return app


def test_details_keep_recorded_settings_separate_from_current_inventory_and_secrets(tmp_path):
    app = instance(tmp_path)
    with TestClient(app, base_url='http://localhost:8766') as client:
        response = client.get('/api/sessions/a/details')
        assert response.status_code == 200
        detail = response.json()
        assert detail['recorded']['settings']['model'] == 'recorded-model'
        assert detail['recorded']['settings']['effort'] == 'high'
        assert detail['current']['config']['settings']['model'] == 'current-default'
        assert detail['current']['scope'] == 'current_workspace_runtime'
        assert detail['current']['mcp']['scope'] == 'current_thread_runtime'
        assert detail['current']['skills']['items'][0]['name'] == 'review'
        assert detail['current']['mcp']['items'][0]['tools'][0]['name'] == 'query'
        assert detail['current']['plugins']['items'][0]['installed'] is True
        assert detail['current']['apps']['items'][0]['accessible'] is True
        assert 'hidden-' not in json.dumps(detail)
        assert all(method not in ('thread/resume', 'thread/start', 'turn/start') for method, _ in app.state.service.rpc.calls)
        first = Counter(method for method, _ in app.state.service.rpc.calls)
        assert client.get('/api/sessions/a/details').status_code == 200
        second = Counter(method for method, _ in app.state.service.rpc.calls)
        assert second['config/read'] == first['config/read']
        assert second['mcpServerStatus/list'] == first['mcpServerStatus/list']


def test_latest_turn_context_tokens_instructions_and_compaction_survive_offline_inventory(tmp_path):
    records = [
        {'type': 'session_meta', 'payload': {'id': 'a', 'parent_thread_id': 'parent', 'base_instructions': {'text': 'Base rules'}}},
        {'type': 'turn_context', 'payload': {'model': 'latest-model', 'effort': 'xhigh', 'cwd': '/project',
                                           'sandbox_policy': {'type': 'workspace-write', 'network_access': False}}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'developer',
                                            'content': [{'type': 'input_text', 'text': 'Developer rules API_KEY=hide-me'}]}},
        {'type': 'compacted', 'payload': {'message': 'Prior work summary'}},
        {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 1000},
                                        'last_token_usage': {'input_tokens': 500, 'output_tokens': 50, 'total_tokens': 550},
                                        'model_context_window': 128000}}},
    ]
    app = instance(tmp_path, records)
    app.state.service.rpc.fail = {'thread/read', 'plugin/list', 'mcpServerStatus/list'}
    with TestClient(app, base_url='http://localhost:8766') as client:
        detail = client.get('/api/sessions/a/details').json()
        assert detail['recorded']['settings']['model'] == 'latest-model'
        assert detail['recorded']['settings']['effort'] == 'xhigh'
        assert detail['recorded']['metadata']['parent_id'] == 'parent'
        assert detail['recorded']['context']['token_usage']['model_context_window'] == 128000
        assert detail['recorded']['context']['token_usage']['last']['total_tokens'] == 550
        assert detail['recorded']['context']['latest_summary'] == 'Prior work summary'
        assert len(detail['recorded']['instructions']) == 2
        assert detail['current']['plugins']['status'] == 'unavailable'
        assert detail['current']['skills']['status'] == 'available'
        assert 'hidden-server-secret' not in json.dumps(detail)
        assert 'hide-me' not in json.dumps(detail)


def test_unknown_session_details_are_not_found(tmp_path):
    app = instance(tmp_path)
    original = app.state.service.rpc.call
    async def call(method, params=None):
        if method == 'thread/read' and params['threadId'] == 'missing':
            raise RpcError('Thread not found', -32602)
        return await original(method, params)
    app.state.service.rpc.call = call
    with TestClient(app, base_url='http://localhost:8766') as client:
        assert client.get('/api/sessions/missing/details').status_code == 404


def test_refresh_reloads_current_inventory_and_retains_recorded_parse_cache(tmp_path):
    app = instance(tmp_path)
    service = app.state.service
    read = service.history.records
    calls = []
    def records(row):
        import threading
        calls.append(threading.get_ident())
        return read(row)
    service.history.records = records
    with TestClient(app, base_url='http://localhost:8766') as client:
        assert client.get('/api/sessions/a/details').status_code == 200
        assert client.get('/api/sessions/a/details?refresh=1').status_code == 200
        assert Counter(method for method, _ in service.rpc.calls)['config/read'] == 2
        assert len(calls) == 1


def test_gateway_context_latest_turn_precedence_and_live_token_notifications(tmp_path):
    app = instance(tmp_path, [{'type': 'turn_context', 'payload': {'model': 'later-model', 'effort': 'xhigh'}}])
    service = app.state.service
    service.rpc.fail = {'thread/read'}
    service.db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)',
                       ('a', 'parent', 'Task', 1, 2, 3, 'workspace-write', '/project', 'turn', 0,
                        json.dumps({'model': 'launch-model', 'effort': 'low', 'context': {
                            'provider': 'caller', 'instructions': [{'source': '/AGENTS.md', 'text': 'Follow rules'}],
                            'memories': [{'source': '/MEMORY.md', 'text': 'Saved decisions'}],
                            'skills': [{'name': 'review', 'path': '/review/SKILL.md', 'description': 'Review'}],
                            'session_summary': 'Inherited work summary'}})))
    service.notification({'method': 'thread/tokenUsage/updated', 'params': {'threadId': 'a', 'turnId': 'turn',
                          'tokenUsage': {'total': {'totalTokens': 40}, 'last': {'inputTokens': 30, 'totalTokens': 40},
                                         'modelContextWindow': 32000}}})
    detail = asyncio.run(service.details.get('a'))
    assert detail['recorded']['settings']['model'] == 'later-model'
    assert detail['recorded']['settings']['effort'] == 'xhigh'
    assert detail['recorded']['memories'][0]['text'] == 'Saved decisions'
    assert detail['recorded']['context']['session_summary'] == 'Inherited work summary'
    assert detail['recorded']['context']['token_usage']['last']['input_tokens'] == 30
    assert detail['recorded']['context']['token_usage']['model_context_window'] == 32000


def test_git_credentials_and_unknown_nested_settings_are_not_disclosed(tmp_path):
    app = instance(tmp_path)
    service = app.state.service
    original = service.rpc.call
    async def call(method, params=None):
        value = await original(method, params)
        if method == 'thread/read':
            value['thread']['gitInfo'] = {'sha': 'abcd', 'branch': 'main',
                                         'originUrl': 'https://user:hidden-password@example.test/repo.git?token=hidden-query'}
        return value
    service.rpc.call = call
    detail = asyncio.run(service.details.get('a'))
    assert detail['recorded']['metadata']['git']['commit_hash'] == 'abcd'
    assert detail['recorded']['metadata']['git']['repository_url'] == 'https://example.test/repo.git'
    assert 'hidden-' not in json.dumps(detail)


@pytest.mark.parametrize('text', ['Authorization: Bearer hidden-secret', '{"api_key":"hidden-secret"}',
                                  "'access_token': 'hidden-secret'", 'TOKEN=hidden-secret'])
def test_recorded_instruction_credentials_are_redacted(tmp_path, text):
    app = instance(tmp_path, [{'type': 'session_meta', 'payload': {'base_instructions': {'text': text}}}])
    detail = asyncio.run(app.state.service.details.get('a'))
    assert 'hidden-secret' not in json.dumps(detail)
    assert '[redacted]' in detail['recorded']['instructions'][0]['text']


def test_native_agents_instructions_and_actual_turn_settings_are_preserved(tmp_path):
    app = instance(tmp_path, [
        {'type': 'turn_context', 'payload': {'model': 'executed-model', 'effort': 'xhigh'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': '# AGENTS.md instructions\n<INSTRUCTIONS>Project rules</INSTRUCTIONS>'}]}},
    ])
    detail = asyncio.run(app.state.service.details.get('a'))
    assert detail['recorded']['settings']['model'] == 'executed-model'
    assert detail['recorded']['settings']['effort'] == 'xhigh'
    assert 'Project rules' in detail['recorded']['instructions'][0]['text']
    assert detail['recorded']['configured_settings']['model'] == 'recorded-model'


def test_plugin_inventory_omits_uninstalled_catalog_entries(tmp_path):
    app = instance(tmp_path)
    original = app.state.service.rpc.call
    async def call(method, params=None):
        value = await original(method, params)
        if method == 'plugin/list':
            value['marketplaces'][0]['plugins'].append({'name': 'catalog-only', 'id': 'catalog', 'installed': False, 'enabled': False})
        return value
    app.state.service.rpc.call = call
    detail = asyncio.run(app.state.service.details.get('a'))
    assert len(detail['current']['plugins']['items']) == 1
    assert detail['current']['plugins']['catalog_count'] == 2


def test_unavailable_mcp_runtime_shows_configured_names_without_secrets(tmp_path):
    app = instance(tmp_path)
    service = app.state.service
    service.rpc.fail = {'mcpServerStatus/list'}
    original = service.rpc.call
    async def call(method, params=None):
        result = await original(method, params)
        if method == 'config/read':
            result['config']['mcp_servers'] = {'ado': {'enabled': True, 'env': {'TOKEN': 'hidden-environment'},
                                                      'url': 'https://example.test/?token=hidden-endpoint'}}
        return result
    service.rpc.call = call
    detail = asyncio.run(service.details.get('a'))
    mcp = detail['current']['mcp']
    assert mcp['status'] == 'available' and mcp['partial'] is True
    assert mcp['scope'] == 'current_workspace_runtime'
    assert mcp['items'][0]['name'] == 'ado'
    assert mcp['items'][0]['runtime_status'] == 'unknown'
    assert 'hidden-' not in json.dumps(detail)


@pytest.mark.parametrize('history_mode,records,expected', [
    ('paginated', [{'type': 'compacted', 'payload': {'message': 'Recorded summary'}}], None),
    ('legacy', [], None),
    ('legacy', [{'type': 'session_meta', 'payload': {'id': 'a'}}], 0),
])
def test_compaction_count_is_unknown_without_complete_recorded_history(tmp_path, history_mode, records, expected):
    app = instance(tmp_path, records)
    service = app.state.service
    rows = service.history.sessions()
    rows[0]['history_mode'] = history_mode
    service.history.sessions = lambda: rows
    detail = asyncio.run(service.details.get('a'))
    assert detail['recorded']['context']['compactions'] == expected


@pytest.mark.parametrize('section,expected', [
    ('recorded', set()),
    ('context', {'config/read', 'skills/list'}),
    ('config', {'config/read'}),
    ('skills', {'skills/list'}),
    ('mcp', {'mcpServerStatus/list'}),
    ('plugins', {'plugin/list'}),
    ('apps', {'app/list'}),
    ('integrations', {'config/read', 'mcpServerStatus/list', 'plugin/list', 'app/list'}),
])
def test_detail_sections_request_only_their_runtime_inventories(tmp_path, section, expected):
    service = instance(tmp_path).state.service
    detail = asyncio.run(service.details.get('a', section=section))
    assert {method for method, _ in service.rpc.calls} == expected
    for name, method in [('config', 'config/read'), ('skills', 'skills/list'), ('mcp', 'mcpServerStatus/list'),
                         ('plugins', 'plugin/list'), ('apps', 'app/list')]:
        inventory = detail['current'][name]
        assert inventory['status'] == ('available' if method in expected else 'not_requested')
        assert inventory['source'] == method
        assert inventory['scope'] == ('current_thread_runtime' if name in ('mcp', 'apps') else 'current_workspace_runtime')
    assert detail['recorded']['settings']['model'] == 'old-model'


def test_saved_details_and_context_finish_while_apps_are_pending(tmp_path):
    service = instance(tmp_path).state.service
    original = service.rpc.call

    async def check():
        started, release = asyncio.Event(), asyncio.Event()
        async def call(method, params=None):
            if method == 'app/list':
                started.set()
                await release.wait()
            return await original(method, params)
        service.rpc.call = call
        integrations = asyncio.create_task(service.details.get('a', section='integrations'))
        await asyncio.wait_for(started.wait(), .5)
        try:
            recorded = await asyncio.wait_for(service.details.get('a', section='recorded'), .5)
            context = await asyncio.wait_for(service.details.get('a', section='context'), .5)
            mcp = await asyncio.wait_for(service.details.get('a', section='mcp'), .5)
            assert recorded['recorded']['settings']['model'] == 'old-model'
            assert context['current']['skills']['status'] == 'available'
            assert mcp['current']['mcp']['items'][0]['name'] == 'ado'
            assert not integrations.done()
        finally:
            release.set()
            await integrations
    asyncio.run(check())


def test_cancelled_inventory_waiter_keeps_shared_load_registered(tmp_path):
    service = instance(tmp_path).state.service
    original = service.rpc.call

    async def check():
        started, release = asyncio.Event(), asyncio.Event()
        async def call(method, params=None):
            started.set()
            await release.wait()
            return await original(method, params)
        service.rpc.call = call
        first = asyncio.create_task(service.details.inventory('apps', '/project', 'a'))
        await asyncio.wait_for(started.wait(), .5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.details.inventory('apps', '/project', 'a', refresh=True))
        await asyncio.sleep(0)
        release.set()
        assert (await second)['status'] == 'available'
        assert Counter(method for method, _ in service.rpc.calls)['app/list'] == 1
        assert not service.details.pending
    asyncio.run(check())


def test_detail_route_validates_sections_and_resolves_unknown_sessions(tmp_path):
    app = instance(tmp_path)
    service = app.state.service
    original = service.rpc.call
    async def call(method, params=None):
        if method == 'thread/read' and params['threadId'] == 'missing':
            raise RpcError('Thread not found', -32602)
        return await original(method, params)
    service.rpc.call = call
    with TestClient(app, base_url='http://localhost:8766') as client:
        assert client.get('/api/sessions/a/details?section=invalid').status_code == 400
        assert client.get('/api/sessions/missing/details?section=recorded').status_code == 404
        response = client.get('/api/sessions/runtime/details?section=recorded')
        assert response.status_code == 200
        assert response.json()['current']['apps']['status'] == 'not_requested'
        assert client.get('/api/sessions/a/details?section=recorded').status_code == 200
    assert {method for method, _ in service.rpc.calls} <= {'thread/read', 'thread/list', 'thread/loaded/list'}


def test_recorded_gateway_evidence_preserves_safe_launch_metadata(tmp_path):
    service = instance(tmp_path).state.service
    artifact = service.protocol.artifacts.put({'name': 'notes.txt', 'data': 'aW5wdXQ='})
    service.db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)',
                       ('a', 'parent', 'Task', 1, 2, 3, 'workspace-write', '/project', 'turn', 0,
                        json.dumps({'prompt': 'Investigate API_KEY=hidden-prompt', 'attachments': [artifact['id']],
                                    'context': {'instructions': [{'source': '/rules.md', 'text': 'Rules',
                                        'condition': {'base': '/project', 'paths_frontmatter': 'paths: [src/**]'},
                                        'imported_from': '/AGENTS.md', 'secret': 'hidden-rule'}]}})))
    service.db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)',
                       ('child', 'a', 'Child task', 2, 1, 3, 'read-only', '/project', None, 0, '{}'))
    detail = asyncio.run(service.details.get('a', section='recorded'))
    recorded = detail['recorded']
    assert recorded['task'] == 'Investigate API_KEY=[redacted]'
    assert recorded['delegation']['max_depth'] == 2
    assert recorded['delegation']['max_children'] == 3
    assert recorded['delegation']['depth'] == 1
    assert recorded['delegation']['parent_id'] == 'parent'
    assert recorded['delegation']['children'][0]['thread_id'] == 'child'
    assert recorded['instructions'][0]['condition'] == {'base': '/project', 'paths_frontmatter': 'paths: [src/**]'}
    assert recorded['instructions'][0]['imported_from'] == '/AGENTS.md'
    assert recorded['attachments'] == [{key: artifact[key] for key in ('id', 'name', 'mime', 'size', 'kind')}]
    assert 'hidden-' not in json.dumps(detail)
    assert not service.rpc.calls


def test_known_thread_metadata_validates_saved_inspection_without_runtime_calls(tmp_path):
    service = instance(tmp_path).state.service
    service.history.sessions = lambda: []
    service.known_threads['known'] = {'id': 'known', 'cwd': '/known', 'model': 'known-model'}
    detail = asyncio.run(service.details.get('known', section='recorded'))
    assert detail['recorded']['configured_settings']['model'] == 'known-model'
    assert detail['current']['cwd'] == '/known'
    assert not service.rpc.calls


def test_native_parent_details_include_gateway_children_without_launch_record(tmp_path):
    service = instance(tmp_path).state.service
    service.db.execute('INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,0)',
                       ('child', 'a', 'Child task', 1, 2, 3, 'read-only', '/project', None, 0, '{}'))
    detail = asyncio.run(service.details.get('a', section='recorded'))
    assert detail['recorded']['delegation']['children'][0]['thread_id'] == 'child'
    assert not service.rpc.calls


def test_recorded_refresh_updates_thread_metadata_without_fetching_inventories(tmp_path):
    app = instance(tmp_path)
    service = app.state.service
    service.known_threads['a'] = {'id': 'a', 'model': 'discovered-model', 'cwd': '/project'}
    async def check():
        fast = await service.details.get('a', section='recorded')
        assert fast['recorded']['settings']['model'] == 'discovered-model'
        assert fast['recorded']['sources']['settings.model'] == 'latest discovered session metadata'
        assert not service.rpc.calls
        refreshed = await service.details.get('a', section='recorded', refresh=True)
        assert refreshed['recorded']['settings']['model'] == 'recorded-model'
        assert [method for method, _ in service.rpc.calls] == ['thread/read']
    asyncio.run(check())


def test_ready_mcp_inventory_does_not_wait_for_slow_workspace_config(tmp_path):
    service = instance(tmp_path).state.service
    original = service.rpc.call
    async def check():
        started, release = asyncio.Event(), asyncio.Event()
        async def call(method, params=None):
            if method == 'config/read':
                started.set()
                await release.wait()
            return await original(method, params)
        service.rpc.call = call
        config = asyncio.create_task(service.details.get('a', section='config'))
        await asyncio.wait_for(started.wait(), .5)
        try:
            result = await asyncio.wait_for(service.details.get('a', section='mcp'), .5)
            assert result['current']['mcp']['items'][0]['name'] == 'ado'
            assert result['current']['config']['status'] == 'not_requested'
            assert not config.done()
        finally:
            release.set()
            await config
    asyncio.run(check())
