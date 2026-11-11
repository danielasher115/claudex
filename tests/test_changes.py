import asyncio

from test_smoke import service
from codex_sessions.gateway import Gateway as Service


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
