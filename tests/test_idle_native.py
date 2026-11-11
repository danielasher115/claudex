"""Opt-in real-daemon check: CODEX_NATIVE_TEST_BIN=/path/to/codex pytest -q tests/test_idle_native.py."""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from codex_sessions.gateway import Gateway as Service


NATIVE_BIN = os.environ.get("CODEX_NATIVE_TEST_BIN")
pytestmark = pytest.mark.skipif(not NATIVE_BIN or sys.platform != "linux", reason="Set CODEX_NATIVE_TEST_BIN on Linux or WSL to run the isolated Unix daemon test")


class LocalResponses(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append({"path": self.path, "body": body})
        number = len(self.server.requests)
        text = f"Local response {number}"
        item = {"id": f"msg_{number}", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}]}
        response = {"id": f"resp_{number}", "object": "response", "created_at": int(time.time()), "status": "completed",
                    "model": body["model"], "output": [item],
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}},
            {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": text},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        encoded = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def process_alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


async def eventually(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if hasattr(result, "__await__"):
            result = await result
        if result:
            return result
        await asyncio.sleep(.05)
    raise AssertionError(f"Condition did not become true within {timeout} seconds")


def test_finished_native_agent_releases_mcp_and_queued_followup_reloads(tmp_path):
    async def check():
        home = tmp_path / "home"
        home.mkdir()
        sentinel_log = tmp_path / "sentinel.jsonl"
        sentinel = Path(__file__).parent / "fixtures" / "idle_sentinel_mcp.py"
        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalResponses)
        server.requests = []
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        config = f'''
model_provider = "local-test"
model = "gpt-5.4"
web_search = "disabled"
[model_providers.local-test]
name = "Local fixture"
base_url = "http://127.0.0.1:{server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
[mcp_servers.sentinel]
command = {json.dumps(sys.executable)}
args = {json.dumps([str(sentinel), str(sentinel_log)])}
'''
        (home / "config.toml").write_text(config)
        socket_path = tmp_path / "native.sock"
        log_path = tmp_path / "native.log"
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CODEX_HOME": str(home), "LANG": "C.UTF-8"}
        with log_path.open("w") as log:
            daemon = subprocess.Popen([NATIVE_BIN, "app-server", "--listen", f"unix://{socket_path}"],
                                      cwd=tmp_path, env=env, stdout=log, stderr=log, start_new_session=True)
            instance = Service(home, socket_path, tmp_path / "gateway")
            notifications = []
            lifecycle_task = None
            original_notification = instance.notification

            def observe(message):
                notifications.append(message)
                original_notification(message)

            instance.rpc.on_notification = observe
            try:
                await eventually(lambda: socket_path.exists() or daemon.poll() is not None)
                assert daemon.poll() is None, log_path.read_text()
                launched = await instance.protocol.launch({"prompt": "Return a local fixture response", "cwd": str(tmp_path),
                                                           "inherit_context": False, "approval-policy": "never"})
                thread_id = launched["thread_id"]

                def completed(turn_id):
                    return next((message["params"]["turn"] for message in notifications if
                                 message.get("method") == "turn/completed" and
                                 message.get("params", {}).get("turn", {}).get("id") == turn_id), None)

                first_turn = await eventually(lambda: completed(launched["turn_id"]))
                assert first_turn["status"] == "completed", first_turn
                assert any(item.get("text") == "Local response 1" for item in first_turn["items"])
                await eventually(sentinel_log.exists)
                first = json.loads(sentinel_log.read_text().splitlines()[0])
                assert first["ppid"] == daemon.pid
                assert process_alive(first["pid"])
                assert len(server.requests) == 1
                assert server.requests[0]["path"] == "/v1/responses"
                await asyncio.sleep(1.2)
                assert thread_id in (await instance.rpc.call("thread/loaded/list"))["data"]
                assert process_alive(first["pid"])
                lifecycle_task = asyncio.create_task(instance.thread_lifecycle.run())

                async def unloaded():
                    result = await instance.rpc.call("thread/loaded/list")
                    return thread_id not in result["data"]

                await eventually(unloaded, timeout=5)
                await eventually(lambda: not process_alive(first["pid"]), timeout=5)
                assert any(message.get("method") == "thread/closed" and
                           message.get("params", {}).get("threadId") == thread_id for message in notifications)
                sent = await instance.protocol.dispatch("agents.send", {"thread_id": thread_id, "text": "Return another local response"})
                queued = instance.db.execute("SELECT * FROM messages WHERE id=?", (sent["message_id"],)).fetchone()
                assert queued["status"] == "queued"
                await instance.start_queued(queued)
                delivered = instance.db.execute("SELECT * FROM messages WHERE id=?", (sent["message_id"],)).fetchone()
                assert delivered["status"] == "delivered", dict(delivered)
                second_turn = await eventually(lambda: completed(delivered["turn_id"]))
                assert second_turn["status"] == "completed", second_turn
                assert any(item.get("text") == "Local response 2" for item in second_turn["items"])
                assert len(server.requests) == 2
                pids = [json.loads(line) for line in sentinel_log.read_text().splitlines()]
                assert len(pids) == 2
                assert pids[1]["ppid"] == daemon.pid
                assert pids[1]["pid"] != first["pid"]
                assert process_alive(pids[1]["pid"])
                await eventually(unloaded, timeout=5)
                await eventually(lambda: not process_alive(pids[1]["pid"]), timeout=5)
            except BaseException:
                print(log_path.read_text())
                print(json.dumps(notifications[-12:], indent=2))
                raise
            finally:
                if lifecycle_task is not None:
                    lifecycle_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await lifecycle_task
                await instance.rpc.close()
                instance.db.close()
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(daemon.pid, signal.SIGTERM)
                try:
                    daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(daemon.pid, signal.SIGKILL)
                    daemon.wait(timeout=5)
                server.shutdown()
                server.server_close()
                serving.join(timeout=2)
    asyncio.run(check())
