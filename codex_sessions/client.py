import base64
from pathlib import Path

import httpx

from .artifacts import MAX_FILE
from .context import capture, merge
from .delivery import resolve_send
from .platform import local_path


def endpoint(port, data_dir):
    from .lifecycle import runtime
    state = runtime(data_dir)
    resolved_port = state["port"] if state else (port or 8766)
    route = runtime(data_dir, "route.json")
    url = state.get("public_url") if state else None
    if route and (not state or (route.get("backend_pid"), route.get("backend_identity")) == (state["pid"], state["identity"])):
        url = route["public_url"]
        if not state:
            resolved_port = route["port"]
    return resolved_port, url or f"http://localhost:{resolved_port}"


def file_payload(path, body=None):
    path = local_path(path).resolve()
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError("Attach an existing file no larger than 25 MB")
    return {**(body or {}), "name": (body or {}).get("name") or path.name, "data": base64.b64encode(path.read_bytes()).decode()}


def call(method, params, port, data_dir):
    port, _ = endpoint(port, data_dir)
    token = (Path(data_dir) / "mcp.token").read_text(encoding="utf-8").strip()
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"x-codex-token": token}, timeout=75, trust_env=False) as client:
        def request(operation, body):
            response = client.post("/api/protocol", json={"protocol_version": "1", "method": operation, "params": body})
            data = response.json()
            if not response.is_success:
                raise ValueError(data.get("error", f"Request failed ({response.status_code})"))
            return data["result"]
        params = dict(params)
        if method == "agents.launch" and not params.get("parent_id"):
            params["cwd"] = str(local_path(params.get("cwd") or Path.cwd()).resolve())
        if method == "agents.launch" and params.get("inherit_context", True) and not params.get("parent_id"):
            supplied_context = params.get("context") or {}
            caller_cwd = str(local_path((supplied_context.get("caller") or {}).get("cwd") or Path.cwd()).resolve())
            caller_context = capture(caller_cwd)
            if params.get("cwd") and Path(params["cwd"]).resolve() != Path(caller_cwd).resolve():
                caller_context = merge(capture(params["cwd"]), caller_context)
            params["context"] = merge(caller_context, supplied_context)
        if method == "artifacts.put" and params.get("path"):
            params = file_payload(params.pop("path"), params)
        files = params.pop("files", [])
        if files:
            if not isinstance(files, list):
                raise ValueError("files must be a list of local paths")
            params["attachments"] = list(params.get("attachments") or []) + [request("artifacts.put", file_payload(path))["id"] for path in files]
        if method == "agents.send" and params.get("mode") != "queue":
            if params.get("expected_turn_id"):
                if params.get("mode") is None:
                    params["mode"] = "steer"
            else:
                target = params.get("thread_id") or params.get("threadId") or params.get("conversationId")
                params = resolve_send(params, request("agents.status", {"thread_id": target}))
        return request(method, params)
