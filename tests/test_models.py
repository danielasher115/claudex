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
        assert await instance.models.resolve("haiku") == "gpt-6-luna"
        assert await instance.models.resolve("sonnet[1m]") == "gpt-6.1-sol"
        assert await instance.models.resolve("us.anthropic.claude-opus-5-5-v1:0") == "gpt-6-astra"
        with pytest.raises(ValueError, match="not available"):
            await instance.models.resolve("imaginary-new-model")
        assert catalog["comparison_note"]
    asyncio.run(check())
