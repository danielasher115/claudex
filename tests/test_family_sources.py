import json

from PIL import Image
from starlette.testclient import TestClient

from codex_sessions.server import create_app, source_items
from test_smoke import Runtime


def test_sources_pages_keep_prepared_files_and_public_evidence_without_loading_the_chat(tmp_path):
    image = tmp_path / "screenshot.png"
    Image.new("RGB", (4, 4), "blue").save(image)
    app = create_app(tmp_path, tmp_path / "socket", tmp_path / "data")
    instance = app.state.service
    calls = []

    class SourceRuntime(Runtime):
        async def call(self, method, params=None):
            calls.append((method, params))
            if method == "thread/loaded/list":
                return {"data": []}
            if method == "thread/items/list":
                assert params["threadId"] == "b" and params["limit"] == 60
                if params.get("cursor"):
                    assert params["cursor"] == "older"
                    return {"data": [{"turnId": "turn-active", "item": {"id": "older-file", "type": "agentMessage", "text": "[Older](/tmp/older.md)"}}]}
                return {"data": [{"turnId": "turn-active", "item": item} for item in [
                    {"id": "view", "type": "imageView", "path": str(image)},
                    {"id": "public", "type": "agentMessage", "text": "[Report](/tmp/report.md)", "annotations": [{"url": "https://example.com/cite", "title": "Cited", "private": "omit"}]},
                    {"id": "search", "type": "webSearch", "action": {"url": "https://example.com/open", "private": "omit"}, "results": [{"url": "https://example.com/result", "title": "Result", "snippet": "omit"}]},
                    {"id": "private", "type": "reasoning", "summary": ["PRIVATE REASONING"]},
                    {"id": "command", "type": "commandExecution", "command": "PRIVATE COMMAND", "aggregatedOutput": "PRIVATE OUTPUT"},
                    {"id": "tool", "type": "mcpToolCall", "arguments": "PRIVATE ARGS", "result": "PRIVATE RESULT"},
                ]], "nextCursor": "older"}
            if method == "thread/turns/list":
                assert params["itemsView"] == "notLoaded"
            assert method not in ("thread/read", "thread/resume")
            return await super().call(method, params)

    instance.rpc = SourceRuntime()
    instance.history.records = lambda *args: (_ for _ in ()).throw(AssertionError("Full rollout scan"))
    client = TestClient(app, base_url="http://localhost:8767")
    first = client.get("/api/sessions/b/sources")
    assert first.status_code == 200
    page = first.json()
    assert page["next_cursor"] == "items:older" and page["source"] == "codex"
    by_id = {item["id"]: item for item in page["items"]}
    assert set(by_id) == {"turn-turn-active", "turn-active:view", "turn-active:public", "turn-active:search"}
    prepared = by_id["turn-active:view"]["images"][0]
    assert prepared["content_url"].startswith("/api/artifacts/")
    assert client.get(prepared["content_url"]).status_code == 200
    assert "PRIVATE" not in json.dumps(page) and "omit" not in json.dumps(page)
    assert by_id["turn-active:public"]["annotations"] == [{"url": "https://example.com/cite", "title": "Cited"}]
    assert sum(method == "thread/items/list" for method, _ in calls) == 1
    second = client.get("/api/sessions/b/sources", params={"cursor": page["next_cursor"]}).json()
    assert second["next_cursor"] is None
    assert second["items"][-1] == {"id": "turn-active:older-file", "type": "agentMessage", "turn_id": "turn-active", "text": "[Older](/tmp/older.md)"}
    assert client.get("/api/sessions/absent/sources").status_code == 404


def test_source_projection_preserves_record_anchors_and_strips_nested_payloads():
    artifact = {"id": "file", "name": "file.txt", "content_url": "/api/artifacts/file/content", "text": "PRIVATE", "path": "PRIVATE"}
    assert source_items([
        {"id": "record-anchor", "type": "record", "attachments": [artifact], "record": {"id": "inner", "type": "message", "role": "assistant", "content": [{"text": "[File](/tmp/file.txt)", "annotations": [{"url_citation": {"url": "https://example.com", "private": "omit"}}], "data": "PRIVATE"}], "metadata": "PRIVATE"}},
        {"id": "system", "type": "message", "role": "system", "content": "PRIVATE"},
    ]) == [{"id": "record-anchor", "type": "message", "role": "assistant", "attachments": [{"id": "file", "name": "file.txt", "content_url": "/api/artifacts/file/content"}], "content": [{"text": "[File](/tmp/file.txt)", "annotations": [{"url_citation": {"url": "https://example.com"}}]}]}]


def test_source_projection_preserves_change_evidence_and_turns_without_private_payloads():
    projected = source_items([
        {"id": "turn-t", "type": "turn", "turn_id": "t", "status": "completed", "diff": "native patch", "workspace_diff": "workspace patch", "workspace_warning": "Incomplete baseline", "error": "PRIVATE"},
        {"id": "t:edit", "type": "fileChange", "turn_id": "t", "status": "completed", "changes": [{"path": "/repo/file.ts", "diff": "file patch", "kind": {"type": "update", "move_path": None, "secret": "PRIVATE"}, "output": "PRIVATE"}], "aggregatedOutput": "PRIVATE"},
        {"id": "t:tool", "type": "commandExecution", "turn_id": "t", "command": "PRIVATE"},
        {"id": "t:reasoning", "type": "reasoning", "turn_id": "t", "summary": "PRIVATE"},
    ])
    assert projected == [
        {"id": "turn-t", "type": "turn", "turn_id": "t", "status": "completed", "diff": "native patch", "workspace_diff": "workspace patch", "workspace_warning": "Incomplete baseline"},
        {"id": "t:edit", "type": "fileChange", "turn_id": "t", "status": "completed", "changes": [{"path": "/repo/file.ts", "diff": "file patch", "kind": {"type": "update"}}]},
    ]
    assert "PRIVATE" not in json.dumps(projected)
