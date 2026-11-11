import asyncio
import io
from pathlib import Path

from PIL import Image

from codex_sessions.artifacts import Artifacts
from codex_sessions.media import Media


def test_viewed_image_is_snapshotted_and_uses_existing_artifact_preview(tmp_path):
    async def check():
        source = tmp_path / "screen.png"
        Image.new("RGB", (16, 16), "blue").save(source)
        store = Artifacts(tmp_path / "data")
        media = Media(store)
        item = await media.enrich({"id": "view", "type": "imageView", "path": str(source)}, str(tmp_path))
        image = item["images"][0]
        assert image["kind"] == "image"
        assert image["content_url"].startswith("/api/artifacts/")
        snapshot = Path(store.get(image["id"], internal=True)["path"])
        source.unlink()
        assert snapshot.is_file()
        reloaded = await Media(store).enrich({"id": "view", "type": "imageView", "path": str(source)}, str(tmp_path))
        assert reloaded["images"][0]["id"] == image["id"]
    asyncio.run(check())


def test_missing_or_non_image_path_does_not_break_the_conversation(tmp_path):
    async def check():
        media = Media(Artifacts(tmp_path / "data"))
        missing = await media.enrich({"id": "missing", "type": "imageView", "path": str(tmp_path / "missing.png")}, str(tmp_path))
        assert missing["media_error"] and not missing.get("images")
        secret = tmp_path / "private.png"; secret.write_text("not an image")
        invalid = await media.enrich({"id": "invalid", "type": "imageView", "path": str(secret)}, str(tmp_path))
        assert invalid["media_error"] and not invalid.get("images")
    asyncio.run(check())


def test_mcp_inline_image_outputs_are_visible_without_arbitrary_url_fetching(tmp_path):
    import base64
    async def check():
        content = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(content, format="PNG")
        media = Media(Artifacts(tmp_path / "data"))
        item = await media.enrich({"id": "mcp", "type": "mcpToolCall", "result": {"content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(content.getvalue()).decode()}]}}, str(tmp_path))
        assert len(item["images"]) == 1
    asyncio.run(check())


def test_native_function_image_outputs_and_remote_references(tmp_path):
    import base64
    async def check():
        content = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(content, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(content.getvalue()).decode()
        media = Media(Artifacts(tmp_path / "data"))
        item = await media.enrich({"id": "function", "type": "functionCallOutput", "output": [{"type": "input_image", "image_url": data_url}]}, str(tmp_path))
        assert len(item["images"]) == 1
        remote = await media.enrich({"id": "remote", "type": "functionCallOutput", "output": [{"type": "input_image", "image_url": "https://example.invalid/image.png"}]}, str(tmp_path))
        assert remote["media_error"] and not remote.get("images")
        unavailable = await media.enrich({"id": "file", "type": "functionCallOutput", "output": [{"type": "input_image", "file_id": "file-1"}]}, str(tmp_path))
        assert "file ID" in unavailable["media_error"]
    asyncio.run(check())


def test_generation_falls_back_to_recorded_bytes_and_malformed_sources_do_not_break_history(tmp_path):
    import base64
    async def check():
        content = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(content, format="PNG")
        media = Media(Artifacts(tmp_path / "data"))
        item = await media.enrich({"id": "generation", "type": "imageGeneration", "savedPath": str(tmp_path / "deleted.png"), "result": base64.b64encode(content.getvalue()).decode()}, str(tmp_path))
        assert len(item["images"]) == 1 and not item.get("media_error")
        invalid = await media.enrich({"id": "malformed", "type": "mcpToolCall", "result": {"content": [{"type": "image", "imageUrl": {"url": "invalid"}}]}}, str(tmp_path))
        assert invalid["media_error"]
        png = bytearray(content.getvalue())
        png[png.index(b"IDAT") + 5] ^= 1
        broken = tmp_path / "corrupt.png"
        broken.write_bytes(png)
        invalid_png = await media.enrich({"id": "corrupt", "type": "imageView", "path": str(broken)}, str(tmp_path))
        assert invalid_png["media_error"] and not invalid_png.get("images")
    asyncio.run(check())


def test_recorded_history_images_use_the_same_preview_when_live_history_is_unavailable(tmp_path):
    import base64
    from test_smoke import service, Runtime
    from codex_sessions.rpc import RpcError

    class OfflineHistory(Runtime):
        async def call(self, method, params=None):
            if method == "thread/turns/list":
                raise RpcError("Live history unavailable")
            return await super().call(method, params)

    async def check():
        content = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(content, format="PNG")
        instance = service(tmp_path)
        instance.rpc = OfflineHistory()
        instance.history.find = lambda _: {}
        instance.history.page = lambda *args: {"items": [{"id": "record-1", "type": "record", "record": {"type": "function_call_output", "output": [{"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(content.getvalue()).decode()}]}}], "next_cursor": None, "source": "recorded"}
        page = await instance.page("a")
        assert page["items"][0]["record"]["type"] == "functionCallOutput"
        assert len(page["items"][0]["images"]) == 1
        assert page["warning"]
    asyncio.run(check())
