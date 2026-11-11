import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="Bash completion requires a POSIX shell")


@pytest.mark.parametrize("words,expected", [
    (["codex-session", "sta"], {"start", "status"}),
    (["codex-sessions", "--home", "/some/home", "res"], {"restart"}),
    (["codex-session", "call", "agents.st"], {"agents.status", "agents.stop", "agents.steer"}),
    (["codex-sessions", "completion", "b"], {"bash"}),
    (["codex-session", "start", "--da"], {"--data-dir"}),
    (["codex-session", "start", "--ad"], {"--address-port"}),
    (["codex-session", "rou"], {"route"}),
])
def test_bash_completion_covers_commands_protocol_operations_and_options(tmp_path, words, expected):
    generated = subprocess.run([sys.executable, "-m", "codex_sessions", "completion", "bash"], cwd=ROOT, capture_output=True, text=True, check=True)
    completion = tmp_path / "completion.bash"; completion.write_text(generated.stdout)
    script = f"source {shlex.quote(str(completion))}\nCOMP_WORDS=({shlex.join(words)})\nCOMP_CWORD={len(words)-1}\n_codex_sessions_complete\nprintf '%s\\n' \"${{COMPREPLY[@]}}\""
    result = subprocess.run(["bash", "--noprofile", "--norc", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert set(result.stdout.splitlines()) == expected


def test_directory_completion_preserves_spaces_and_excludes_files(tmp_path):
    (tmp_path / "my project").mkdir()
    (tmp_path / "my file.txt").write_text("test")
    generated = subprocess.run([sys.executable, "-m", "codex_sessions", "completion", "bash"], cwd=ROOT, capture_output=True, text=True, check=True)
    script = generated.stdout + "\nCOMP_WORDS=(codex-session start --data-dir 'my ')\nCOMP_CWORD=3\n_codex_sessions_complete\nprintf '%s\\n' \"${COMPREPLY[@]}\""
    result = subprocess.run(["bash", "--noprofile", "--norc", "-c", script], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == ["my project"]
