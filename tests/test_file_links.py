import json
import os
import time
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from codex_sessions.artifacts import MAX_FILE
from codex_sessions.rpc import RpcError
from codex_sessions.server import create_app
from test_smoke import Runtime


class FileRuntime(Runtime):
    def __init__(self, text, cwd, *, unsupported=False):
        super().__init__()
        self.text, self.cwd, self.unsupported = text, str(cwd), unsupported
        self.item_reads = []

    async def call(self, method, params=None):
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "status": {"type": "idle"}, "cwd": self.cwd, "source": "appServer"}}
        if method == "thread/items/read":
            self.item_reads.append(params)
            if self.unsupported:
                raise RpcError("Method not found", -32601)
            if params != {"threadId": "a", "turnId": "turn-old", "itemIds": ["item-old"]}:
                raise RpcError("Item not found", -32602)
            return {"data": [{"turnId": "turn-old", "item": {"id": "item-old", "type": "agentMessage", "text": self.text}}]}
        return await super().call(method, params)


def setup(tmp_path, text, *, cached=True, unsupported=False):
    app = create_app(tmp_path, tmp_path / "socket", tmp_path / "data")
    service = app.state.service
    service.rpc = FileRuntime(text, tmp_path, unsupported=unsupported)
    if cached:
        service.pages[("a", "older-cursor", "old-revision", True, True)] = (time.monotonic(), {
            "items": [{"id": "turn-old:item-old", "type": "agentMessage", "text": text}],
        })
    return app


def preview(client, href, *, item_id="turn-old:item-old", session="a", origin="http://localhost:8767", document=None):
    body = {"item_id": item_id, "href": href}
    if document:
        body.update(document_id=document["id"], document_path=document["source_path"])
    return client.post(f"/api/sessions/{session}/files/preview", json=body, headers={"origin": origin})


def test_linked_file_uses_existing_artifact_preview_and_keeps_first_snapshot(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# Complete report\n" + "evidence\n" * 5000)
    app = setup(tmp_path, f"[report]({report})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        response = preview(client, str(report))
        assert response.status_code == 200
        artifact = response.json()
        assert artifact["name"] == "report.md" and "path" not in artifact
        assert artifact["source_path"] == str(report)
        assert client.get(f"/api/artifacts/{artifact['id']}/preview").json()["text"] == report.read_text()
        assert client.get(artifact["content_url"]).content == report.read_bytes()
        report.write_text("a different report")
        assert preview(client, str(report)).json()["id"] == artifact["id"]
        report.unlink()
        service = app.state.service
        service.pages.clear()
        service.rpc.text = "[report removed]"
        assert preview(client, str(report)).json()["id"] == artifact["id"]
        assert preview(client, str(report)).json()["source_path"] == str(report)
        assert preview(client, str(report), session="unknown").status_code == 404


def test_document_links_use_saved_markdown_and_document_directory(tmp_path):
    folder = tmp_path / "docs"; folder.mkdir()
    report, nested, private = folder / "report.md", tmp_path / "nested.md", tmp_path / "private.md"
    report.write_text("[nested](../nested.md)")
    nested.write_text("nested evidence")
    private.write_text("private evidence")
    app = setup(tmp_path, "[report](docs/report.md)")
    with TestClient(app, base_url="http://localhost:8767") as client:
        document = preview(client, "docs/report.md").json()
        assert "source_path" in document
        report.write_text("[private](../private.md)")
        assert preview(client, "../private.md", document=document).status_code == 403
        report.unlink()
        response = preview(client, "../nested.md", document=document)
        assert response.status_code == 200, response.text
        artifact = response.json()
        assert artifact["source_path"] == str(nested) and "path" not in artifact
        assert client.get(artifact["content_url"]).text == "nested evidence"
        nested.unlink()
        assert preview(client, "../nested.md", document=document).json() == artifact


def test_nested_document_cache_distinguishes_directories_and_supports_chains(tmp_path):
    for name in ("first", "second"):
        folder = tmp_path / name; folder.mkdir()
        (folder / "report.md").write_text("[nested](nested.md)")
        (folder / "nested.md").write_text("[leaf](leaf.md)")
        (folder / "leaf.md").write_text(name + " evidence")
    app = setup(tmp_path, "[first](first/report.md) [second](second/report.md)")
    with TestClient(app, base_url="http://localhost:8767") as client:
        for name in ("first", "second"):
            document = preview(client, name + "/report.md").json()
            assert "source_path" in document
            nested = preview(client, "nested.md", document=document).json()
            assert nested["source_path"] == str(tmp_path / name / "nested.md")
            leaf = preview(client, "leaf.md", document=nested).json()
            assert leaf["source_path"] == str(tmp_path / name / "leaf.md")
            assert client.get(leaf["content_url"]).text == name + " evidence"


@pytest.mark.parametrize("forgery", ["session", "item", "path", "id", "missing_path", "missing_id"])
def test_document_link_parent_must_match_saved_message_association(tmp_path, forgery):
    report, private = tmp_path / "report.md", tmp_path / "private.md"
    report.write_text("[private](private.md)")
    private.write_text("private evidence")
    app = setup(tmp_path, "[report](report.md)")
    with TestClient(app, base_url="http://localhost:8767") as client:
        document = preview(client, "report.md").json()
        body = {"item_id": "turn-old:item-old", "href": "report.md", "document_id": document["id"], "document_path": str(report)}
        session = "a"
        if forgery == "session":
            session = "b"
        elif forgery == "item":
            body["item_id"] = "turn-other:item-other"
        elif forgery == "path":
            body["document_path"] = str(tmp_path / "other.md")
        elif forgery == "id":
            body["document_id"] = "f" * 64
        elif forgery == "missing_path":
            del body["document_path"]
        else:
            del body["document_id"]
        response = client.post(f"/api/sessions/{session}/files/preview", json=body, headers={"origin": "http://localhost:8767"})
        assert response.status_code == 403, response.text


def test_copied_private_path_and_wrong_item_do_not_grant_file_access(tmp_path):
    linked, private = tmp_path / "linked.md", tmp_path / "private.md"
    linked.write_text("public")
    private.write_text("secret")
    app = setup(tmp_path, f"[report]({linked})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, str(private)).status_code == 403
        assert preview(client, str(linked), item_id="turn-other:item-other").status_code in (403, 404)
        assert preview(client, str(linked), origin="http://evil.test").status_code == 403


@pytest.mark.parametrize("markdown,href", [
    ("[report](report(parentheses).md)", "report(parentheses).md"),
    ("[report](<report with spaces.md>)", "report%20with%20spaces.md"),
    ("[report](./report.md:12:3)", "./report.md:12:3"),
    ("[report](report.md#L12-L18)", "report.md#L12-L18"),
    ("[report][audit]\n\n[audit]: <report with spaces.md> \"Audit title\"", "report%20with%20spaces.md"),
    ("[audit]: report.md\n\n[audit]", "report.md"),
    ("[report](report\\(parentheses\\).md)", "report(parentheses).md"),
])
def test_markdown_destinations_are_validated_before_reading(tmp_path, markdown, href):
    for name in ("report.md", "report with spaces.md", "report(parentheses).md"):
        (tmp_path / name).write_text("complete")
    app = setup(tmp_path, markdown)
    with TestClient(app, base_url="http://localhost:8767") as client:
        response = preview(client, href)
        assert response.status_code == 200, response.text
        assert response.json()["characters"] == 8


def test_native_exact_item_lookup_works_without_cached_history_page(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("native evidence")
    app = setup(tmp_path, f"[report]({report})", cached=False)
    with TestClient(app, base_url="http://localhost:8767") as client:
        response = preview(client, str(report))
        assert response.status_code == 200
        assert client.get(f"/api/artifacts/{response.json()['id']}/preview").json()["text"] == "native evidence"
        assert app.state.service.rpc.item_reads == [{"threadId": "a", "turnId": "turn-old", "itemIds": ["item-old"]}]


@pytest.mark.parametrize("href", ["https://example.test/report.md", "http://localhost:8766/report.md", "//localhost/report.md", "javascript:alert(1)", "file://remote/report.md"])
def test_remote_or_different_origin_links_are_rejected_without_fetch(tmp_path, href):
    app = setup(tmp_path, f"[report]({href})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, href).status_code == 400


def test_loopback_same_origin_and_file_localhost_paths(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("linked")
    for href in (f"http://localhost:8767{urlsplit(report.as_uri()).path}", f"file://localhost{urlsplit(report.as_uri()).path}"):
        app = setup(tmp_path, f"[report]({href})")
        with TestClient(app, base_url="http://localhost:8767") as client:
            assert preview(client, href).status_code == 200


def test_named_local_address_links_remain_same_origin(tmp_path):
    report = tmp_path / "report.md"; report.write_text("linked")
    origin = "http://codex.localhost:8765"
    href = f"{origin}{urlsplit(report.as_uri()).path}"
    app = setup(tmp_path, f"[report]({href})")
    with TestClient(app, base_url=origin) as client:
        assert preview(client, href, origin=origin).status_code == 200
        assert preview(client, f"http://codex.localhost:8764{urlsplit(report.as_uri()).path}", origin=origin).status_code == 400
        assert preview(client, f"http://other.localhost:8765{urlsplit(report.as_uri()).path}", origin=origin).status_code == 400


@pytest.mark.parametrize("backend_identity,expected", [("current", 200), ("stale", 400)])
def test_proxy_named_links_require_a_live_route_bound_to_this_backend(tmp_path, backend_identity, expected):
    from codex_sessions.lifecycle import identity, save_runtime
    report = tmp_path / "report.md"; report.write_text("linked")
    href = f"http://codex.localhost:8765{urlsplit(report.as_uri()).path}"
    app = setup(tmp_path, f"[report]({href})")
    process_identity = identity(os.getpid())
    save_runtime(tmp_path / "data", {"pid": os.getpid(), "identity": process_identity, "backend_pid": os.getpid(),
                 "backend_identity": process_identity if backend_identity == "current" else "stale", "port": 8767,
                 "public_url": "http://codex.localhost:8765"}, "route.json")
    with TestClient(app, base_url="http://127.0.0.1:8767") as client:
        assert preview(client, href, origin="http://127.0.0.1:8767").status_code == expected
        assert preview(client, f"http://codex.localhost:8764{urlsplit(report.as_uri()).path}", origin="http://127.0.0.1:8767").status_code == 400


def test_missing_oversize_and_nonfile_destinations_return_json(tmp_path):
    missing, large, directory = tmp_path / "missing.md", tmp_path / "large.md", tmp_path
    with large.open("wb") as stream:
        stream.truncate(MAX_FILE + 1)
    app = setup(tmp_path, f"[missing]({missing}) [large]({large}) [directory]({directory})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        for href, status in ((missing, 404), (large, 400), (directory, 400)):
            response = preview(client, str(href))
            assert response.status_code == status
            assert response.headers["content-type"] == "application/json"


def test_legacy_record_id_uses_recorded_markdown(tmp_path):
    report = tmp_path / "legacy.md"
    report.write_text("legacy evidence")
    app = setup(tmp_path, "", unsupported=True)
    app.state.service.history.find = lambda session_id: {"rollout_path": str(tmp_path / "sessions" / "record.jsonl")}
    path = tmp_path / "sessions" / "record.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": f"[legacy]({report})"}]}}) + "\n")
    with TestClient(app, base_url="http://localhost:8767") as client:
        response = preview(client, str(report), item_id="record-0")
        assert response.status_code == 200
        assert response.json()["name"] == "legacy.md"


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="Windows drive paths require Windows or WSL")
def test_windows_drive_links_follow_the_host_filesystem(monkeypatch):
    from codex_sessions.file_links import local_path
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    expected = Path("C:/Users/example/report.md") if sys.platform == "win32" else Path("/mnt/c/Users/example/report.md")
    for value in [r"C:\Users\example\report.md:12", "file:///C:/Users/example/report.md#L3", "C:%5CUsers%5Cexample%5Creport.md:12"]:
        assert local_path(value, "/tmp", "http://localhost:8767") == expected



@pytest.mark.parametrize("markdown", ["`[private]({path})`", "```markdown\n[private]({path})\n```", "    [private]({path})", r"\[private]({path})"])
def test_code_and_escaped_markdown_links_do_not_authorize_private_files(tmp_path, markdown):
    private = tmp_path / "private.md"
    private.write_text("private evidence")
    app = setup(tmp_path, markdown.format(path=private))
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, str(private)).status_code == 403


def test_snapshot_survives_service_restart_and_original_file_deletion(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("durable evidence")
    app = setup(tmp_path, f"[report]({report})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        artifact = preview(client, str(report)).json()
    report.unlink()
    reopened = setup(tmp_path, "message no longer cached", cached=False)
    with TestClient(reopened, base_url="http://localhost:8767") as client:
        assert preview(client, str(report)).json()["id"] == artifact["id"]
        assert client.get(f"/api/artifacts/{artifact['id']}/preview").json()["text"] == "durable evidence"


def test_preparation_rejects_text_over_existing_context_limit(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("x" * 240001)
    app = setup(tmp_path, f"[report]({report})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        response = preview(client, str(report))
        assert response.status_code == 400
        assert "context limit" in response.json()["error"]


@pytest.mark.parametrize("label", ["`report.md`", "**Full report**", "report [with evidence]"])
def test_formatted_link_labels_are_validated_as_rendered_markdown(tmp_path, label):
    report = tmp_path / "report.md"; report.write_text("report")
    app = setup(tmp_path, f"[{label}]({report})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, str(report)).status_code == 200


@pytest.mark.parametrize("fence", ["```markdown", "~~~markdown"])
def test_unclosed_code_fences_do_not_authorize_file_previews(tmp_path, fence):
    private = tmp_path / "private.md"; private.write_text("private")
    app = setup(tmp_path, f"{fence}\n[private]({private})")
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, str(private)).status_code == 403


def test_dynamic_tool_markdown_output_can_preview_its_recorded_file(tmp_path):
    report = tmp_path / "report.md"; report.write_text("dynamic report")
    app = setup(tmp_path, "")
    page = next(iter(app.state.service.pages.values()))[1]
    page["items"] = [{"id": "turn-old:item-old", "type": "dynamicToolCall", "contentItems": [{"type": "inputText", "text": f"[report]({report})"}]}]
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, str(report)).status_code == 200


@pytest.mark.parametrize("kind", ["file", "http"])
def test_local_autolinks_can_open_the_same_recorded_file(tmp_path, kind):
    report = tmp_path / "report.md"; report.write_text("autolink report")
    href = f"file://{urlsplit(report.as_uri()).path}" if kind == "file" else f"http://localhost:8767{urlsplit(report.as_uri()).path}"
    text = f"<{href}>" if kind == "file" else href
    app = setup(tmp_path, text)
    with TestClient(app, base_url="http://localhost:8767") as client:
        assert preview(client, href).status_code == 200


@pytest.mark.parametrize('href', ['file:////server/share/report.md', '%5C%5Cserver%5Cshare%5Creport.md',
                                  'http://localhost:8767/%5Cserver/share/report.md'])
def test_decoded_unc_links_are_rejected_before_filesystem_resolution(monkeypatch, href):
    import codex_sessions.file_links as links
    def reject_access(*args, **kwargs):
        pytest.fail('A network path must be rejected before filesystem access')
    monkeypatch.setattr(links, 'host_path', reject_access)
    with pytest.raises(links.FileLinkError, match='Remote file links'):
        links.local_path(href, '/workspace', 'http://localhost:8767')
