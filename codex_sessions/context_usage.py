"""Bounded context snapshots for the conversation composer."""
import json
from collections import OrderedDict
from pathlib import Path

TAIL_LIMIT = 1024 * 1024
TOKEN_FIELDS = {'inputTokens': 'input_tokens', 'cachedInputTokens': 'cached_input_tokens',
                'cacheWriteInputTokens': 'cache_write_input_tokens', 'outputTokens': 'output_tokens',
                'reasoningOutputTokens': 'reasoning_output_tokens', 'totalTokens': 'total_tokens'}


def snapshot(last, window):
    if not isinstance(last, dict):
        return None
    last = {TOKEN_FIELDS.get(key, key): value for key, value in last.items()
            if TOKEN_FIELDS.get(key, key) in TOKEN_FIELDS.values() and type(value) is int and value >= 0}
    if not last:
        return None
    return {'last': last, 'model_context_window': window if type(window) is int and window > 0 else None}


class ContextUsage:
    def __init__(self, history, live):
        self.history = history
        self.live = live
        self.cache = OrderedDict()

    def get(self, session_id):
        live = self.live.get(session_id)
        if isinstance(live, dict):
            usage = snapshot(live.get('last'), live.get('modelContextWindow'))
            if usage:
                return usage
        row = next((row for row in self.history.sessions() if row['id'] == session_id), {})
        try:
            path = Path(row.get('rollout_path') or '').resolve()
            if not any(path.is_relative_to((self.history.home / name).resolve()) for name in ('sessions', 'archived_sessions')):
                return None
            stat = path.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            cached = self.cache.get(path)
            if cached and cached[0] == signature:
                self.cache.move_to_end(path)
                return cached[1]
            offset = max(0, stat.st_size - TAIL_LIMIT)
            with path.open('rb') as stream:
                stream.seek(offset)
                tail = stream.read(TAIL_LIMIT)
        except (OSError, ValueError):
            return None
        lines = tail.splitlines()
        if offset:
            lines = lines[1:]
        if not tail.endswith(b'\n'):
            lines = lines[:-1]
        usage = None
        for line in reversed(lines):
            if b'"token_count"' not in line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            payload = record.get('payload') if isinstance(record, dict) else None
            if not isinstance(payload, dict) or payload.get('type') != 'token_count':
                continue
            info = payload.get('info')
            if isinstance(info, dict):
                usage = snapshot(info.get('last_token_usage'), info.get('model_context_window'))
                if usage:
                    break
        self.cache[path] = (signature, usage)
        self.cache.move_to_end(path)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return usage
