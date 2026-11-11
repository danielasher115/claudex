"""Image snapshots derived only from paths or image bytes in actual recorded Codex items."""
import asyncio
import base64
import io
import re
import hashlib
import json
import uuid
from collections import OrderedDict, defaultdict
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .artifacts import MAX_FILE
from .platform import local_path


class Media:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.cache = OrderedDict()
        self.records = self.artifacts.root.parent / "viewed-images"
        self.records.mkdir(exist_ok=True)
        self.locks = defaultdict(asyncio.Lock)

    @staticmethod
    def blocks(item):
        kind = item.get("type")
        value = []
        if kind == "userMessage" and not item.get("attachments"):
            value = item.get("content")
        elif kind == "mcpToolCall":
            result = item.get("result")
            value = result.get("content") if isinstance(result, dict) else []
        elif kind == "dynamicToolCall":
            value = item.get("contentItems")
        elif kind == "functionCallOutput":
            value = item.get("output")
            if isinstance(value, dict):
                value = value.get("content")
        return value if isinstance(value, list) else []

    def capture(self, value, cwd):
        if value.startswith("data:image/"):
            header, encoded = value.split(",", 1)
            if ";base64" not in header or len(encoded) > MAX_FILE * 4 // 3 + 8:
                raise ValueError("Unsupported inline image")
            data = base64.b64decode(encoded, validate=True)
            name = "tool-image"
            key, signature = value, None
        else:
            if re.match(r"^https?://", value):
                raise ValueError("This history stores only an external image URL; image bytes were not recorded locally")
            path = local_path(value)
            if not path.is_absolute():
                if not cwd:
                    raise ValueError("The recorded workspace is required for this relative image path")
                path = Path(cwd) / path
            path = path.resolve()
            stat = path.stat()
            if not path.is_file() or stat.st_size > MAX_FILE:
                raise ValueError("The viewed image is not a readable file under 25 MB")
            key, signature = str(path), (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            cached = self.cache.get(key)
            if cached and cached[0] == signature:
                self.cache.move_to_end(key)
                return cached[1]
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE + 1)
            name = path.name
        if len(data) > MAX_FILE:
            raise ValueError("The viewed image exceeds 25 MB")
        with Image.open(io.BytesIO(data)) as image:
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}.get(image.format)
            if not extension:
                raise ValueError("This recorded image format cannot be previewed")
            image.verify()
        if Path(name).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            name += extension
        artifact = self.artifacts.put({"name": name, "data": base64.b64encode(data).decode(), "kind": "image"})
        if signature is not None:
            self.cache[key] = (signature, artifact)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
        return artifact

    async def enrich(self, item, cwd, cache_key=None):
        kind = item.get("type")
        if kind not in ("imageView", "imageGeneration"):
            if not any(isinstance(block, dict) and block.get("type") in ("image", "inputImage", "input_image", "localImage") for block in self.blocks(item)):
                return item
        key = str(cache_key or item.get("id", ""))
        if not key:
            return await self.prepare(item, cwd)
        async with self.locks[key]:
            record = self.records / (hashlib.sha256(key.encode()).hexdigest() + ".json")
            try:
                images = json.loads(record.read_text(encoding="utf-8"))
                return {**item, "images": images}
            except (OSError, ValueError):
                pass
            result = await self.prepare(item, cwd)
            if result.get("images"):
                try:
                    temporary = self.records / (str(uuid.uuid4()) + ".tmp")
                    temporary.write_text(json.dumps(result["images"]), encoding="utf-8")
                    temporary.replace(record)
                except OSError:
                    pass
            return result

    async def prepare(self, item, cwd):
        result = dict(item)
        sources = []
        kind = item.get("type")
        if kind == "imageView" and item.get("path"):
            sources.append(item["path"])
        if kind == "imageGeneration":
            if item.get("savedPath"):
                sources.append(item["savedPath"])
            if item.get("result"):
                value = item["result"]
                sources.append(value if value.startswith("data:") else "data:image/png;base64," + value)
        for block in self.blocks(item):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "localImage" and block.get("path"):
                sources.append(block["path"])
            elif block.get("type") in ("image", "inputImage", "input_image"):
                if block.get("data"):
                    sources.append(f"data:{block.get('mimeType', 'image/png')};base64,{block['data']}")
                elif block.get("url") or block.get("imageUrl") or block.get("image_url"):
                    sources.append(block.get("url") or block.get("imageUrl") or block["image_url"])
                else:
                    result["media_error"] = "This image is stored by file ID; its bytes are not available in this history."
        images, errors = [], []
        for source in sources[:20]:
            try:
                if not isinstance(source, str):
                    raise ValueError("The recorded image source must be a string")
                image = await asyncio.to_thread(self.capture, source, cwd)
                if not any(existing["id"] == image["id"] for existing in images):
                    images.append(image)
                if kind == "imageGeneration":
                    errors.clear()
                    break
            except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
                errors.append("The image referenced by this recorded step is unavailable or cannot be decoded.")
        if images:
            result["images"] = images
        if errors:
            result["media_error"] = " ".join(dict.fromkeys(errors))
        return result
