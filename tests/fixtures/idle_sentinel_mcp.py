"""A credential-free stdio MCP server whose lifetime is visible to its parent test."""

import json
import os
import sys
from pathlib import Path


with Path(sys.argv[1]).open("a") as output:
    output.write(json.dumps({"pid": os.getpid(), "ppid": os.getppid()}) + "\n")

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "idle-sentinel", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "ping", "description": "Return a local sentinel response", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "sentinel"}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
