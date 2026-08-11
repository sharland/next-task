#!/usr/bin/env python3
"""
next_task_mcp.py - expose next-task to the Claude desktop app over MCP.

WHY THIS EXISTS
    Claude Code sessions run on this machine and can call next_task.py directly.
    Projects in the Claude app cannot: they have no filesystem. This server is
    the bridge, so a Project can file a ticket into the same stores.

HOW IT AVOIDS DISAGREEING WITH THE CLI
    Every tool here runs next_task.py as a subprocess and returns what it said,
    verbatim. Nothing is reimplemented and nothing is parsed, so there is no
    second copy of the rules to drift out of step - the same code path, the same
    validation, the same store lock, the same ID ledger. dashboard.py achieves
    this by importing the functions; a subprocess gets the same guarantee for
    the write paths, where the printed notices ("Unblocked: X") are part of the
    answer and worth passing through unchanged.

THE SCOPE ARGUMENT
    Which store to write to is a required argument on every tool, with no
    default, and every reply names the store it used. On disk the personal/work
    split is enforced by a path; through here it is enforced by an argument,
    which is weaker. Making it explicit and echoing it back is what keeps a
    mistake visible instead of silent. Do not add a default.

INSTALL
    In claude_desktop_config.json:

        "mcpServers": {
          "next-task": {
            "command": "python",
            "args": ["/path/to/next_task_mcp.py"]
          }
        }

    Approve the read tools freely; leave create and update asking every time.
    That prompt is the confirmation step - it is the only thing standing between
    a Project's instructions and a ticket landing in the wrong store.

DEPENDENCY NOTE
    Imports the MCP SDK, which next_task.py must never do. Same arrangement as
    Flask in dashboard.py: a third-party library is allowed in a file beside the
    tool, never in the tool itself, because the data path stays stdlib-only.
"""
import subprocess
import sys
from pathlib import Path

from mcp.server import MCPServer

import next_task

HERE = Path(__file__).resolve().parent
CLI = HERE / "next_task.py"

server = MCPServer(
    name="next-task",
    instructions=(
        "Local ticket tracker with dependency-aware readiness. Every tool needs "
        "a 'scope' naming which store to use - ask the user rather than guessing, "
        "because the stores separate work that is subject to different rules. "
        "Draft a ticket and get the user's agreement before calling create_ticket "
        "or update_ticket."
    ),
)


def known_scopes() -> list:
    """Store folders sitting beside next_task.py.

    Discovered rather than hardcoded, so adding a third store needs no change
    here - the same rule dashboard.py uses to find stores."""
    return sorted(
        child.name for child in next_task.STORES_ROOT.iterdir()
        if child.is_dir() and (child / "tickets").is_dir()
    )


def run_cli(scope: str, *args: str) -> str:
    """Run one next_task.py command and hand back what it said.

    The store is named in the reply on purpose: a Project supplies the scope
    from its own instructions, so the only way a wrong one becomes visible is
    if every answer states where it went."""
    available = known_scopes()
    if scope not in available:
        return (f"No store called {scope!r}. Available: {', '.join(available)}.\n"
                f"Nothing was read or written.")
    store = next_task.STORES_ROOT / scope
    result = subprocess.run(
        [sys.executable, str(CLI), "--store", str(store), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(HERE),
    )
    body = (result.stdout or "").strip()
    problem = (result.stderr or "").strip()
    parts = [f"store: {scope}  ({store})"]
    if body:
        parts.append(body)
    if problem:
        parts.append(f"warnings/errors:\n{problem}")
    if result.returncode != 0:
        parts.append(f"(command exited {result.returncode})")
    elif not body:
        parts.append("(no output)")
    return "\n\n".join(parts)


def csv(values) -> str:
    """Accept either a list or an already-comma-joined string."""
    if values is None:
        return ""
    if isinstance(values, str):
        return values
    return ",".join(str(v) for v in values)


@server.tool(description="List tickets that can be worked on now: not finished, "
                         "and with every prerequisite settled. Ordered by priority.")
def list_ready(scope: str) -> str:
    return run_cli(scope, "ready")


@server.tool(description="List tickets that are waiting on something, and say what "
                         "each one is waiting for.")
def list_blocked(scope: str) -> str:
    return run_cli(scope, "blocked")


@server.tool(description="List tickets in a store. Omit status for everything "
                         "unfinished; pass one of open, in_progress, done, cancelled "
                         "to filter.")
def list_tickets(scope: str, status: str = "") -> str:
    args = ["list"] + (["--status", status] if status else [])
    return run_cli(scope, *args)


@server.tool(description="Show one ticket in full, including what it waits on and "
                         "what waits on it.")
def show_ticket(scope: str, ticket_id: str) -> str:
    return run_cli(scope, "show", ticket_id)


@server.tool(description="Create a ticket. Confirm the wording, the store and the "
                         "source with the user first - this writes to their real "
                         "task list. 'source' should name the project the work "
                         "belongs to.")
def create_ticket(scope: str, title: str, source: str, description: str = "",
                  priority: str = "medium", tags=None, depends_on=None) -> str:
    args = ["create", title, "--source", source, "--priority", priority]
    if description:
        args += ["--desc", description]
    if csv(tags):
        args += ["--tags", csv(tags)]
    if csv(depends_on):
        args += ["--depends-on", csv(depends_on)]
    return run_cli(scope, *args)


@server.tool(description="Change an existing ticket. Closing one is "
                         "status='done'. Only the fields you pass are altered.")
def update_ticket(scope: str, ticket_id: str, status: str = "", title: str = "",
                  description: str = "", priority: str = "", source: str = "",
                  add_depends=None, remove_depends=None) -> str:
    args = ["update", ticket_id]
    for flag, value in (("--status", status), ("--title", title),
                        ("--priority", priority), ("--source", source),
                        ("--add-depends", csv(add_depends)),
                        ("--remove-depends", csv(remove_depends))):
        if value:
            args += [flag, value]
    if description:
        args += ["--desc", description]
    return run_cli(scope, *args)


@server.tool(description="Check a store for problems: dangling references, "
                         "dependency loops, unreadable files, mismatched ids.")
def verify_store(scope: str) -> str:
    return run_cli(scope, "verify")


@server.tool(description="Names of the ticket stores available, for when you need "
                         "to ask the user which one applies.")
def list_stores() -> str:
    return "Available scopes: " + ", ".join(known_scopes())


if __name__ == "__main__":
    server.run("stdio")
