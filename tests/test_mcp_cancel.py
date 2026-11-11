import json
import subprocess
import sys


def test_cancel_during_launch_waits_for_receipt_then_stops_owned_agent(tmp_path):
    script = '''
import sys,time
from pathlib import Path
import codex_sessions.mcp as m
root=Path(sys.argv[1])
def call(method,params,port,data_dir):
 if method=='agents.launch':
  print('started',file=sys.stderr,flush=True)
  time.sleep(.15)
  return {'thread_id':'owned','turn_id':'owned-turn','status':'running'}
 if method=='agents.cancel':
  assert params['turn_id']=='owned-turn'
  (root/'stopped').write_text(params['thread_id'])
  return {'status':'stopped'}
 raise AssertionError(method)
m.call=call
m.run(8766,root)
'''
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "agents_launch", "arguments": {"prompt": "Work"}}}) + "\n")
    process.stdin.flush()
    assert process.stderr.readline().strip() == "started"
    process.communicate(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}) + "\n", timeout=5)
    assert process.returncode == 0
    assert (tmp_path / "stopped").read_text() == "owned"
