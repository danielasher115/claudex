import base64
from starlette.testclient import TestClient
from test_smoke import Runtime
from codex_sessions.server import create_app


def test_preview_returns_prepared_text_without_exposing_server_paths(tmp_path):
    app = create_app(tmp_path, tmp_path / "socket", tmp_path / "data")
    app.state.service.rpc = Runtime()
    artifact = app.state.service.protocol.artifacts.put({"name": "notes.txt", "data": base64.b64encode(b"Complete document text").decode()})
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get(f"/api/artifacts/{artifact['id']}/preview")
        assert response.status_code == 200
        assert response.json()["text"] == "Complete document text"
        assert "path" not in response.json()
        assert client.get(f"/api/artifacts/{artifact['id']}/pages/-1").status_code == 404
        assert client.get("/api/artifacts/not-an-id/preview").status_code == 404
