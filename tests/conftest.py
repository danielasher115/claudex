"""Keep native Windows test subprocesses headless."""
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def no_windows_test_consoles(monkeypatch):
    if sys.platform != 'win32':
        return
    original = subprocess.Popen
    from codex_sessions.platform import quiet_process_options

    def hidden_process(*args, **kwargs):
        flags = kwargs.get('creationflags', 0)
        if flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS):
            raise AssertionError('Tests must not create visible console windows')
        options = quiet_process_options()
        kwargs['creationflags'] = flags | options['creationflags']
        startup = kwargs.get('startupinfo') or options['startupinfo']
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs['startupinfo'] = startup
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', hidden_process)
