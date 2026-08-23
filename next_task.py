#!/usr/bin/env python3
"""
next_task.py - a minimal local ticket tracker with dependency-aware "what's ready" logic.

Zero third-party dependencies, stdlib only. Each ticket is one JSON file on disk,
so a git diff shows exactly what changed and nothing needs a database.

Every file operation explicitly specifies encoding="utf-8" - that's not decoration,
it's the exact class of bug that took down the last tool we tried on Windows.

USAGE
    python next_task.py --store PATH init
    python next_task.py --store PATH create "Title" [options]
    python next_task.py --store PATH list [--status open|in_progress|done|cancelled] [--all]
    python next_task.py --store PATH show ID
    python next_task.py --store PATH update ID [options]
    python next_task.py --store PATH delete ID [--force]
    python next_task.py --store PATH ready
    python next_task.py --store PATH blocked
    python next_task.py --store PATH verify

EXAMPLES
    python next_task.py --store ./work create "Fix login bug" --priority high
    python next_task.py --store ./work create "Deploy fix" --depends-on WORK-001
    python next_task.py --store ./work update WORK-001 --status done
    python next_task.py --store ./work update WORK-002 --add-depends WORK-003
    python next_task.py --store ./work ready
    python next_task.py --store ./work verify

    With a .ticket-scope marker in the project, --store can be left out entirely.

DESIGN NOTES (why it's built this way, not just what it does)
    - Status is the only stored state field. "Blocked" is never stored - it's
      computed fresh every time from the depends_on graph, so it can't drift
      out of sync with reality. Completing (or un-completing) a dependency
      instantly changes what's blocked, with nothing to remember to update.
    - Both "done" and "cancelled" count as resolved for dependency purposes.
      A cancelled prerequisite doesn't leave its dependents blocked forever -
      if the prerequisite isn't happening, there's nothing left to wait for.
    - There's no destructive delete for anything with a dependent. Deleting a
      ticket other tickets depend on would leave a dangling reference with no
      warning, so it's refused unless you pass --force. Use 'cancelled' for
      abandoned work; reach for 'delete' only for genuine mistakes (test
      tickets, duplicates) that nothing else points to.
    - Every edit that could change the dependency graph (status change,
      --add-depends, --remove-depends) reports what it affected: whether the
      ticket you just edited became blocked or unblocked itself, and whether
      any tickets that depend on it did too, in either direction.
    - Adding a dependency is checked for cycles before it's saved. A cycle
      would create a ticket that can never become ready, silently, and
      wouldn't show up as an error anywhere else.
    - Ticket IDs are stable strings (PERS-001, WORK-004), not titles, so
      dependency references survive a rename and don't rely on fuzzy matching.
    - A new ID is claimed with an exclusive create before the ticket is written,
      and that claim is kept forever in <store>/.ids. Working out "the next
      number" and then writing the file are two steps, and two sessions running
      them at the same moment both picked the same number, losing one ticket.
      Keeping the claim also stops a number being reissued after its ticket is
      deleted, which used to hand an unrelated new ticket the old one's
      dependents. Gaps in the numbering are harmless; reuse isn't.
    - Reads retry briefly before giving up. Windows fails an open that lands in
      the middle of another process's rename; the same read a moment later is
      fine, so a transient collision shouldn't surface as a crash.
    - Writes are atomic (write to a temp file, then os.replace into place), so
      a crash or a second process reading mid-write never sees a half file.
      The temp file lives in <store>/.tmp, deliberately NOT in <store>/tickets.
      It used to, and that was enough to break the store two different ways:
      a temp file left by a crashed write made every later command fail with a
      decode error, and on Windows a second process reading the folder hit the
      first one's open handle. Only files named like a ticket ID are loaded, so
      anything else in that folder is ignored rather than parsed and crashed on.
    - Each store (e.g. "personal", "work") is just a folder. There's no shared
      database and no daemon, so two stores can never leak into each other.
    - A store is never created by accident. Every command except 'init' stops
      if --store isn't an existing store, because the folder boundary is what
      separates personal from work data, and a path typo that silently created
      a third empty store made that boundary meaningless.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Only files named like a ticket ID are treated as tickets. Anything else in
# the folder - scratch files, notes, editor droppings - is ignored rather than
# parsed and crashed on. This has to accept every prefix id_prefix can produce,
# or the loader silently drops real tickets: the two are kept in step by
# id_prefix stripping anything that isn't alphanumeric.
TICKET_FILENAME = re.compile(r"^[A-Za-z0-9]+-\d+\.json$")

# How many numbers to walk past before giving up on allocating an ID. Only
# reached if that many claims are held or stale at once.
MAX_ID_ATTEMPTS = 50

# Retries for a read that collides with another process's write.
READ_RETRIES = 5
READ_RETRY_DELAY = 0.05

# Serialising edits. An update reads the whole store, changes one ticket and
# writes it back; two of those at once lose one edit. The lock is a file in
# <store>/.tmp, so it works across processes without a daemon.
LOCK_NAME = "update.lock"
LOCK_TIMEOUT = 10.0
LOCK_POLL = 0.05
LOCK_STALE_AFTER = 60.0

# Width of the source column in list/ready/blocked output.
SOURCE_WIDTH = 12

# Per-project marker naming which store applies. Read by Claude Code as well.
SCOPE_MARKER = ".ticket-scope"
SCOPE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# Stores live beside this script: <here>/personal, <here>/work.
STORES_ROOT = Path(__file__).resolve().parent

STATUSES = ["open", "in_progress", "done", "cancelled"]
RESOLVED_STATUSES = ("done", "cancelled")
PRIORITIES = ["low", "medium", "high", "critical"]
PRIORITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def use_utf8_output() -> None:
    """Print UTF-8 whatever the console claims to be.

    Every file open in here specifies utf-8, but stdout was left on the system
    default. Piped output on Windows lands on cp1252, so one accented character
    in a title crashed 'list' and 'ready' for the whole store, and made 'create'
    exit 1 having already written the ticket - the command looked like it had
    failed when it hadn't. That is precisely the failure that killed the
    previous tool. errors='replace' means a stream that can't be reconfigured
    degrades to a visible '?' rather than stopping the command."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_time(iso, fmt: str = "%Y-%m-%d") -> str:
    """A stored timestamp shown in the reader's own timezone.

    Timestamps are stored in UTC so they're unambiguous and sort correctly.
    Printing them raw is wrong by the local offset, and for anything created
    late in the evening that's wrong by a whole day - which is the one thing a
    date is meant to get right. A timestamp with no offset is read as UTC,
    since that's what this tool writes. Anything unparseable prints as '?'
    rather than stopping the command."""
    try:
        when = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "?"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime(fmt)


def id_number(ticket_id: str) -> int:
    """Numeric part of an ID, for sorting. As plain text PERS-1000 comes
    before PERS-999, which is only invisible while the numbers are all the
    same width."""
    try:
        return int(ticket_id.split("-")[-1])
    except ValueError:
        return 0


def split_list(value) -> list:
    """Comma-separated input to a clean list.

    Empty segments are dropped: 'a,,b' used to store an empty string, which
    then read as a dependency that could never be found."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def display_source(ticket: dict) -> str:
    """Source for a fixed-width column, or '-' when the ticket predates the
    field. Deliberately plain ASCII: the console here isn't reliably UTF-8,
    and a character that can't be encoded would stop the command rather than
    decorate it."""
    source = (ticket.get("source") or "").strip()
    if not source:
        return "-"
    if len(source) > SOURCE_WIDTH:
        return source[:SOURCE_WIDTH - 1] + "~"
    return source


def find_scope_marker(start: Path):
    """The nearest .ticket-scope walking up from start, or None.

    Walking up rather than checking only the current folder means the tool
    works from anywhere inside a project, not just its root."""
    start = Path(start).resolve()
    for folder in [start, *start.parents]:
        marker = folder / SCOPE_MARKER
        if marker.is_file():
            return marker
    return None


def read_scope_marker(marker: Path) -> tuple:
    """(scope, source) from a marker file. Blank lines are ignored.

    Line one is the store name. An optional line two names the project for the
    source field, and exists because the two aren't always the same folder: a
    project that splits its work across scopes puts a marker in each subfolder,
    so the marker sits in 'general' or 'company' and that is a useless answer
    to "which project is this". Line two lets the project say what it's called.

    The source is a label, not a path, so spaces and punctuation are fine. The
    scope is checked by the caller, since it decides which store gets written."""
    lines = [line.strip() for line in marker.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    scope = lines[0] if lines else ""
    source = lines[1] if len(lines) > 1 else ""
    return scope, source


def resolve_store(start: Path, root: Path):
    """The store named by the nearest .ticket-scope, or None if there isn't one.

    The marker holds a bare scope name - 'personal' or 'work' - which is joined
    onto root. Anything that isn't a single plain name is refused rather than
    resolved: this is the personal/work boundary, and a marker that could climb
    out of the store root with '..' would make it meaningless."""
    marker = find_scope_marker(start)
    if marker is None:
        return None
    scope, _ = read_scope_marker(marker)
    if not SCOPE_NAME.match(scope):
        raise ValueError(
            f"{marker} does not contain a usable scope name.\n"
            f"Expected a single word such as 'personal' or 'work', "
            f"found: {scope!r}")
    return Path(root) / scope


def default_source(start: Path) -> str:
    """Project a ticket came from.

    The marker's second line if it names one, otherwise the folder holding the
    marker, otherwise wherever the command was run."""
    marker = find_scope_marker(start)
    if marker is None:
        return Path(start).resolve().name
    _, source = read_scope_marker(marker)
    return source or marker.parent.name


def tickets_dir(store: Path) -> Path:
    return store / "tickets"


def require_store(store: Path) -> None:
    """Stop unless this really is an existing store.

    This used to create the folder on demand, which meant a typo in --store
    quietly produced a brand new empty store and reported success. The two
    stores are a compliance boundary - "is this company data" is answered by
    the path - and that only holds if a wrong path fails instead of inventing
    somewhere new to write."""
    if tickets_dir(store).is_dir():
        return
    if store.exists():
        problem = f"Not a ticket store: {store} exists but has no 'tickets' folder."
    else:
        problem = f"No such folder: {store}"
    print(f"{problem}\n"
          f"Check the path for a typo. To deliberately create a store there:\n"
          f"    python next_task.py --store {store} init", file=sys.stderr)
    sys.exit(2)


def temp_dir(store: Path) -> Path:
    """Scratch space for in-progress writes, deliberately NOT inside tickets/.

    A half-written file sitting in the folder every command globs is enough to
    take down the whole store, and on Windows another process holding one open
    fails the read outright. Keeping them out of that folder means a concurrent
    reader physically cannot see them."""
    d = store / ".tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def store_lock(store: Path, timeout: float = None):
    """Hold the store while a ticket is read, changed and written back.

    Without this, two sessions editing the same ticket at the same moment both
    read the old copy and the second write silently discards the first edit.
    The lock is an exclusive file create, which is the one thing the filesystem
    will only let one process win.

    A lock left behind by a process that was killed mid-edit is ignored once
    it's old enough, so a crash can't wedge the store permanently."""
    if timeout is None:
        timeout = float(os.environ.get("TICKETS_LOCK_TIMEOUT", LOCK_TIMEOUT))
    lock = temp_dir(store) / LOCK_NAME
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_AFTER:
                    lock.unlink()
                    continue
            except OSError:
                pass          # it went away on its own; try again
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Gave up waiting for the store lock after {timeout:g}s: {lock}\n"
                    f"Another session is probably mid-edit. If nothing else is "
                    f"running, delete that file.")
            time.sleep(LOCK_POLL)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def read_ticket(path: Path) -> dict:
    """Read one ticket, retrying briefly if the file is momentarily unreadable.

    On Windows an open that lands in the middle of another process's
    os.replace fails with a sharing violation. It's transient, and the rename
    is atomic either way, so a retry sees one complete file or the other. A
    failure that outlives the retries is real and propagates."""
    for attempt in range(READ_RETRIES):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except OSError:
            if attempt == READ_RETRIES - 1:
                raise
            time.sleep(READ_RETRY_DELAY)


def load_store(store: Path) -> tuple:
    """Every ticket that could be read, plus a note about each one that couldn't.

    A single unreadable file used to end every command in the store with a
    traceback, including 'verify' - the one command whose job is to find files
    like that. Nothing is silently dropped: what can't be read is returned so
    callers can say so."""
    tickets = {}
    problems = []
    from_file = {}
    for f in sorted(tickets_dir(store).glob("*.json")):
        if not TICKET_FILENAME.match(f.name):
            continue
        try:
            t = read_ticket(f)
        except json.JSONDecodeError as exc:
            problems.append(f"{f.name} is not valid JSON: {exc}")
            continue
        except OSError as exc:
            problems.append(f"{f.name} could not be read: {exc}")
            continue
        if not isinstance(t, dict):
            problems.append(f"{f.name} does not contain a ticket")
            continue
        if not t.get("id"):
            problems.append(f"{f.name} has no ticket id")
            continue
        tid = t["id"]
        if f.stem != tid:
            problems.append(f"{f.name} contains id {tid} - the filename and the id "
                            f"inside must match, or an edit writes to a different "
                            f"file than the one it read")
        if tid in from_file:
            # Keep the first in sorted order, so which one wins is at least
            # predictable. Letting the later file overwrite it made a real
            # ticket vanish from every command while verify still said "OK".
            problems.append(f"{f.name} and {from_file[tid]} both claim {tid}; "
                            f"only {from_file[tid]} is being used")
            continue
        from_file[tid] = f.name
        tickets[tid] = t
    return tickets, problems


def load_all_tickets(store: Path) -> dict:
    tickets, problems = load_store(store)
    if problems:
        print(f"Warning: {len(problems)} problem(s) with the files in "
              f"{tickets_dir(store)}; some tickets may not be shown. "
              f"Run 'verify' on this store for detail.", file=sys.stderr)
    return tickets


def id_prefix(store: Path) -> str:
    """First four usable characters of the folder name: personal -> PERS.

    Anything that isn't a letter or digit is dropped, so the result always
    matches TICKET_FILENAME. A prefix the loader won't accept would make every
    ticket in the store invisible."""
    usable = re.sub(r"[^A-Za-z0-9]", "", store.name)[:4]
    return (usable or "TICK").upper()


def ids_dir(store: Path) -> Path:
    """Durable record of every ID this store has ever handed out, one empty
    file per ID.

    Kept forever, deliberately. Without it, deleting the highest-numbered
    ticket freed its number, and the next unrelated ticket created silently
    inherited whatever still pointed at the old one. It doubles as the
    allocation lock, since an exclusive create can only be won by one process.

    Seeded from the tickets already on disk the first time it's needed, so a
    store that predates this is protected immediately rather than after its
    next create."""
    d = store / ".ids"
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        for tid in load_all_tickets(store):
            (d / tid).touch(exist_ok=True)
    return d


def next_number(store: Path, tickets: dict, prefix: str) -> int:
    """One past the highest number ever used, whether or not its ticket
    still exists."""
    nums = []
    for tid in list(tickets) + [p.name for p in ids_dir(store).iterdir()]:
        if tid.startswith(prefix + "-"):
            try:
                nums.append(int(tid.split("-")[-1]))
            except ValueError:
                pass
    return (max(nums) + 1) if nums else 1


def claim_id(store: Path, tickets: dict) -> tuple:
    """Reserve the next free ticket ID against every other process.

    Working out the next number and then writing the file are two steps, and
    two processes that run them at the same time both pick the same number -
    one then overwrites the other, or fails outright doing it. So the number is
    claimed first, with an O_EXCL create that only one process can win.

    The claim is the entry in <store>/.ids, and it is never removed - see
    ids_dir. It lives there rather than in tickets/ for the same reason temp
    files do: a placeholder sitting in the globbed folder would be read as a
    half-written ticket."""
    prefix = id_prefix(store)
    n = next_number(store, tickets, prefix)
    for _ in range(MAX_ID_ATTEMPTS):
        tid = f"{prefix}-{n:03d}"
        try:
            fd = os.open(str(ids_dir(store) / tid), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return tid
    raise RuntimeError(
        f"Could not allocate a ticket ID after {MAX_ID_ATTEMPTS} attempts. "
        f"Every number tried is already recorded in {ids_dir(store)}."
    )


def atomic_write(path: Path, data: dict, tmp_dir: Path) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=str(tmp_dir), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_ticket(store: Path, ticket: dict) -> None:
    atomic_write(tickets_dir(store) / f"{ticket['id']}.json", ticket, temp_dir(store))


def unresolved_deps(ticket: dict, tickets: dict) -> list:
    return [d for d in ticket.get("depends_on", [])
            if d in tickets and tickets[d]["status"] not in RESOLVED_STATUSES]


def missing_deps(ticket: dict, tickets: dict) -> list:
    return [d for d in ticket.get("depends_on", []) if d not in tickets]


def blocking_deps(ticket: dict, tickets: dict) -> list:
    """Everything standing between this ticket and being workable: prerequisites
    that aren't finished, plus prerequisites that can't be found at all.

    A reference to a ticket that doesn't exist is unknown, not done. Counting it
    as satisfied put tickets whose prerequisite had been deleted straight into
    'ready', which is the one place you least want a wrong answer."""
    blocking = set(unresolved_deps(ticket, tickets)) | set(missing_deps(ticket, tickets))
    return [d for d in ticket.get("depends_on", []) if d in blocking]


def describe_blockers(ticket: dict, tickets: dict) -> list:
    """blocking_deps for display, with the missing ones called out."""
    missing = set(missing_deps(ticket, tickets))
    return [f"{d} (missing)" if d in missing else d
            for d in blocking_deps(ticket, tickets)]


def is_blocked(ticket: dict, tickets: dict) -> bool:
    return bool(blocking_deps(ticket, tickets))


def dependents_of(ticket_id: str, tickets: dict) -> list:
    return [t for t in tickets.values() if ticket_id in t.get("depends_on", [])]


def waits_on(start: str, target: str, tickets: dict) -> bool:
    """Does start wait on target, directly or through any chain of others?"""
    visited = set()
    stack = list(tickets.get(start, {}).get("depends_on", []))
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        stack.extend(tickets.get(current, {}).get("depends_on", []))
    return False


def creates_cycle(ticket_id: str, new_dep_id: str, tickets: dict) -> bool:
    """Would ticket_id depending on new_dep_id create a circular chain?"""
    return new_dep_id == ticket_id or waits_on(new_dep_id, ticket_id, tickets)


def tickets_in_cycles(tickets: dict) -> list:
    """Tickets that end up waiting on themselves.

    Every one of these is unreachable forever, and no other command says so:
    'ready' simply leaves them out, and 'blocked' shows them waiting on each
    other without pointing out that the wait can never end."""
    return sorted(tid for tid in tickets if waits_on(tid, tid, tickets))


def cmd_init(args, store: Path) -> None:
    d = tickets_dir(store)
    if d.is_dir():
        print(f"Already a ticket store: {store}")
        return
    d.mkdir(parents=True)
    print(f"Created ticket store: {store}")


def cmd_create(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    depends_on = split_list(args.depends_on)
    tid = claim_id(store, tickets)
    # A ticket can point at an ID that doesn't exist yet, so the number being
    # issued now may already be spoken for further up a chain.
    for d in list(depends_on):
        if creates_cycle(tid, d, tickets):
            print(f"Skipped: depending on {d} would create a circular dependency",
                  file=sys.stderr)
            depends_on.remove(d)
    unknown = [d for d in depends_on if d not in tickets]
    if unknown:
        print(f"Warning: unknown dependency ID(s): {', '.join(unknown)}. "
              f"Saved anyway, but the new ticket counts as blocked until those "
              f"exist. Fix with 'update' if an ID is wrong.", file=sys.stderr)
    ticket = {
        "id": tid,
        "title": args.title,
        "description": args.desc or "",
        "status": "open",
        "priority": args.priority,
        "tags": split_list(args.tags),
        "source": args.source.strip() if args.source else default_source(Path.cwd()),
        "depends_on": depends_on,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    save_ticket(store, ticket)
    print(f"Created {tid}: {args.title}")


def cmd_list(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    rows = list(tickets.values())
    if args.status:
        # Asking for a status is explicit enough on its own. Applying the
        # hide-finished default first made '--status done' always print
        # "No tickets.", with no hint that --all was also needed.
        rows = [t for t in rows if t["status"] == args.status]
    elif not args.all:
        rows = [t for t in rows if t["status"] not in RESOLVED_STATUSES]
    rows.sort(key=lambda t: id_number(t["id"]))
    if not rows:
        print("No tickets.")
        return
    for t in rows:
        flag = ""
        if t["status"] not in RESOLVED_STATUSES and is_blocked(t, tickets):
            flag += " [BLOCKED]"
        miss = missing_deps(t, tickets)
        if miss:
            flag += f" [MISSING DEP: {', '.join(miss)}]"
        print(f"{t['id']:<10} {local_time(t.get('created_at')):<11} {display_source(t):<13} {t['status']:<12} {t['priority']:<8} {t['title']}{flag}")


def cmd_show(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    t = tickets.get(args.id)
    if not t:
        print(f"No such ticket: {args.id}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(t, indent=2, ensure_ascii=False))
    stamp = "%Y-%m-%d %H:%M"
    print(f"\nCreated {local_time(t.get('created_at'), stamp)}, "
          f"last updated {local_time(t.get('updated_at'), stamp)} (local time)")
    deps = describe_blockers(t, tickets)
    miss = missing_deps(t, tickets)
    dependents = [d["id"] for d in dependents_of(t["id"], tickets)]
    if deps:
        print(f"\nBlocked on: {', '.join(deps)}")
    if miss:
        print(f"Warning: depends on unknown ticket(s): {', '.join(miss)}")
    if dependents:
        print(f"Depended on by: {', '.join(dependents)}")


def cmd_update(args, store: Path) -> None:
    # Everything from here to the last write is one edit: read, change,
    # write back. See store_lock for why it can't be interleaved.
    with store_lock(store):
        _update_locked(args, store)


def _update_locked(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    t = tickets.get(args.id)
    if not t:
        print(f"No such ticket: {args.id}", file=sys.stderr)
        sys.exit(1)

    before_self_blocked = is_blocked(t, tickets)
    dependents_before = dependents_of(t["id"], tickets)
    before_dep_blocked = {d["id"]: is_blocked(d, tickets) for d in dependents_before}

    if args.status:
        t["status"] = args.status
    if args.title:
        t["title"] = args.title
    if args.desc is not None:
        t["description"] = args.desc
    if args.priority:
        t["priority"] = args.priority
    if args.tags is not None:
        t["tags"] = split_list(args.tags)
    if args.source is not None:
        t["source"] = args.source.strip()
    # A hand-edited or older ticket may have no depends_on at all; everywhere
    # else reads it defensively, so don't crash here alone.
    t.setdefault("depends_on", [])
    if args.add_depends:
        for d in split_list(args.add_depends):
            if d in t["depends_on"]:
                continue
            if d == t["id"]:
                print(f"Skipped: a ticket can't depend on itself", file=sys.stderr)
                continue
            if creates_cycle(t["id"], d, tickets):
                print(f"Skipped: adding {d} would create a circular dependency", file=sys.stderr)
                continue
            t["depends_on"].append(d)
    if args.remove_depends:
        for d in split_list(args.remove_depends):
            if d in t["depends_on"]:
                t["depends_on"].remove(d)

    t["updated_at"] = now_iso()
    save_ticket(store, t)
    print(f"Updated {t['id']}")

    refreshed = load_all_tickets(store)
    after_self_blocked = is_blocked(refreshed[t["id"]], refreshed)

    if after_self_blocked and not before_self_blocked:
        deps_now = unresolved_deps(refreshed[t["id"]], refreshed)
        print(f"Note: {t['id']} is now blocked on: {', '.join(deps_now)}")
    elif before_self_blocked and not after_self_blocked:
        print(f"Note: {t['id']} is no longer blocked")

    unblocked, reblocked = [], []
    for dep_id, was_blocked in before_dep_blocked.items():
        dep_ticket = refreshed.get(dep_id)
        if not dep_ticket:
            continue
        if dep_ticket["status"] in RESOLVED_STATUSES:
            continue      # nobody cares that a finished ticket became workable
        now_blocked = is_blocked(dep_ticket, refreshed)
        if was_blocked and not now_blocked:
            unblocked.append(dep_id)
        elif not was_blocked and now_blocked:
            reblocked.append(dep_id)
    if unblocked:
        print(f"Unblocked: {', '.join(unblocked)}")
    if reblocked:
        print(f"Re-blocked: {', '.join(reblocked)}")


def cmd_delete(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    t = tickets.get(args.id)
    if not t:
        print(f"No such ticket: {args.id}", file=sys.stderr)
        sys.exit(1)
    deps = dependents_of(t["id"], tickets)
    if deps and not args.force:
        dep_ids = ", ".join(d["id"] for d in deps)
        print(f"Refusing to delete {t['id']}: {dep_ids} depend(s) on it.\n"
              f"Use --force to delete anyway (leaves a dangling reference, 'verify' will catch it),\n"
              f"or consider 'update {t['id']} --status cancelled' instead.", file=sys.stderr)
        sys.exit(1)
    # Make sure the ledger exists before the ticket goes: it's what stops this
    # number being handed out again to something unrelated.
    ids_dir(store)
    (tickets_dir(store) / f"{t['id']}.json").unlink()
    print(f"Deleted {t['id']}")


def cmd_ready(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    rows = [t for t in tickets.values() if t["status"] in ("open", "in_progress") and not is_blocked(t, tickets)]
    rows.sort(key=lambda t: (-PRIORITY_RANK.get(t["priority"], 1), id_number(t["id"])))
    if not rows:
        print("Nothing ready.")
        return
    for t in rows:
        print(f"{t['id']:<10} {local_time(t.get('created_at')):<11} {display_source(t):<13} {t['priority']:<8} {t['title']}")


def cmd_blocked(args, store: Path) -> None:
    tickets = load_all_tickets(store)
    rows = [t for t in tickets.values() if t["status"] not in RESOLVED_STATUSES and is_blocked(t, tickets)]
    if not rows:
        print("Nothing blocked.")
        return
    for t in rows:
        print(f"{t['id']:<10} {local_time(t.get('created_at')):<11} {display_source(t):<13} {t['title']:<40} blocked on: {', '.join(describe_blockers(t, tickets))}")


def cmd_verify(args, store: Path) -> None:
    # load_store rather than load_all_tickets: reporting these IS the job here,
    # so the generic "run verify" warning would just be noise.
    tickets, problems = load_store(store)
    for t in tickets.values():
        miss = missing_deps(t, tickets)
        if miss:
            problems.append(f"{t['id']} depends on missing ticket(s): {', '.join(miss)}")
    looped = tickets_in_cycles(tickets)
    if looped:
        problems.append(f"circular dependencies - these can never become ready: "
                        f"{', '.join(looped)}")
    if not problems:
        print(f"OK - {len(tickets)} tickets, no dangling references.")
        return
    print(f"Found {len(problems)} issue(s) across {len(tickets)} readable ticket(s):")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)      # so verify can be chained, or run from a hook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal local ticket tracker with dependency-aware readiness.")
    parser.add_argument("--store", help="Path to the ticket store folder. Optional: "
                                        f"defaults to the store named by the nearest {SCOPE_MARKER} file.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create a new, empty ticket store at --store")

    p_create = sub.add_parser("create", help="Create a new ticket")
    p_create.add_argument("title")
    p_create.add_argument("--desc")
    p_create.add_argument("--depends-on", help="Comma-separated ticket IDs this depends on")
    p_create.add_argument("--priority", choices=PRIORITIES, default="medium")
    p_create.add_argument("--tags", help="Comma-separated tags")
    p_create.add_argument("--source", help="Project this came from; defaults to the current folder name")

    p_list = sub.add_parser("list", help="List tickets")
    p_list.add_argument("--status", choices=STATUSES)
    p_list.add_argument("--all", action="store_true", help="Include done/cancelled tickets")

    p_show = sub.add_parser("show", help="Show full detail for one ticket")
    p_show.add_argument("id")

    p_update = sub.add_parser("update", help="Update a ticket")
    p_update.add_argument("id")
    p_update.add_argument("--status", choices=STATUSES)
    p_update.add_argument("--title")
    p_update.add_argument("--desc")
    p_update.add_argument("--priority", choices=PRIORITIES)
    p_update.add_argument("--tags", help="Comma-separated tags; replaces the existing set")
    p_update.add_argument("--source")
    p_update.add_argument("--add-depends")
    p_update.add_argument("--remove-depends")

    p_delete = sub.add_parser("delete", help="Delete a ticket (refused if anything depends on it, unless --force)")
    p_delete.add_argument("id")
    p_delete.add_argument("--force", action="store_true")

    sub.add_parser("ready", help="Show what's open/in-progress and has no unresolved dependencies")
    sub.add_parser("blocked", help="Show what's blocked and what it's waiting on")
    sub.add_parser("verify", help="Check the whole store for dangling dependency references")

    return parser


def main() -> None:
    use_utf8_output()
    args = build_parser().parse_args()
    if args.store:
        store = Path(args.store)
    else:
        try:
            store = resolve_store(Path.cwd(), STORES_ROOT)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            sys.exit(2)
        if store is None:
            print(f"No --store given, and no {SCOPE_MARKER} file in this folder "
                  f"or any parent.\nEither pass --store, or create a "
                  f"{SCOPE_MARKER} file containing 'personal' or 'work'.",
                  file=sys.stderr)
            sys.exit(2)
    if args.command != "init":
        require_store(store)


    commands = {
        "init": cmd_init,
        "create": cmd_create,
        "list": cmd_list,
        "show": cmd_show,
        "update": cmd_update,
        "delete": cmd_delete,
        "ready": cmd_ready,
        "blocked": cmd_blocked,
        "verify": cmd_verify,
    }
    try:
        commands[args.command](args, store)
    except TimeoutError as exc:
        # Waiting on another session isn't a crash; say so without a traceback.
        print(exc, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
