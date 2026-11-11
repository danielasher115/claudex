"""Resolve follow-up intent against one observed agent state without retargeting retries."""
import uuid


def resolve_send(params, state):
    result = dict(params)
    mode = result.get('mode')
    if mode not in (None, 'steer', 'queue'):
        raise ValueError('Choose Queue or Steer.')
    if mode == 'queue':
        return result
    request_id = result.get('request_id')
    if request_id:
        request_id = str(uuid.UUID(str(request_id)))
        result['request_id'] = request_id
    existing = next((message for message in state.get('messages', []) if message['id'] == request_id), None)
    if existing and mode is None:
        result['mode'] = existing.get('mode', 'queue')
        if result['mode'] == 'steer' and existing.get('turn_id'):
            result.setdefault('expected_turn_id', existing['turn_id'])
        return result
    if existing and mode == 'steer' and existing.get('mode') == 'steer' and existing.get('turn_id'):
        result.setdefault('expected_turn_id', existing['turn_id'])
        return result
    active_turn = state.get('active_turn_id') if state.get('status') == 'active' else None
    if state.get('status') == 'active' and not active_turn and not result.get('expected_turn_id'):
        raise ValueError('The active turn could not be resolved. Inspect the agent before steering, or choose mode="queue" explicitly.')
    if mode == 'steer' and not active_turn and not result.get('expected_turn_id'):
        raise ValueError('An active turn is required for steering. Use mode="queue" to start a new turn.')
    result['mode'] = mode or ('steer' if active_turn or result.get('expected_turn_id') else 'queue')
    if result['mode'] == 'steer' and not result.get('expected_turn_id'):
        result['expected_turn_id'] = active_turn
    return result
