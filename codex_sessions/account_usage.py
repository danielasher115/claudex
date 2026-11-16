"""Account quota snapshots and explicit earned reset redemption."""
import asyncio
import math
import time

from .rpc import RpcError


class AccountUsageError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def text(value):
    return value if isinstance(value, str) else None


def window(value):
    if not isinstance(value, dict) or number(value.get('usedPercent')) is None:
        return None
    return {'used_percent': value['usedPercent'], 'window_minutes': number(value.get('windowDurationMins')),
            'resets_at': number(value.get('resetsAt'))}


def snapshot(payload):
    fallback = payload.get('rateLimits')
    buckets = payload.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        buckets = {'codex': fallback} if isinstance(fallback, dict) and fallback else {}
    limits, plan = [], None
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        limit_id = text(bucket.get('limitId')) or key
        plan = plan or text(bucket.get('planType'))
        credits = bucket.get('credits')
        if isinstance(credits, dict) and type(credits.get('hasCredits')) is bool and type(credits.get('unlimited')) is bool:
            credits = {'has_credits': credits['hasCredits'], 'unlimited': credits['unlimited'], 'balance': text(credits.get('balance'))}
        else:
            credits = None
        limits.append({'id': limit_id, 'name': text(bucket.get('limitName')) or ('Codex' if limit_id == 'codex' else limit_id),
                       'primary': window(bucket.get('primary')), 'secondary': window(bucket.get('secondary')), 'credits': credits})
    if plan is None and isinstance(fallback, dict):
        plan = text(fallback.get('planType'))
    summary = payload.get('rateLimitResetCredits')
    reset_credits = None
    if isinstance(summary, dict) and type(summary.get('availableCount')) is int and summary['availableCount'] >= 0:
        rows = summary.get('credits')
        details = None
        if isinstance(rows, list):
            details = [{'id': row['id'], 'title': text(row.get('title')), 'expires_at': number(row.get('expiresAt'))}
                       for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str) and row.get('status') == 'available']
        reset_credits = {'available_count': summary['availableCount'], 'credits': details}
    return {'status': 'available' if limits or reset_credits is not None else 'unavailable', 'plan_type': plan,
            'limits': limits, 'reset_credits': reset_credits, 'checked_at': time.time()}


class AccountUsage:
    def __init__(self, service):
        self.service = service
        self.cached = None
        self.at = 0
        self.version = 0
        self.pending = None
        self.consume_lock = asyncio.Lock()

    def invalidate(self):
        self.cached = None
        self.version += 1

    def observe(self, message):
        if message.get('method') in {'account/rateLimits/updated', 'account/updated'}:
            self.invalidate()

    async def get(self, refresh=False):
        if not refresh and self.cached is not None and time.monotonic() - self.at < 60:
            return self.cached
        if self.pending is None or self.pending.done():
            self.pending = asyncio.create_task(self._read())
        pending = self.pending
        try:
            return await asyncio.shield(pending)
        finally:
            if pending.done() and self.pending is pending:
                self.pending = None

    async def _read(self):
        result = {'status': 'unavailable', 'plan_type': None, 'limits': [], 'reset_credits': None,
                  'message': 'Plan usage is unavailable from this Codex runtime.'}
        try:
            async with asyncio.timeout(8):
                for _ in range(2):
                    version = self.version
                    payload = await self.service.rpc.call('account/rateLimits/read', {})
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid account usage snapshot')
                    if version == self.version:
                        result = snapshot(payload)
                        break
        except (RpcError, OSError, TimeoutError, ValueError, TypeError):
            pass
        result['checked_at'] = time.time()
        self.cached, self.at = result, time.monotonic()
        return result

    async def close(self):
        pending, self.pending = self.pending, None
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def consume(self, body):
        key = body.get('idempotency_key')
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise ValueError('idempotency_key must be a nonempty string of at most 256 characters')
        params = {'idempotencyKey': key}
        if 'credit_id' in body:
            credit_id = body['credit_id']
            if not isinstance(credit_id, str) or not credit_id.strip() or len(credit_id) > 1024:
                raise ValueError('credit_id must be a nonempty string of at most 1024 characters')
            params['creditId'] = credit_id
        if self.consume_lock.locked():
            raise AccountUsageError('A reset is already in progress. Wait for it to finish before trying again.', 409)
        async with self.consume_lock:
            self.invalidate()
            try:
                result = await self.service.rpc.call('account/rateLimitResetCredit/consume', params)
            except (RpcError, OSError, TimeoutError) as exc:
                self.invalidate()
                raise AccountUsageError('The reset could not be confirmed. Retry the same attempt to check its result.') from exc
            self.invalidate()
            usage = await self.get(refresh=True)
            outcome = result.get('outcome') if isinstance(result, dict) else None
            if outcome not in {'reset', 'alreadyRedeemed', 'nothingToReset', 'noCredit'}:
                raise AccountUsageError('The Codex runtime returned an unknown reset result.')
            return {'outcome': outcome, 'usage': usage}
