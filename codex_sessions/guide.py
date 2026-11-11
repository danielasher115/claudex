from pathlib import Path

SKILL_URI = "codex-sessions://skills/codex-agents"
SKILL_PATH = Path(__file__).parent / "skills" / "codex-agents" / "SKILL.md"


def read_skill():
    return SKILL_PATH.read_text(encoding="utf-8")
