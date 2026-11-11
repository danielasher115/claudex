import json

from codex_sessions.address import public_url


def test_port_free_address_applies_only_to_the_configured_forwarder(tmp_path):
    (tmp_path / 'address.json').write_text(json.dumps({'listen_port': 8765, 'public_port': 80}))
    assert public_url(8765, tmp_path) == 'http://codex.localhost'
    assert public_url(9000, tmp_path) == 'http://codex.localhost:9000'
    assert public_url(8765) == 'http://codex.localhost:8765'
    (tmp_path / 'address.json').write_text('{invalid')
    assert public_url(8765, tmp_path) == 'http://codex.localhost:8765'
