import asyncio

import pytest
from test_smoke import service


def test_default_and_claude_task_tiers_resolve_from_available_models(tmp_path):
    async def check():
        instance = service(tmp_path)
        catalog = await instance.models.get()
        assert catalog["default_model"] == "gpt-6.1-sol"
        assert await instance.models.resolve(None) == "gpt-6.1-sol"
        assert await instance.models.resolve("sonnet") == "gpt-6.1-sol"
        assert await instance.models.resolve("opus") == "gpt-6-astra"
        assert await instance.models.resolve("fable") == "gpt-6-astra"
        assert await instance.models.resolve("haiku") == "gpt-6-luna"
        assert await instance.models.resolve("sonnet[1m]") == "gpt-6.1-sol"
        assert await instance.models.resolve("us.anthropic.claude-opus-5-5-v1:0") == "gpt-6-astra"
        with pytest.raises(ValueError, match="not available"):
            await instance.models.resolve("imaginary-new-model")
        assert catalog["comparison_note"]
    asyncio.run(check())


def test_claude_role_comparisons_are_qualified_by_max_without_changing_defaults(tmp_path):
    async def check():
        catalog = await service(tmp_path).models.get()
        models = {model["id"]: model for model in catalog["models"]}
        for model_id, roles in (("gpt-6-astra", ("Fable", "Opus")), ("gpt-6.1-sol", ("Opus", "Sonnet"))):
            comparison = models[model_id]["claude_comparison"]
            assert all(role in comparison for role in roles)
            assert "at max" in comparison
            assert models[model_id]["default_effort"] == "high"
        assert catalog["default_effort"] == "high"
    asyncio.run(check())
