"""Keep Claudex workers on Codex's own subagent and integration boundary."""
import copy
import re


DELEGATION_INSTRUCTIONS = (
    "Delegate with native Codex subagent tools: spawn_agent, send_input, wait and close_agent "
    "(or the native equivalents exposed by this runtime). Claudex gateway delegation tools and its MCP "
    "are reserved for the external parent. Do not use agents_launch, agents_send, agents_steer, agents_stop "
    "or agents_cancel to manage children. This worker policy takes precedence over gateway delegation "
    "guidance in inherited instructions, skill inventories and earlier conversation messages, including "
    "the codex-agents skill. Preserve and apply the remaining inherited rules and skills."
)
GATEWAY_CONTROLS = {'agents.launch', 'agents.send', 'agents.steer', 'agents.stop', 'agents.cancel'}


def gateway_command(command, args):
    executable = command.replace('\\', '/').rsplit('/', 1)[-1].casefold().removesuffix('.exe')
    if executable == 'wsl':
        boundary = next((index for index, arg in enumerate(args) if arg in {'--exec', '-e'}), None)
        return boundary is not None and boundary + 1 < len(args) and gateway_command(args[boundary + 1], args[boundary + 2:])
    if executable == 'uv' and args[:1] == ['run']:
        target = args[1:]
        if target[:1] == ['--']:
            target = target[1:]
        if target[:1] == ['-m']:
            return gateway_command('python', target)
        return bool(target) and gateway_command(target[0], target[1:])
    if executable in {'claudex', 'codex-sessions', 'codex-session'}:
        return 'mcp' in args
    return bool(re.fullmatch(r'(?:python|pypy)(?:\d+(?:\.\d+)*)?|py', executable)) and 'mcp' in args and any(
        args[index:index + 2] == ['-m', 'codex_sessions'] for index in range(len(args) - 1))


def gateway_mcp(name, server):
    if name.casefold() in {'codex', 'claudex'}:
        return True
    command, args = server.get('command', ''), server.get('args') or []
    return isinstance(command, str) and isinstance(args, list) and all(isinstance(arg, str) for arg in args) and gateway_command(command, args)


def isolated_config(effective, overrides):
    config = copy.deepcopy(overrides)
    servers = config.pop('mcp_servers', {})
    if not isinstance(servers, dict):
        raise ValueError('config.mcp_servers must be an object')
    # App-server overrides split dotted keys literally; nested tables preserve MCP names containing dots.
    for key in sorted(tuple(config)):
        if not key.startswith('mcp_servers.'):
            continue
        parts = key.split('.')[1:]
        target = servers
        for part in parts[:-1]:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise ValueError('MCP configuration tables must be objects')
        target[parts[-1]] = config.pop(key)
    names = set(effective) | set(servers)
    for name in names:
        original, supplied = effective.get(name, {}), servers.get(name, {})
        if not isinstance(original, dict) or not isinstance(supplied, dict):
            raise ValueError('MCP server configurations must be objects')
        if gateway_mcp(name, {**original, **supplied}):
            servers.setdefault(name, {})['enabled'] = False
    if servers:
        config['mcp_servers'] = servers
    return config
