import json
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows console handles are platform-specific')
def test_windows_child_process_has_no_console_window():
    result = subprocess.run([sys.executable, '-c',
                             'import ctypes,json; print(json.dumps({"console":ctypes.windll.kernel32.GetConsoleWindow()}))'],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['console'] == 0
