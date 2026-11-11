"""Read-only session evidence and explicitly separate current runtime inventories."""
import asyncio
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit
from collections import OrderedDict

from .rpc import RpcError
from .context import report as context_report

CONFIG_FIELDS = {
    'model', 'model_provider', 'model_reasoning_effort', 'model_reasoning_summary', 'model_verbosity',
    'model_context_window', 'model_auto_compact_token_limit', 'model_auto_compact_token_limit_scope',
    'approval_policy', 'approvals_reviewer', 'sandbox_mode', 'service_tier', 'web_search', 'review_model',
}
NESTED_FIELDS = {
    'type', 'kind', 'mode', 'settings', 'model', 'reasoning_effort', 'developer_instructions', 'granular',
    'mcp_elicitations', 'request_permissions', 'rules', 'sandbox_approval', 'skill_approval', 'writable_roots',
    'network_access', 'exclude_tmpdir_env_var', 'exclude_slash_tmp', 'file_system', 'network', 'entries',
    'path', 'access', 'name', 'id', 'domain', 'key', 'file', 'profile', 'dotCodexFolder', 'text', 'enabled',
    'subagent', 'subAgent', 'custom', 'other', 'thread_spawn', 'parent_thread_id', 'depth', 'agent_nickname',
    'agent_role', 'status', 'value', 'base', 'paths_frontmatter',
}
SECRET = re.compile(r'(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|bearer[_-]?token|authorization|password|secret|credential)')
ASSIGNMENT = re.compile(r'''(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|bearer[_-]?token|token|authorization|password|secret|credential)["']?\s*[=:]\s*)(?:(?:Bearer|Basic)\s+)?(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''')
AUTH_HEADER = re.compile(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+')

TOKENS = {'input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens'}
INVENTORIES = {'config': 'config/read', 'skills': 'skills/list', 'mcp': 'mcpServerStatus/list',
               'plugins': 'plugin/list', 'apps': 'app/list'}
SECTIONS = {'recorded': (), 'context': ('config', 'skills'), 'integrations': ('config', 'mcp', 'plugins', 'apps'),
            'all': tuple(INVENTORIES), **{name: (name,) for name in INVENTORIES}}


def inventory_section(name, status):
    return {'status': status, 'source': INVENTORIES[name],
            'scope': 'current_thread_runtime' if name in ('mcp', 'apps') else 'current_workspace_runtime'}


def clean(value):
    if isinstance(value, str):
        return AUTH_HEADER.sub(r'\1 [redacted]', ASSIGNMENT.sub(r'\1[redacted]', value))
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: '[redacted]' if SECRET.search(key) else clean(item) for key, item in value.items() if key in NESTED_FIELDS}
    return value if value is None or isinstance(value, (int, float, bool)) else None


def pick(value, fields):
    return {key: clean(value[key]) for key in fields if key in value and value[key] is not None}


def config_settings(config):
    return {**pick(config, CONFIG_FIELDS), **({'sandbox_workspace_write': clean(config['sandbox_workspace_write'])}
            if isinstance(config.get('sandbox_workspace_write'), dict) else {})}


def repository_url(value):
    if not isinstance(value, str):
        return None
    if '://' not in value:
        return value.split('?', 1)[0].split('#', 1)[0]
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if parsed.port:
            host += ':' + str(parsed.port)
        return urlunsplit((parsed.scheme, host, parsed.path, '', ''))
    except ValueError:
        return '[redacted]'


class SessionDetails:
    def __init__(self, service):
        self.service = service
        self.cache = {}
        self.pending = {}
        self.token_usage = {}
        self.recorded_cache = OrderedDict()

    def observe(self, message):
        if message.get('method') == 'thread/tokenUsage/updated':
            params = message.get('params') or {}
            if params.get('threadId') and isinstance(params.get('tokenUsage'), dict):
                self.token_usage[params['threadId']] = params['tokenUsage']

    async def request(self, method, params):
        return await asyncio.wait_for(self.service.rpc.call(method, params), timeout=12 if method == 'app/list' else 6)

    async def inventory(self, name, cwd, session_id, *, refresh=False):
        key = (name, cwd, session_id if name in ('mcp', 'apps') else None)
        if refresh:
            self.cache.pop(key, None)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 45:
            return cached[1]
        if key in self.pending:
            return await asyncio.shield(self.pending[key])
        async def load():
            section = inventory_section(name, 'available')
            try:
                params = {
                    'config': {'cwd': cwd, 'includeLayers': True},
                    'skills': {'cwds': [cwd], 'forceReload': False},
                    'mcp': {'threadId': session_id, 'limit': 100},
                    'plugins': {'cwds': [cwd], 'forceRefetch': False},
                    'apps': {'threadId': session_id, 'limit': 100, 'forceRefetch': False},
                }[name]
                result = await self.request(INVENTORIES[name], params)
                data = list(result.get('data', []))
                cursor = result.get('nextCursor')
                seen = set()
                while cursor and name in ('mcp', 'apps'):
                    if cursor in seen:
                        raise ValueError('Repeated inventory cursor')
                    seen.add(cursor)
                    result = await self.request(INVENTORIES[name], {**params, 'cursor': cursor})
                    data.extend(result.get('data', []))
                    cursor = result.get('nextCursor')
                if name == 'config':
                    config = result.get('config', {})
                    section['settings'] = config_settings(config)
                    section['mcp_servers'] = [{'name': clean(name), 'enabled': server.get('enabled', True)}
                                              for name, server in (config.get('mcp_servers') or {}).items() if isinstance(server, dict)]
                    section['layers'] = [{'name': clean(layer.get('name')), 'version': layer.get('version'),
                                          'disabled_reason': clean(layer.get('disabledReason')),
                                          'settings': config_settings(layer.get('config') or {})} for layer in result.get('layers') or []]
                elif name == 'skills':
                    section['items'] = [{**pick(skill, {'name', 'description', 'path', 'enabled', 'scope'}),
                                         **({'plugin_id': skill['pluginId']} if skill.get('pluginId') else {})}
                                        for entry in data for skill in entry.get('skills', [])]
                    section['errors'] = [pick(error, {'path', 'message'}) for entry in data for error in entry.get('errors', [])]
                elif name == 'mcp':
                    section['items'] = [{**pick(server, {'name'}), 'auth_status': server.get('authStatus'),
                        'runtime_status': clean(server.get('runtimeStatus')), 'plugin_id': server.get('pluginId'),
                        'http_origin': repository_url(server.get('httpOrigin')), 'tools_error': bool(server.get('toolsError')),
                        'tools': [{**pick(tool, {'name', 'title', 'description'}), 'name': tool.get('name', key)}
                                  for key, tool in (server.get('tools') or {}).items()],
                        'resource_count': len(server.get('resources') or []),
                        'resource_template_count': len(server.get('resourceTemplates') or [])} for server in data]
                elif name == 'plugins':
                    section['items'] = [{**pick(plugin, {'id', 'name', 'enabled', 'installed', 'version', 'availability'}),
                                         'local_version': plugin.get('localVersion'), 'marketplace': clean(market.get('name'))}
                                        for market in result.get('marketplaces', []) for plugin in market.get('plugins', [])
                                        if plugin.get('installed') or plugin.get('enabled')]
                    section['catalog_count'] = sum(len(market.get('plugins', [])) for market in result.get('marketplaces', []))
                    section['errors'] = [{'message': 'A plugin marketplace could not be loaded'} for _ in result.get('marketplaceLoadErrors', [])]
                elif name == 'apps':
                    section['items'] = [{**pick(app, {'id', 'name', 'description'}), 'enabled': app.get('isEnabled'),
                                         'accessible': app.get('isAccessible'), 'plugin_names': clean(app.get('pluginDisplayNames', []))}
                                        for app in data if app.get('isAccessible') or app.get('pluginDisplayNames')]
                    section['catalog_count'] = len(data)
            except (RpcError, OSError, TimeoutError, ValueError, TypeError, KeyError):
                section.update(status='unavailable', message='This inventory is unavailable from the connected Codex runtime.', items=[])
            self.cache[key] = (time.monotonic(), section)
            return section
        task = self.pending[key] = asyncio.create_task(load())
        task.add_done_callback(lambda _: self.pending.pop(key, None))
        return await asyncio.shield(task)

    async def get(self, session_id, *, refresh=False, section='all'):
        if section not in SECTIONS:
            raise ValueError('Unknown details section')
        service = self.service
        rows = await asyncio.to_thread(service.history.sessions)
        row = next((row for row in rows if row['id'] == session_id), {})
        agent = service.protocol.agent(session_id)
        thread = service.known_threads.get(session_id, {})
        thread_source = 'latest discovered session metadata'
        warnings = []
        if section == 'all' or refresh and section == 'recorded' or not (row or agent or thread):
            thread_source = 'thread/read configured session settings'
            try:
                thread = (await self.request('thread/read', {'threadId': session_id, 'includeTurns': False})).get('thread', {})
            except (RpcError, OSError, TimeoutError):
                warnings.append('Live thread metadata is unavailable; recorded session evidence is shown where present.')
        if not row and not agent and not thread:
            raise KeyError('Session not found')
        signature = (service.history.revision(row), json.dumps(row, sort_keys=True),
                     json.dumps(thread, sort_keys=True), thread_source, json.dumps(agent, sort_keys=True),
                     json.dumps(self.token_usage.get(session_id), sort_keys=True))
        cached = self.recorded_cache.get(session_id)
        if cached and cached[0] == signature:
            recorded, cwd, recorded_warnings = cached[1]
        else:
            recorded, cwd, recorded_warnings = await asyncio.to_thread(self.recorded, session_id, row, agent, thread, thread_source)
            self.recorded_cache[session_id] = (signature, (recorded, cwd, recorded_warnings))
            self.recorded_cache.move_to_end(session_id)
            while len(self.recorded_cache) > 32:
                self.recorded_cache.popitem(last=False)
        warnings.extend(recorded_warnings)
        names = SECTIONS[section]
        inventories = await asyncio.gather(*(self.inventory(name, cwd, session_id, refresh=refresh) for name in names))
        current = {name: inventory_section(name, 'not_requested') for name in INVENTORIES}
        current.update(zip(names, inventories))
        if section == 'mcp' and current['mcp']['status'] == 'unavailable':
            current['config'] = await self.inventory('config', cwd, session_id, refresh=refresh)
        if current['mcp']['status'] == 'unavailable' and current['config'].get('mcp_servers'):
            current['mcp'] = {'status': 'available', 'partial': True, 'source': 'config/read configured MCP names',
                'scope': 'current_workspace_runtime',
                'message': 'Configured servers only. Connection status and tools could not be read from the runtime.',
                'items': [{**server, 'runtime_status': 'unknown', 'tools': []} for server in current['config']['mcp_servers']]}
        children = service.protocol.children(session_id)
        if agent or children:
            recorded = {**recorded, 'delegation': {**recorded.get('delegation', {}),
                'children': [pick(child, {'thread_id', 'parent_id', 'name', 'depth', 'cwd', 'stopped'}) for child in children]},
                'sources': {**recorded['sources'], 'delegation': 'gateway agent database'}}
        return {'session_id': session_id, 'recorded': recorded,
                'current': {'scope': 'current_workspace_runtime', 'cwd': cwd, **current}, 'warnings': warnings}

    def recorded(self, session_id, row, agent, thread, thread_source='thread/read configured session settings'):
        service = self.service
        warnings = []
        settings, sources = {}, {}
        def apply(values, mapping, source):
            for original, target in mapping.items():
                if values.get(original) is not None:
                    settings[target] = clean(values[original])
                    sources['settings.' + target] = source
        apply(row, {'cwd': 'cwd', 'model': 'model', 'reasoning_effort': 'effort', 'model_provider': 'model_provider',
                    'approval_mode': 'approval_policy'}, 'recorded state database')
        sandbox = row.get('sandbox_policy')
        if isinstance(sandbox, str):
            try:
                sandbox = json.loads(sandbox)
            except ValueError:
                pass
        if sandbox is not None:
            settings['sandbox'] = clean(sandbox)
            sources['settings.sandbox'] = 'recorded state database'
        request = {}
        if agent:
            try:
                request = json.loads(agent['request_json'])
            except (ValueError, TypeError):
                pass
            apply(request, {'model': 'model', 'effort': 'effort', 'sandbox': 'sandbox', 'approval-policy': 'approval_policy'}, 'gateway launch request')
        inherited = request.get('context') or {}
        instructions = [pick(item, {'source', 'text', 'condition', 'imported_from'}) for item in inherited.get('instructions', [])]
        memories = [pick(item, {'source', 'text', 'condition', 'imported_from'}) for item in inherited.get('memories', [])]
        skills = [pick(item, {'name', 'path', 'description', 'enabled', 'scope'}) for item in inherited.get('skills', [])]
        context = {'tokens_used': row.get('tokens_used'), 'compactions': None}
        inheritance = {key: clean(value) for key, value in context_report(inherited).items()}
        caller_conversation = pick(inherited.get('conversation') or {}, {'session_id', 'agent_id', 'agent_type', 'cwd',
            'transcript_path', 'permission_mode', 'captured_at', 'message_count', 'content_sha256', 'omitted_binary_blocks'})
        if (inherited.get('conversation') or {}).get('snapshot'):
            caller_conversation['snapshot'] = pick(inherited['conversation']['snapshot'], {'path', 'sha256', 'bytes'})
        if inherited.get('provider'):
            context['inherited_provider'] = inherited['provider']
        if inherited.get('session_summary'):
            context['session_summary'] = clean(inherited['session_summary'])
        metadata = {**pick(row, {'id', 'title', 'originator', 'source', 'cli_version', 'created_at', 'updated_at',
                               'archived', 'history_mode', 'memory_mode', 'agent_nickname', 'agent_role'}), 'id': session_id}
        git = {key: row[field] for field, key in [('git_branch', 'branch'), ('git_sha', 'commit_hash')] if row.get(field)}
        if row.get('git_origin_url'):
            git['repository_url'] = repository_url(row['git_origin_url'])
        if agent:
            metadata['parent_id'] = agent['parent_id']
        token_usage = None
        try:
            records = service.history.records(row)
        except (OSError, ValueError):
            records = []
            warnings.append('The recorded rollout could not be read.')
        if records and row.get('history_mode') != 'paginated':
            context['compactions'] = 0
        for index, record in enumerate(records):
            payload = record.get('payload') or {}
            kind = record.get('type')
            if kind == 'session_meta':
                metadata.update(pick(payload, {'originator', 'source', 'cli_version', 'history_mode', 'agent_nickname', 'agent_role'}))
                if payload.get('parent_thread_id'):
                    metadata['parent_id'] = payload['parent_thread_id']
                apply(payload, {'model_provider': 'model_provider', 'cwd': 'cwd'}, 'recorded session metadata')
                base = payload.get('base_instructions')
                if isinstance(base, dict):
                    base = base.get('text')
                if isinstance(base, str):
                    instructions.append({'source': 'Recorded base instructions', 'text': clean(base)})
                if isinstance(payload.get('git'), dict):
                    git.update(pick(payload['git'], {'branch', 'commit_hash'}))
                    if payload['git'].get('repository_url'):
                        git['repository_url'] = repository_url(payload['git']['repository_url'])
            elif kind == 'turn_context':
                apply(payload, {'cwd': 'cwd', 'model': 'model', 'effort': 'effort', 'approval_policy': 'approval_policy',
                    'approvals_reviewer': 'approvals_reviewer', 'sandbox_policy': 'sandbox', 'permission_profile': 'permission_profile',
                    'file_system_sandbox_policy': 'file_system_sandbox_policy', 'personality': 'personality', 'service_tier': 'service_tier',
                    'collaboration_mode': 'collaboration_mode', 'workspace_roots': 'workspace_roots', 'timezone': 'timezone',
                    'current_date': 'current_date', 'disabled_plugin_ids': 'disabled_plugin_ids'}, 'latest recorded turn context')
            elif kind == 'compacted':
                if context['compactions'] is not None:
                    context['compactions'] += 1
                if payload.get('message'):
                    context['latest_summary'] = clean(payload['message'])
            elif kind == 'event_msg' and payload.get('type') == 'token_count' and payload.get('info'):
                info = payload['info']
                token_usage = {'total': pick(info.get('total_token_usage') or {}, TOKENS),
                               'last': pick(info.get('last_token_usage') or {}, TOKENS),
                               'model_context_window': info.get('model_context_window')}
            elif kind == 'response_item' and payload.get('role') in ('developer', 'system', 'user'):
                text = '\n'.join(item.get('text', '') for item in payload.get('content', []) if isinstance(item, dict))
                if text and (payload.get('role') != 'user' or text.lstrip().startswith(('# AGENTS.md instructions', '<user_instructions>', '<INSTRUCTIONS>'))):
                    instructions.append({'source': f"Recorded {payload['role']} instructions (record {index})", 'text': clean(text)})
        configured_settings = {target: clean(thread[key]) for key, target in {'cwd': 'cwd', 'model': 'model',
                               'reasoningEffort': 'effort', 'modelProvider': 'model_provider'}.items() if thread.get(key) is not None}
        instruction_sources = thread.get('instructionSources')
        if isinstance(instruction_sources, list):
            configured_settings['instruction_sources'] = [clean(item) for item in instruction_sources]
        for key, value in configured_settings.items():
            if key != 'instruction_sources' and sources.get('settings.' + key) != 'latest recorded turn context':
                settings[key] = value
                sources['settings.' + key] = thread_source
        metadata.update({target: clean(thread[key]) for key, target in {'cliVersion': 'cli_version', 'originator': 'originator',
            'parentThreadId': 'parent_id', 'forkedFromId': 'forked_from_id', 'source': 'source', 'createdAt': 'created_at',
            'updatedAt': 'updated_at', 'historyMode': 'history_mode', 'agentRole': 'agent_role', 'agentNickname': 'agent_nickname',
            'projectId': 'project_id', 'sessionId': 'session_id', 'ephemeral': 'ephemeral'}.items() if thread.get(key) is not None})
        if isinstance(thread.get('gitInfo'), dict):
            native_git = thread['gitInfo']
            git.update(pick(native_git, {'branch'}))
            if native_git.get('sha'):
                git['commit_hash'] = native_git['sha']
            if native_git.get('originUrl'):
                git['repository_url'] = repository_url(native_git['originUrl'])
        metadata['git'] = git
        live_usage = self.token_usage.get(session_id)
        if live_usage:
            def usage(value):
                return {re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower(): value[key] for key in value
                        if re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower() in TOKENS}
            token_usage = {'total': usage(live_usage.get('total', {})), 'last': usage(live_usage.get('last', {})),
                           'model_context_window': live_usage.get('modelContextWindow')}
        if token_usage:
            context['token_usage'] = token_usage
            sources['context.token_usage'] = 'thread/tokenUsage/updated notification' if live_usage else 'latest recorded token_count event'
        cwd = settings.get('cwd') or (agent['cwd'] if agent else '')
        recorded = {'status': 'available', 'source': 'Session state, rollout and gateway launch request',
                    'settings': settings, 'configured_settings': configured_settings, 'metadata': metadata, 'context': context, 'instructions': instructions,
                    'memories': memories, 'skills': skills, 'sources': sources, 'inheritance': inheritance,
                    'caller_conversation': caller_conversation}
        if isinstance(request.get('prompt'), str):
            recorded['task'] = clean(request['prompt'])
            sources['task'] = 'gateway launch request'
        if agent:
            recorded['delegation'] = {key: clean(agent[key]) for key in ('parent_id', 'depth', 'max_depth', 'max_children')}
            sources['delegation'] = 'gateway agent database'
        attachments = []
        for artifact_id in request.get('attachments') or []:
            try:
                artifact = service.protocol.artifacts.get(artifact_id)
            except (OSError, ValueError):
                warnings.append('A recorded launch attachment is unavailable.')
                continue
            attachments.append(pick(artifact, {'id', 'name', 'mime', 'size', 'kind'}))
        if attachments:
            recorded['attachments'] = attachments
            sources['attachments'] = 'gateway launch attachment metadata'
        return recorded, cwd, warnings
