#!/usr/bin/env python3
"""
dashboard_launch.py - make sure the ticket dashboard is running, then get out
of the way.

Written to be called from a Claude Code SessionStart hook, which sets three
hard constraints:

  - It must not block. dashboard.py runs until killed, so it's spawned
    detached; this script returns immediately.
  - It must be safe to run over and over. Every new session calls it, so it
    checks whether the port is already serving and does nothing if so, rather
    than starting a rival server or piling up browser tabs. A tab is opened
    only at the moment the server is actually started.
  - It must never be noisy. A hook that prints or fails at the start of every
    session in every project is worse than no hook, so all output is suppressed
    and any error exits 0.

It also stays out of the way in folders that have nothing to do with tickets:
if there's no .ticket-scope marker here or in any parent, it does nothing.

Stdlib only, like next_task.py. Flask is dashboard.py's problem, not this one's.

USAGE
    python dashboard_launch.py [--port 5000]
"""
import argparse
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 5000
MARKER = ".ticket-scope"
STARTUP_TIMEOUT = 5.0
POLL_INTERVAL = 0.2


def in_a_ticket_project(start) -> bool:
    """Is there a .ticket-scope marker here or above? Same lookup next_task.py
    uses, so the hook fires exactly where the tool is relevant."""
    start = Path(start).resolve()
    return any((folder / MARKER).is_file() for folder in [start, *start.parents])


def already_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((host, port)) == 0


def wait_until_listening(port: int, timeout: float = STARTUP_TIMEOUT) -> bool:
    """Give the server a moment to bind, so the browser doesn't arrive first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if already_listening(port):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def quiet_python() -> str:
    """pythonw.exe where it exists, so Windows doesn't leave a console window
    sitting on the desktop for the life of the server."""
    running = Path(sys.executable)
    quiet = running.with_name("pythonw.exe")
    return str(quiet) if quiet.is_file() else str(running)


def spawn(port: int) -> None:
    """Start dashboard.py detached, so it outlives this process and the session."""
    detach = {}
    if os.name == "nt":
        detach["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        detach["start_new_session"] = True
    subprocess.Popen(
        [quiet_python(), str(HERE / "dashboard.py"), "--port", str(port)],
        cwd=str(HERE),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
    )


def main(port: int = DEFAULT_PORT, cwd=None) -> int:
    if not in_a_ticket_project(cwd or Path.cwd()):
        return 0
    if already_listening(port):
        return 0          # already up: leave the existing tab alone
    spawn(port)
    if wait_until_listening(port):
        webbrowser.open(f"http://127.0.0.1:{port}/")
    return 0


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--port", type=int, default=DEFAULT_PORT)
        main(port=parser.parse_args().port)
    except SystemExit:
        pass      # argparse exits 2 on a bad --port; the hook must not
    except Exception:
        pass      # nor on anything else that goes wrong
    sys.exit(0)
