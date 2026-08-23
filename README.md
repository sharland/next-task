# next-task

A small local ticket tracker that knows what you can actually work on next.

Tickets are plain JSON files in a folder. There's no database, no server to
keep running, no account, and nothing leaves your machine. The tracker itself
is one Python file with no dependencies at all.

The point of it is the dependency graph. Say a ticket depends on another one,
and it stops showing up in `ready` until that one is finished:

```
$ next_task.py --store ./notes ready
NOTE-001   2026-08-08  my-project    high     Write the migration script

$ next_task.py --store ./notes blocked
NOTE-002   2026-08-08  my-project    Run the migration in staging   blocked on: NOTE-001
NOTE-003   2026-08-08  my-project    Update the runbook             blocked on: NOTE-002

$ next_task.py --store ./notes update NOTE-001 --status done
Updated NOTE-001
Unblocked: NOTE-002

$ next_task.py --store ./notes ready
NOTE-002   2026-08-08  my-project    medium   Run the migration in staging
```

`ready` is the command you live in. It answers "what can I pick up right now",
and nothing appears there until everything it waits on is settled.

## Is this for you?

It might suit you if you want a task list that lives with your files, works
offline, is readable and editable by hand, and can express "not until that
other thing is done". It's built for one person across several projects.

It is **not** a replacement for a team issue tracker. There's no multi-user
story, no notifications, no comments, no attachments, no web API, and no
mobile app. If you need any of those, use a real one.

## Requirements

- **Python 3.7 or newer.** No third-party packages. `next_task.py` imports only
  the standard library, deliberately — see [Why it's built this way](#why-its-built-this-way).
- **Flask**, but only if you want the optional web dashboard:
  `pip install flask`. The command line never needs it.

Developed and used on Windows; nothing in it is Windows-specific, and several
of the sharper edges it works around are Windows-only problems.

## Getting started

There's nothing to install. Clone the repository, or just take `next_task.py` on
its own — it's self-contained.

Create a store. A store is simply a folder that holds tickets:

```bash
python next_task.py --store ./notes init
```

Add some work. The ID prefix comes from the store folder's name, so `notes`
gives you `NOTE-001`:

```bash
python next_task.py --store ./notes create "Write the migration script" --priority high --tags db
python next_task.py --store ./notes create "Run the migration in staging" --depends-on NOTE-001
```

Then ask what you can do:

```bash
python next_task.py --store ./notes ready
```

## Commands

```
next_task.py [--store <path>] init
next_task.py [--store <path>] create "title" [--desc D] [--priority P] [--tags a,b]
                                           [--source S] [--depends-on ID,ID]
next_task.py [--store <path>] list [--status S] [--all]
next_task.py [--store <path>] show ID
next_task.py [--store <path>] update ID [--status S] [--title T] [--desc D]
                                      [--priority P] [--tags a,b] [--source S]
                                      [--add-depends ID] [--remove-depends ID]
next_task.py [--store <path>] delete ID [--force]
next_task.py [--store <path>] ready
next_task.py [--store <path>] blocked
next_task.py [--store <path>] verify
```

Statuses are `open`, `in_progress`, `done`, `cancelled`. Priorities are `low`,
`medium`, `high`, `critical`, and `ready` is ordered by priority.

`verify` checks the whole store for problems and exits non-zero if it finds
any, so you can chain it or run it from a hook. It reports dangling references,
dependency loops, unreadable files, and files whose name disagrees with the ID
inside them.

## How dependencies work

- **"Blocked" is never stored.** It's worked out from the dependency graph
  every time you read, so it can't drift out of step with reality. Finish a
  prerequisite and its dependents become available instantly, with nothing to
  remember to update.
- **`done` and `cancelled` both count as settled.** Abandoned work doesn't leave
  its dependents stuck forever — that's what `cancelled` is for, rather than
  deleting a ticket other things point at.
- **A prerequisite that can't be found blocks.** If a ticket refers to an ID
  that doesn't exist, that's unknown, not finished, so the dependent stays out
  of `ready` and `verify` complains.
- **Loops are refused and reported.** You can't add a dependency that would
  create a circle, and `verify` finds any that got in by other means. Every
  ticket in a loop is unreachable forever, which is otherwise invisible.
- **Deleting something other tickets depend on is refused** unless you pass
  `--force`.

## The dashboard

Optional and localhost-only:

```bash
pip install flask
python dashboard.py
```

Then open <http://127.0.0.1:5000>. One tab per store shows what's ready and
what's blocked, and a shared Done / cancelled tab merges the finished tickets
of every store. Columns sort on click, each tab has a filter by source
project, a row with a description unfurls it when clicked, and the browser
remembers which tab and sort you were on. All of that runs in your browser on
rows already sent.

It has **one write route, and only one**: deleting finished tickets from the
Done / cancelled tab, for clearing out old history. It checks against the
files on disk that a ticket really is done or cancelled, and it runs the CLI's
own `delete` for the deletion itself, so the same guard applies — a ticket
something still depends on is refused. Everything else serves GET and nothing
but GET, which a test asserts. It imports its read logic from `next_task.py`
rather than reimplementing it, so it can never disagree with the command line
about what's blocked.

## Several stores

Point `--store` at different folders and you get completely independent sets of
tickets. They're separate directories with nothing shared between them — no
common index, no tags to get wrong.

That matters if some of your work is subject to rules the rest isn't. "Which of
these is which" is answered by a path, which is much harder to get wrong than
remembering to tag every ticket correctly. The dashboard treats any folder
containing a `tickets/` subfolder as a store, so adding another needs no
configuration.

## Using it with Claude Code

Entirely optional — the tool works fine on its own. This part is what it was
built for.

Drop a file called `.ticket-scope` in a project folder, naming the store that
project's work belongs to:

```
personal
```

`next_task.py` walks up from wherever it's run looking for that file, so inside a
project you can drop `--store` completely:

```bash
python /path/to/next_task.py ready
```

The nearest marker wins, so a project can put different parts of itself in
different stores — a `client/` subfolder in one, everything else in another.

New tickets also record which project they came from, so a single store can
collect work from many projects and still tell you where each came from. That's
the `my-project` column in the example above. By default that's the folder
holding the marker, which is wrong when the marker sits in a subfolder, so an
optional second line names the project instead:

```
personal
AI Governance
```

Line one picks the store and must be a single plain word. Line two is only a
label, so spaces and punctuation are fine.

There's a skill in `skills/next-task-setup/` that does this setup for you. Copy
it to `~/.claude/skills/` and Claude can create a project's marker on request.
It's worth having rather than writing the file by hand, because it checks
subfolders first: if a project has markers further down, the absence at the top
is deliberate, and adding one there would route that project's work into a
single store.

Add instructions to your `CLAUDE.md` telling Claude to use the script, and it
can file and close tickets as you work. `dashboard_launch.py` is a small helper
for a `SessionStart` hook: it starts the dashboard if it isn't already running,
opens one tab at that moment only, does nothing outside a folder with a
`.ticket-scope` marker, and never reports an error — a hook that fails loudly
at every session start is worse than no hook.

## Using it from Claude app Projects

Projects in the Claude app have no filesystem, so they can't run the script.
`next_task_mcp.py` is a small MCP server that bridges the gap: add it to
`claude_desktop_config.json` and a Project can file tickets into the same
stores.

```json
"mcpServers": {
  "next-task": {
    "command": "python",
    "args": ["/path/to/next_task_mcp.py"]
  }
}
```

Every tool runs the real CLI and returns what it said, so the MCP path can't
disagree with the command line, and writes go through the same lock and ID
ledger. Which store to use is a required argument with no default, and each
reply names the store it used — through MCP that choice is an argument rather
than a path, so it's worth keeping visible.

`docs/claude-project-instructions.md` has a template for a Project's custom
instructions. This only works where the server can be reached, meaning the
desktop app on the machine holding the stores — not the web or a phone.

## The data

One ticket, one JSON file, named after its ID. `docs/example-ticket.json` is a
committed sample of the exact format, and a test asserts it matches what a
freshly created ticket looks like, so it can't quietly go out of date.

Because tickets are separate files, a diff shows exactly which ones changed,
and you can read or fix one in any text editor. Two folders sit alongside:
`.tmp/` for in-progress writes, and `.ids/` recording every ID ever issued so a
number is never handed out twice.

## Why it's built this way

Short version: an earlier attempt used a third-party tool that crashed on
Windows on every command, and separately hit an encoding failure that blocked
ticket creation outright rather than degrading. So this one has no dependencies
in the data path, specifies its encoding everywhere, and treats "the tool
breaks the store" as the failure worth engineering against.

A few consequences you might otherwise mistake for over-engineering: writes go
via a temporary file and a rename; IDs are claimed with an exclusive create
before the ticket is written; edits take a lock; reads retry briefly; one
unreadable file never stops a command; and a store is never created by
accident, so a typo in `--store` fails loudly instead of silently starting a
new empty store.

`CLAUDE.md` records each of those with the specific thing that went wrong.
Worth reading before changing any of them.

## Tests

```bash
python -m unittest test_next_task -v
```

Every test builds a throwaway store in a temp directory; none of them touch
real data. The suite covers the dependency lifecycle, the concurrency
behaviour, the dashboard and the launcher.

## Licence

MIT — see [LICENSE](LICENSE). Use it, change it, ship it; no warranty.
