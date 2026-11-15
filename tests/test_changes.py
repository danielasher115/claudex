import asyncio
import os

import pytest

from test_smoke import service
from codex_sessions.gateway import Gateway as Service


def test_workspace_git_uses_the_platform_quiet_process_policy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from codex_sessions import changes

    observed = {}
    monkeypatch.setattr(changes, "quiet_process_options", lambda: {"creationflags": 123}, raising=False)
    def run(command, **options):
        observed["creationflags"] = options.get("creationflags")
        return SimpleNamespace(returncode=0, stdout=b"")
    monkeypatch.setattr(changes.subprocess, "run", run)

    changes.git(tmp_path, "status")

    assert observed.get("creationflags") == 123


def test_native_run_diff_replaces_snapshots_and_survives_restart(tmp_path):
    async def check():
        app = service(tmp_path)
        def notify(thread, turn, diff):
            app.notification({"method": "turn/diff/updated", "params": {"threadId": thread, "turnId": turn, "diff": diff}})
        notify("a", "turn-active", "first patch")
        assert (await app.page("a"))["items"][0]["diff"] == "first patch"
        notify("a", "turn-active", "latest patch")
        notify("b", "turn-active", "other agent patch")
        assert (await app.page("a"))["items"][0]["diff"] == "latest patch"
        restarted = Service(app.home, app.home / "socket", tmp_path / "data")
        restarted.rpc = app.rpc
        assert (await restarted.page("a"))["items"][0]["diff"] == "latest patch"
        notify("a", "turn-active", "")
        assert (await app.page("a"))["items"][0]["diff"] == ""
        app.db.close(); restarted.db.close()
    asyncio.run(check())


def git(repo, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    for name in ("tracked.txt", "deleted.txt", "untouched.txt"):
        (repo / name).write_text("original\n", newline="\n")
    (repo / ".gitignore").write_text("ignored/\n", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    return repo


def test_shell_workspace_changes_include_real_patches_without_native_file_events(tmp_path):
    import subprocess

    async def check():
        repo = repository(tmp_path)
        app = service(tmp_path)
        original = app.rpc.call

        async def call(method, params=None):
            if method == "turn/start":
                subprocess.run(["sh", "-c", "printf 'shell edit\\n' > tracked.txt; printf 'new file\\n' > new.txt; rm deleted.txt; mkdir ignored; printf secret > ignored/private.txt"], cwd=repo, check=True)
                return {"turn": {"id": "turn-active"}}
            if method == "thread/turns/list":
                return {"data": [{"id": "turn-active", "status": "completed", "items": []}]}
            return await original(method, params)

        app.rpc.call = call
        await app.start_turn("a", {"threadId": "a", "input": []}, str(repo))
        marker = (await app.page("a"))["items"][0]
        patch = marker.get("workspace_diff", "")
        assert "-original\n+shell edit\n" in patch
        assert "+new file\n" in patch
        assert "deleted file mode" in patch
        assert "ignored/private.txt" not in patch
        assert "untouched.txt" not in patch
        assert marker["workspace_source"] == "observed-workspace"
        subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", "-"], input=patch.encode(), check=True, capture_output=True)
        restarted = Service(app.home, app.home / "socket", tmp_path / "data")
        restarted.rpc = app.rpc
        assert (await restarted.page("a"))["items"][0]["workspace_diff"] == patch
        app.db.close()
        restarted.db.close()

    asyncio.run(check())


def test_dirty_baseline_is_incremental_and_survives_agent_commit(tmp_path):
    async def check():
        repo = repository(tmp_path)
        (repo / "tracked.txt").write_text("staged before turn\n", newline="\n")
        git(repo, "add", "tracked.txt")
        (repo / "tracked.txt").write_text("dirty before turn\n", newline="\n")
        (repo / "untouched.txt").write_text("other preexisting edit\n", newline="\n")
        (repo / "existing-new.txt").write_text("preexisting untracked\n", newline="\n")
        app = service(tmp_path)
        original = app.rpc.call

        async def call(method, params=None):
            if method == "turn/start":
                (repo / "tracked.txt").write_text("edited during turn\n", newline="\n")
                (repo / "existing-new.txt").write_text("untracked edited during turn\n", newline="\n")
                (repo / "committed.txt").write_text("committed during turn\n", newline="\n")
                git(repo, "add", ".")
                git(repo, "commit", "-qm", "agent commit")
                return {"turn": {"id": "turn-active"}}
            if method == "thread/turns/list":
                return {"data": [{"id": "turn-active", "status": "completed", "items": []}]}
            return await original(method, params)

        app.rpc.call = call
        await app.start_turn("a", {"threadId": "a", "input": []}, str(repo))
        patch = (await app.page("a"))["items"][0].get("workspace_diff", "")
        assert "-dirty before turn\n+edited during turn\n" in patch
        assert "-preexisting untracked\n+untracked edited during turn\n" in patch
        assert "+committed during turn\n" in patch
        assert "other preexisting edit" not in patch
        assert "staged before turn" not in patch
        app.db.close()

    asyncio.run(check())


def test_observation_is_bounded_and_never_follows_links(tmp_path):
    import os
    from codex_sessions.changes import baseline, compare, MAX_FILE

    repo = repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private evidence must not be captured\n", newline="\n")
    (repo / "linked").symlink_to(outside, target_is_directory=True)
    (repo / "binary.bin").write_bytes(b"before\0binary")
    base = baseline(repo)
    (repo / "binary.bin").write_bytes(b"after\0binary")
    (repo / "large.txt").write_bytes(b"x" * (MAX_FILE + 1))
    (repo / "tracked.txt").unlink()
    (repo / "tracked.txt").symlink_to(outside / "private.txt")
    diff, warning = compare(base)
    assert "Binary files a/binary.bin and b/binary.bin differ" in diff
    assert "private evidence" not in diff
    assert "linked" not in diff
    assert "tracked.txt" not in diff
    assert "file(s) omitted" in warning


def test_workspace_patch_handles_spaces_and_missing_final_newlines(tmp_path):
    import subprocess
    from codex_sessions.changes import baseline, compare

    repo = repository(tmp_path)
    (repo / "space file.txt").write_text("before without newline", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "file name")
    base = baseline(repo)
    (repo / "space file.txt").write_text("after without newline", newline="\n")
    diff, warning = compare(base)
    assert not warning
    assert "No newline at end of file" in diff
    subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", "-"], input=diff.encode(), check=True, capture_output=True)


def test_completion_notification_captures_without_opening_the_page(tmp_path):
    async def check():
        repo = repository(tmp_path)
        app = service(tmp_path)
        await app.start_turn("a", {"threadId": "a", "input": []}, str(repo))
        (repo / "tracked.txt").write_text("observed at completion\n", newline="\n")
        app.notification({"method": "turn/completed", "params": {"threadId": "a", "turn": {"id": "turn-next", "status": "completed"}}})
        await app.changes.close()
        saved = app.changes.saved("a", "turn-next")
        assert "+observed at completion\n" in saved["diff"]
        (repo / "tracked.txt").write_text("later unrelated edit\n", newline="\n")
        assert "later unrelated" not in (await app.changes.finish("a", {"id": "turn-next", "status": "completed"}))["diff"]
        app.db.close()

    asyncio.run(check())


def test_capture_budget_failure_does_not_attribute_preexisting_edits(tmp_path, monkeypatch):
    import codex_sessions.changes as changes

    repo = repository(tmp_path)
    (repo / "tracked.txt").write_text("preexisting\n", newline="\n")
    monkeypatch.setattr(changes, "MAX_PATHS", 0)
    base = changes.baseline(repo)
    diff, warning = changes.compare(base)
    assert not diff
    assert "baseline was not captured" in warning


def test_rejected_launch_and_send_discard_the_pending_baseline(tmp_path):
    import pytest
    from test_protocol import AgentRuntime
    from test_smoke import message
    from codex_sessions.rpc import RpcError

    async def check():
        repo = repository(tmp_path)
        (repo / "tracked.txt").write_text("dirty baseline retained only while starting\n", newline="\n")
        app = service(tmp_path)
        app.rpc = AgentRuntime()
        original = app.rpc.call

        async def call(method, params=None):
            if method == "turn/start":
                assert app.changes.pending[params["threadId"]].dirty["tracked.txt"]
                raise RpcError("Rejected test turn", -32602)
            return await original(method, params)

        app.rpc.call = call
        with pytest.raises(RpcError, match="Rejected test turn"):
            await app.protocol.launch({"prompt": "Failure cleanup", "cwd": str(repo), "inherit_context": False})
        assert not app.changes.pending
        assert not app.changes.baselines
        sent = await app.send("a", message())
        app.known_threads["a"]["cwd"] = str(repo)
        await app.deliver(sent["id"], "turn/start", {"threadId": "a", "input": []})
        assert not app.changes.pending
        assert not app.changes.baselines
        assert app.db.execute("SELECT status FROM messages WHERE id=?", (sent["id"],)).fetchone()[0] == "failed"
        app.db.close()

    asyncio.run(check())


def test_cancelled_start_discards_the_pending_baseline(tmp_path):
    import pytest

    async def check():
        repo = repository(tmp_path)
        app = service(tmp_path)

        async def call(method, params=None):
            assert app.changes.pending["a"]
            raise asyncio.CancelledError()

        app.rpc.call = call
        with pytest.raises(asyncio.CancelledError):
            await app.start_turn("a", {"threadId": "a", "input": []}, str(repo))
        assert not app.changes.pending
        app.db.close()

    asyncio.run(check())


def test_interrupted_and_failed_completions_release_the_baseline(tmp_path):
    async def check():
        repo = repository(tmp_path)
        app = service(tmp_path)
        for status in ("interrupted", "failed"):
            await app.changes.begin("a", str(repo))
            app.changes.bind("a", status)
            (repo / "tracked.txt").write_text(f"{status} turn workspace\n", newline="\n")
            app.notification({"method": "turn/completed", "params": {"threadId": "a", "turn": {"id": status, "status": status}}})
            await app.changes.close()
            assert not app.changes.baselines
            assert not app.changes.pending
            assert not app.changes.locks
            assert f"+{status} turn workspace\n" in app.changes.saved("a", status)["diff"]
        app.db.close()

    asyncio.run(check())


def test_shutdown_discards_unfinished_baselines_without_claiming_a_diff(tmp_path):
    async def check():
        repo = repository(tmp_path)
        app = service(tmp_path)
        await app.changes.begin("a", str(repo))
        app.changes.bind("a", "unfinished")
        await app.changes.begin("b", str(repo))
        assert app.changes.baselines and app.changes.pending
        await app.changes.close()
        assert not app.changes.baselines
        assert not app.changes.pending
        assert not app.changes.locks
        assert app.changes.saved("a", "unfinished") is None
        app.db.close()

    asyncio.run(check())


def test_unborn_repository_first_commit_keeps_new_files_and_excludes_preexisting(tmp_path):
    from codex_sessions.changes import baseline, compare

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "preexisting.txt").write_text("already here\n", newline="\n")
    git(repo, "add", ".")
    base = baseline(repo)
    (repo / "first-commit.txt").write_text("created during turn\n", newline="\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "first commit during turn")
    diff, warning = compare(base)
    assert not warning
    assert "+created during turn\n" in diff
    assert "preexisting.txt" not in diff


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not support Git executable permission bits")
def test_workspace_patches_preserve_executable_modes(tmp_path):
    import subprocess
    from codex_sessions.changes import baseline, compare

    repo = repository(tmp_path)
    (repo / "removed.sh").write_text("#!/bin/sh\nexit 0\n", newline="\n")
    (repo / "removed.sh").chmod(0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "executable baseline")
    base = baseline(repo)
    (repo / "tracked.txt").chmod(0o755)
    (repo / "added.sh").write_text("#!/bin/sh\nexit 0\n", newline="\n")
    (repo / "added.sh").chmod(0o755)
    (repo / "removed.sh").unlink()
    diff, warning = compare(base)
    assert not warning
    assert "old mode 100644\nnew mode 100755\n" in diff
    assert "new file mode 100755\n" in diff
    assert "deleted file mode 100755\n" in diff
    subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", "-"], input=diff.encode(), check=True, capture_output=True)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="This platform does not provide POSIX FIFOs")
def test_workspace_capture_omits_fifos_without_blocking(tmp_path):
    from codex_sessions.changes import contents, OMITTED

    os.mkfifo(tmp_path / "pipe")
    assert contents(tmp_path, "pipe") is OMITTED


def test_dirty_workspace_patch_preserves_crlf_bytes(tmp_path):
    import subprocess
    from codex_sessions.changes import baseline, compare

    repo = repository(tmp_path)
    (repo / "tracked.txt").write_bytes(b"before\r\n")
    base = baseline(repo)
    (repo / "tracked.txt").write_bytes(b"after\r\n")
    diff, warning = compare(base)
    assert not warning
    assert "-before\r\n+after\r\n" in diff
    subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", "-"], input=diff.encode(), check=True, capture_output=True)
