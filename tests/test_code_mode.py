import asyncio
import json

from codex_sessions.interfaces import tools
from codex_sessions.mcp import Bridge, INSTRUCTIONS


def test_read_tools_have_parallel_read_hints_without_mislabeling_agent_mutations():
    definitions = {tool['name']: tool for tool in tools()}
    for name in ('agents_list', 'agents_status', 'agents_attach', 'agents_wait', 'agents_result', 'agents_transcript', 'models_list', 'protocol_guide'):
        assert definitions[name]['annotations']['readOnlyHint'] is True
        assert definitions[name]['annotations']['idempotentHint'] is True
        assert definitions[name]['outputSchema']['type'] == 'object'
    for name in ('agents_launch', 'agents_send', 'agents_steer', 'agents_stop', 'agents_cancel', 'codex', 'codex-reply'):
        assert definitions[name]['annotations']['readOnlyHint'] is False
        assert definitions[name]['annotations']['idempotentHint'] is False
    for name in ('agents_status', 'agents_attach', 'agents_wait', 'agents_result'):
        properties = definitions[name]['inputSchema']['properties']
        assert properties['detail']['enum'] == ['summary', 'full']
        assert properties['fields']['type'] == 'array'
    for definition in tools(native=True):
        assert 'annotations' not in definition and 'outputSchema' not in definition


def test_children_preserves_its_legacy_array_without_advertising_an_object_schema():
    definition = next(tool for tool in tools() if tool['name'] == 'agents_children')
    assert definition['annotations']['readOnlyHint'] is True
    assert 'outputSchema' not in definition
    async def check():
        async def operation(method, params):
            assert method == 'agents.children' and params == {'thread_id': 'parent'}
            return [{'thread_id': 'child'}]
        result = (await Bridge(operation).handle({'id': 1, 'method': 'tools/call', 'params': {
            'name': 'agents_children', 'arguments': {'thread_id': 'parent'}}}))['result']
        assert json.loads(result['content'][0]['text']) == [{'thread_id': 'child'}]
        assert 'structuredContent' not in result
    asyncio.run(check())


def test_code_mode_batch_receives_independent_machine_readable_results():
    async def check():
        entered = set()
        both = asyncio.Event()
        async def operation(method, params):
            assert method == 'agents.status'
            assert params['fields'] == ['status', 'active_turn_id']
            entered.add(params['thread_id'])
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return {'thread_id': params['thread_id'], 'status': 'active', 'active_turn_id': f"turn-{params['thread_id']}"}
        bridge = Bridge(operation)
        replies = await asyncio.gather(*(bridge.handle({'jsonrpc': '2.0', 'id': index,
            'method': 'tools/call', 'params': {'name': 'agents_status', 'arguments': {
                'thread_id': thread_id, 'fields': ['status', 'active_turn_id']},
                '_meta': {'claudecode/toolUseId': f'code-call-{index}'}}}) for index, thread_id in enumerate(('one', 'two'), 1)))
        for index, response in enumerate(replies, 1):
            result = response['result']
            assert response['id'] == index and result['isError'] is False
            assert json.loads(result['content'][0]['text']) == result['structuredContent']
        assert [response['result']['structuredContent']['thread_id'] for response in replies] == ['one', 'two']
    asyncio.run(check())


def test_server_guidance_is_small_and_explains_code_mode_waiting():
    assert len(INSTRUCTIONS) < 3000
    assert 'Code Mode' in INSTRUCTIONS
    assert 'fields' in INSTRUCTIONS and 'agents_wait' in INSTRUCTIONS
    assert 'inherit_context' in INSTRUCTIONS


def test_code_mode_mcp_calls_keep_the_normal_inheritance_hook_contract(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    from test_claude_hook import caller, updated
    event, home, _ = caller(tmp_path)
    events = []
    for index in range(2):
        current = deepcopy(event)
        current.update(tool_name='mcp__codex__agents_launch', tool_use_id=f'code-call-{index}')
        current['tool_input']['prompt'] = f'Review independent component {index}'
        events.append(current)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda value: updated(value, tmp_path, home), events))
    for index, output in enumerate(outputs):
        assert output['prompt'] == f'Review independent component {index}'
        assert output['context']['caller']['session_id'] == event['session_id']
        assert output['context']['conversation']['message_count'] == 1
        assert output['context']['instructions']
