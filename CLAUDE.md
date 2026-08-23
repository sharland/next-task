# next-task

Local, dependency-aware ticket tracker (`next_task.py`) plus a read-only Flask
dashboard (`dashboard.py`). Two independent stores, `personal/` and `work/`,
one JSON file per ticket. No database, no daemon, no cloud service.

The folder sits inside a Dropbox folder, so basic backup and version history come
from that. Worth knowing before changing how files are written: `.tmp/`, `.ids/`
and the lock file all get synced too, which is harmless — a lock older than a
minute is ignored, and a synced `.ids/` entry only ever prevents ID reuse.

```
next_task.py                the CLI
dashboard.py              web view, imports next_task.py directly; one guarded delete route
dashboard_launch.py       starts the dashboard if it isn't already running
next_task_mcp.py          MCP server: lets Claude app Projects file tickets
test_next_task.py           stdlib unittest suite
README.md                 public-facing: what it is and how to run it
LICENSE                   MIT; named explicitly in .gitignore
CLAUDE.md                 this file: the current source of truth
.gitignore                deny-by-default; see Repository policy
skills/next-task-setup/   Claude Code skill: create a project's .ticket-scope
docs/BACKLOG.md           engineering work on the tool itself
docs/claude-project-instructions.md  template for wiring up a Claude Project
docs/example-ticket.json  committed sample of the on-disk format
personal/tickets/         PERS-*.json
work/tickets/             WORK-*.json
<store>/.tmp/             in-progress writes and the update lock
<store>/.ids/             one empty file per ID ever issued; never pruned
```

## Non-negotiable invariants

Each of these is a reaction to something that actually went wrong. Don't
relax one without reading why it's here.

- **`next_task.py` stays stdlib-only.** A previous third-party tool had an
  unconditional `import fcntl` (Unix-only, crashed every command on Windows)
  and a UTF-8 decode failure that hard-blocked ticket creation rather than
  degrading. If a feature seems to need a dependency, reconsider the feature.
  Two files beside it are allowed one each, because neither sits in the data
  path: `dashboard.py` uses Flask, and `next_task_mcp.py` uses the MCP SDK. The
  rule is the file, not the repository — a dependency in `next_task.py` itself
  is what must never happen.
- **`next_task_mcp.py` shells out to the CLI rather than importing its write
  logic.** The dashboard imports functions for its read paths; anything that
  writes goes through the real command instead. The MCP server writes, and the
  printed notices — `Unblocked: X`, the cycle refusals, the unknown-dependency
  warning — are part of the answer. Running the real command and returning its
  words verbatim means there is no second copy of the rules to drift, and the
  store lock and ID ledger apply automatically. The dashboard's delete route
  follows the same rule: the deletion itself is `next_task.py delete` run as a
  subprocess, never a reimplementation.
- **`scope` is a required MCP argument with no default.** On disk, which store
  a ticket lands in is decided by a path. Through MCP it's decided by an
  argument a Project's instructions supply, which is weaker — so every tool
  demands it explicitly and every reply names the store it used. A default here
  would quietly undo the reason the stores are separate folders.
- **Every file open specifies `encoding="utf-8"`, and so does stdout.**
  Same incident. Windows otherwise defaults to the system codepage. The file
  half was right from the start; the output half was missed until a test with
  an accented title found it. Piped output — which is how Claude Code runs
  this — lands on cp1252, so one `£` or curly quote in a title crashed `list`
  and `ready` for the whole store, and made `create` exit 1 after it had
  already written the ticket. `use_utf8_output()` runs before anything else in
  `main`.
- **`source` records which project folder a ticket came from.** It defaults
  to the name of the folder the command was run in, since other projects call
  this tool from their own root, and `--source` overrides it. Tickets created
  before the field existed have none and display as `-`; nothing was
  backfilled, deliberately, rather than guessing at real data.
- **Timestamps are stored in UTC and displayed in local time.** Storing UTC
  keeps them unambiguous and correctly sortable; printing them raw is wrong by
  the local offset, and for anything created late in the evening it names the
  wrong day. Everything on-screen goes through `local_time`.
- **"Blocked" is never stored.** It's computed from `depends_on` on every read,
  so it cannot drift out of sync. Completing or un-completing a dependency
  instantly changes what's blocked, with nothing to remember to update. Do not
  add a cached or stored status field.
- **A prerequisite that can't be found blocks.** A reference to a ticket that
  no longer exists is unknown, not finished. It used to count as satisfied,
  which put tickets whose prerequisite had been deleted straight into `ready` —
  a wrong answer in the one place it matters most. `blocking_deps` is the
  single source of truth; `unresolved_deps` and `missing_deps` are its parts.
  Both `done` and `cancelled` still count as settled, so abandoned work never
  blocks its dependents forever.
- **`personal/` and `work/` stay separate folders, not one store with a tag.**
  This is a compliance boundary, not a UX preference. The work store holds real
  company governance content. "Which of these is company data" must be answerable by
  pointing at a path, not by trusting every ticket got tagged correctly.
- **`dashboard.py` has exactly one write route, and it can only remove
  finished tickets.** `POST /delete` exists so old done/cancelled tickets can
  be cleared from the Done / cancelled tab (decided 2026-08-23, deliberately
  relaxing the previous "no write routes at all" rule). Its guards are the
  invariant now: the done/cancelled status is checked against what's on disk,
  never trusted from the browser; the deletion shells out to the CLI's
  `delete`, so its dependents guard applies — a missing prerequisite counts as
  blocking, and deleting one would re-block live work; and the route accepts
  JSON only, which is the CSRF guard, because a cross-site form can POST to
  localhost but a cross-site JSON request needs a preflight this app never
  answers. Everything else serves GET and nothing but GET — a test asserts
  exactly that boundary. It still imports `is_blocked` / `blocking_deps` /
  `missing_deps` from `next_task.py` rather than reimplementing them, so it
  can never disagree with the CLI, and it binds `127.0.0.1` only. Sorting,
  filtering, tabs and description unfurling run in the browser on rows
  already sent.
- **Nothing transient ever lives in `<store>/tickets/`.** Temp files and ID
  claims go in `<store>/.tmp/`, and only files named like a ticket ID are
  loaded. A temp file in that folder used to break every command in the store,
  and on Windows it also failed concurrent readers outright. `TICKET_FILENAME`
  and `id_prefix` must stay in step — a prefix the filter rejects makes every
  ticket in that store invisible, and IDs then get handed out twice.
- **A new ID is claimed with an exclusive create before the ticket is
  written, and the claim is never released.** Computing "the next number" and
  writing the file are two steps; two sessions running them at once both picked
  the same number and one ticket was lost. `<store>/.ids/` holds one empty file
  per ID ever issued, which is both that lock and the reason a number is never
  reissued — deleting the highest-numbered ticket used to free its number, and
  the next new ticket silently inherited whatever still pointed at the old one.
  Do not prune it: gaps in the numbering are harmless, reuse is not.
- **Reads retry briefly before failing.** Windows rejects an open that lands
  mid-rename. The retry turns a transient collision into a non-event.
- **An edit holds a store-level lock.** `update` reads the whole store,
  changes one ticket and writes it back; two sessions doing that at the same
  moment both read the old copy and the second write discarded the first. The
  lock is an exclusive file create in `<store>/.tmp`, and one left by a killed
  process is ignored once it's a minute old, so a crash can't wedge the store.
  Waiting too long is reported as a message, not a traceback.
- **`verify` fails when it finds something.** It exits 1 on dangling
  references, unreadable files, dependency loops, or a filename that disagrees
  with the id inside it, so it can be chained or run from a hook. Two of those
  are silent otherwise: every ticket in a loop is unreachable forever while
  `ready` just omits them, and two files claiming one id made a real ticket
  vanish from every command while `verify` still reported "OK". Where ids
  collide the first file in sorted order wins, so which one survives is at
  least predictable.
- **One unreadable file never stops a command.** `load_store` returns what it
  could read plus a note on each file it couldn't; `load_all_tickets` warns on
  stderr and carries on; `verify` reports the detail. A corrupt or truncated
  file used to end every command in the store with a traceback, `verify`
  included — the one command whose job was to find it. Nothing is dropped
  silently: the dashboard shows a banner for the same reason.
- **A store is never created by accident.** Every command except `init` stops
  if `--store` isn't an existing store. The folder boundary is what separates
  personal from work data, and it means nothing if a typo can quietly create a
  third empty store and report success.

## Running it

    python next_task.py [--store <path>] init
    python next_task.py [--store <path>] create "title" [--desc D] [--priority P] [--tags a,b] [--source S] [--depends-on ID,ID]
    python next_task.py [--store <path>] list [--status S] [--all]
    python next_task.py [--store <path>] show ID
    python next_task.py [--store <path>] update ID [--status S] [--title T] [--desc D] [--priority P] [--tags a,b] [--source S] [--add-depends ID] [--remove-depends ID]
    python next_task.py [--store <path>] delete ID [--force]
    python next_task.py [--store <path>] ready
    python next_task.py [--store <path>] blocked
    python next_task.py [--store <path>] verify

`--store` is optional. Left out, the tool walks up from the current folder for a
`.ticket-scope` file and uses the store it names, so inside any project you can
just run `python /path/to/next-task/next_task.py ready`. Stores live beside
the script: `personal/` and `work/`.

Dashboard: `python dashboard.py`, then open <http://127.0.0.1:5000>. Optional
`--root` and `--port`. It shows every store found under root, where a store is
any subfolder containing a `tickets/` folder — so adding a third store needs no
code change here. The page is one tab per store (ready and blocked) plus a
shared Done / cancelled tab that merges the finished tickets of every store,
most recently touched first, with checkboxes and a delete button — the one
write the dashboard can do; see the invariant above. Columns sort on click
(numbers as numbers only when the whole value is numeric — a leading-prefix
parse once made every ISO timestamp equal to its year and the Created column
wouldn't sort), each tab has its own source filter, and a row with a
description unfurls it on click. The chosen tab and sort survive a refresh via
localStorage; nothing is remembered server-side. The filter only appears once
tickets in that tab actually have a source, so it stays hidden on stores of
older tickets.

It usually starts itself. A `SessionStart` hook in the global
`~/.claude/settings.json` runs `dashboard_launch.py`, which starts the server if
the port is free and opens one tab at that moment only — a session finding it
already running does nothing, so tabs don't accumulate. The launcher does
nothing at all outside a folder with a `.ticket-scope` marker, runs detached so
it can't hold up session start, and swallows every error, because a hook that
fails noisily at the start of every session in every project is worse than no
hook. The cost of that silence is that a broken hook and a working one look
identical: if the dashboard stops appearing, run `dashboard_launch.py` by hand
to see the real error.

## Tests

    python -m unittest test_next_task -v

Every test builds a throwaway store under the system temp directory. **Never
point a test at `personal/` or `work/`** — that's real data.

Any change to dependency or status logic must be exercised against the full
sequence create → block → complete → unblock → un-complete → re-block, plus
`verify`, before it's trusted on the real stores.

New work follows the pattern set in PERS-022: write the failing test first,
watch it fail for the right reason, then fix. Three separate concurrency races
were found that way, each one only visible after the previous was fixed, and
the stdout encoding bug was found the same way — by writing a test for the one
thing the project was founded on and discovering it had never been checked.

Keep a non-ASCII case in any test that touches output. `NonAsciiTest` is not
decoration.

## Repository policy

`.gitignore` is deny-by-default: everything is ignored, and the tool and its
docs are opted back in. Anything without a `.py` or `.md` extension has to be
named explicitly — `LICENSE` would otherwise have been left out of the repo
silently, which is the trap this shape of ignore file sets in exchange for
never leaking a store. This is deliberate. `dashboard.py` treats any folder
containing `tickets/` as a store and needs no code change to gain one, so an
ignore list written by store name would silently fail to cover the next store
added. Verify with `git status --short` before any first commit — it should
show the code, docs and `.gitignore`, and no `.json` from a store.

`README.md` is the public face: what the tool is, how to run it, and the design
reasoning in general terms. It deliberately names no employer, no personal
paths and no real ticket content, because it's the file a stranger reads first.
Keep it that way — the specifics belong here instead.

`docs/example-ticket.json` is the committed sample of the format. It lives in
`docs/` and **not** in an `examples/tickets/` folder, because the latter would
be discovered as a third store and rendered on the dashboard. `test_next_task.py`
asserts it carries exactly the fields a freshly created ticket has, so it can't
drift as fields are added.

Pushing to a remote is a separate decision, not a follow-on from `git init`.
Cloud hosting for the work store was already ruled out — company data in a
personal account that never went through the company's supplier approval — and that
reasoning doesn't change because the transport is git. The ignore file protects
against accident, not against `git add -f`, and history is permanent.

## How other projects invoke this

Routing lives in the global `~/.claude/CLAUDE.md`, not here. Other project
folders carry a `.ticket-scope` file whose first line is `personal` or `work`,
which tells Claude Code which store applies there. `next_task.py` reads the same
file, so `--store` is only needed to override it. A scope naming anything other
than a single plain word is refused rather than resolved — it decides which of
the two stores gets written to, so it must not be able to climb out with `..`.

The nearest marker wins, which lets one project split its work between stores by
putting a marker in each subfolder. That layout is in real use, and it has a
consequence: the marker is then in `general/` or `company/`, so the folder name
is a poor answer to "which project is this". An optional second line names the
project for the `source` field. It's a label rather than a path, so it isn't
constrained like the scope is.

Because of that layout, a missing marker doesn't always mean one is wanted.
Markers further down mean the absence higher up is deliberate, and adding one at
the top could route that project's work into the wrong store — which is the one
mistake the two-store split exists to prevent. The global instructions say to
check downwards before offering to create one.

`find_scope_marker` cannot make that distinction — it walks up only, so "markers
below" and "no markers at all" both come back as nothing. That's why the check
lives in `skills/next-task-setup/` as a procedure rather than in the code as a
rule. The working copy is installed at `~/.claude/skills/next-task-setup/`; the
copy here is the source of truth, and the two need keeping in step by hand.

## Open work

Engineering work on the tool lives in `docs/BACKLOG.md`, deliberately not in a
ticket store — those stores are the owner's own task list, not the tool's.
Anything that changes what the owner sees or decides goes in the store instead;
the test is whether they would ever act on the outcome.

`docs/BACKLOG.md` is where open internal work, deliberately-deferred ideas and
completed changes are recorded. Read it before proposing anything: several
obvious-looking improvements are already listed there as considered and left,
with the reasons. Don't assert here that there are no known problems — that
claim rots the moment someone uses the tool. The backlog is the answer.

This repository is public, so nothing committed here names the owner's employer,
their colleagues, their products, or local machine paths. Keep new material to
that standard: the reasoning is what's worth publishing, not who it was about
or where it happens to live on disk. Private working notes stay out entirely
rather than being edited down — `.gitignore` excludes the original handover on
that basis.
