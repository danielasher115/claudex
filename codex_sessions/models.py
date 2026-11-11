import asyncio
import re
import time


class Models:
    def __init__(self, service):
        self.service = service
        self.cached = None
        self.at = 0
        self.lock = asyncio.Lock()

    async def get(self, refresh=False):
        async with self.lock:
            if not refresh and self.cached and time.monotonic() - self.at < 300:
                return self.cached
            rows, cursor = [], None
            while True:
                result = await self.service.rpc.call("model/list", {"includeHidden": False, "cursor": cursor})
                rows.extend(result.get("data", []))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            if not rows:
                raise ValueError("The Codex runtime returned no available models")
            workhorses = [m for m in rows if re.fullmatch(r"gpt-\d+(?:\.\d+)*-(?:sol|codex)", m.get("model", ""))]
            def version(model):
                match = re.match(r"gpt-([\d.]+)-", model.get("model", ""))
                return tuple(int(v) for v in match.group(1).split(".")) if match else (0,)
            default = max(workhorses, key=version) if workhorses else next((m for m in rows if m.get("isDefault")), rows[0])
            models = []
            for row in rows:
                model = row.get("model") or row["id"]
                efforts = [e["reasoningEffort"] for e in row.get("supportedReasoningEfforts", [])]
                tier = "frontier" if "astra" in model else "fast" if "luna" in model else "balanced"
                comparison = {"frontier": "Opus / Fable-style demanding work", "fast": "Haiku-style fast delegated work", "balanced": "Sonnet-style everyday coding"}[tier]
                models.append({"id": model, "name": row.get("displayName", model), "description": row.get("description", ""), "is_default": model == default.get("model", default.get("id")),
                               "efforts": efforts, "default_effort": "high" if "high" in efforts else row.get("defaultReasoningEffort", "medium"), "runtime_default_effort": row.get("defaultReasoningEffort"),
                               "modalities": row.get("inputModalities", ["text"]), "tier": tier, "claude_comparison": comparison, "legacy": "Older" in row.get("description", "") or "Previous" in row.get("description", "")})
            ids = {m["id"] for m in models}
            balanced = default.get("model") or default["id"]
            aliases = {"sonnet": balanced, "opus": "gpt-6-astra", "fable": "gpt-6-astra", "haiku": "gpt-6-luna"}
            aliases = {key: value for key, value in aliases.items() if value in ids}
            self.cached = {"default_model": balanced, "default_effort": next(m["default_effort"] for m in models if m["id"] == balanced), "models": models, "claude_aliases": aliases,
                           "comparison_note": "Claude comparisons describe similar task roles, not measured capability equivalence. These launches always use Codex/OpenAI models.",
                           "sources": ["https://developers.openai.com/api/docs/models", "https://developers.openai.com/api/docs/guides/model-selection", "https://platform.claude.com/docs/en/models/overview"]}
            self.at = time.monotonic()
            return self.cached

    async def resolve(self, requested=None):
        catalog = await self.get()
        if not requested:
            return catalog["default_model"]
        if not isinstance(requested, str):
            raise ValueError("model must be a model ID or Claude task-tier alias")
        alias = requested.lower().split("[", 1)[0]
        match = re.search(r"(?:^|[.:])claude-(sonnet|opus|haiku|fable)(?:-|$)", alias)
        if match:
            alias = match.group(1)
        resolved = catalog["claude_aliases"].get(alias, requested)
        if resolved not in {m["id"] for m in catalog["models"]}:
            raise ValueError(f"Model {requested} is not available in this Codex catalog. Call models.list for available IDs.")
        return resolved
