"""Read-only background terminals owned by a native Codex thread."""
import asyncio
import time

from .rpc import RpcError


class BackgroundShells:
    def __init__(self, service):
        self.service = service

    async def get(self, session_id):
        await self.service.session(session_id)
        result = {'status': 'available', 'source': 'thread/backgroundTerminals/list',
                  'scope': 'Background terminals reported by the connected Codex runtime for this session.',
                  'items': [], 'partial': False, 'checked_at': time.time()}
        items, cursors = {}, set()
        cursor = None
        try:
            async with asyncio.timeout(6):
                for _ in range(10):
                    page = await self.service.rpc.call('thread/backgroundTerminals/list',
                                                       {'threadId': session_id, 'limit': 100, 'cursor': cursor})
                    for item in page['data']:
                        items[item['processId']] = {key: item[key] for key in
                            ('processId', 'itemId', 'command', 'cwd', 'osPid', 'cpuPercent', 'rssKb') if key in item}
                    cursor = page.get('nextCursor')
                    if not cursor:
                        break
                    if cursor in cursors:
                        result['partial'] = True
                        break
                    cursors.add(cursor)
                else:
                    result['partial'] = True
        except (RpcError, OSError, TimeoutError, KeyError, TypeError):
            return {**result, 'status': 'unavailable',
                    'message': 'The connected runtime could not list background terminals for this session.'}
        result['items'] = list(items.values())
        if result['partial']:
            result['message'] = 'The runtime returned an incomplete background terminal list.'
        return result
