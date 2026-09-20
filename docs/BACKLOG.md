# Backlog — internal work on the tracker

Engineering work on the tool that the owner would never act on directly. It
lives here rather than in a ticket store, because those stores are their own
task list. Anything that changes what the owner sees or decides goes in the
store instead; the test is whether they would ever act on the outcome.

## Open

- **Decide where this project's auto-memory lives.** Two directory keys now
  hold the same memory files, and which one a session uses depends on the
  folder it was started from — see the auto-memory note under Known
  consequences for the detail. The choice is either to consolidate on the
  `next-task` key and always start sessions there, or to keep both knowingly.
  What can't hold is the current state, where they agree only by luck and the
  first write to either one begins a silent divergence.

The tool has been in real use since 2026-08-08 across several projects, filed
from both Claude Code sessions and Claude app Projects. The first round of bug
reports and feature requests from that period landed on 2026-08-23 (see Done);
further rounds are expected as use continues.

## Deliberately not done

Each of these came up, was considered, and was left. Reasons included so they
don't get silently re-proposed or silently dropped.

- **Widening the source column.** `SOURCE_WIDTH` is 12, so `ai-governance`
  shows as `ai-governan~` and `family-history` as `family-hist~`. Two of the
  eight sources in use are affected, and naming projects properly in the
  marker's second line made it more visible rather than less. It's a
  one-character change; it was deferred, not rejected.

  Rechecked 2026-08-23: both examples this entry used to give had gone stale —
  one source was renamed and the other shortened at its marker, so the entry
  named two strings that no longer existed anywhere while the problem itself
  was still live. An entry that quotes real data is worth re-running before
  it's trusted.
- **Source varying within a project.** The marker's second line gives one label
  per project. At least one project wants tickets tagged by workstream instead
  (`explainer-video` rather than the repo name), which that can't express —
  it'd need `--source` per ticket, or a different idea entirely.
- **A stop command for the dashboard.** It runs until killed. Task Manager
  works, and a stop command is more moving parts than the problem deserves.
- **Showing status in `ready`.** The dashboard distinguishes in-progress from
  untouched; `ready` doesn't. Only worth it if the CLI starts feeling blind.
- **Backfilling `source` on older tickets.** Never guessed at. The owner
  supplies it per ticket when asking for one to be updated.
- **A link on each ticket back to the Claude Code session that created it.**
  With ticket counts rising, jumping straight back into the right
  conversation — instead of starting fresh and re-explaining context — would
  save real time. Investigated 2026-09-14, blocked on an upstream bug rather
  than rejected.

  Two deep-link schemes exist. `claude-cli://open?cwd=&q=` is real and
  documented (code.claude.com/docs/en/deep-links) but only opens a *new*
  session at a folder — no session identity involved, so it's the wrong tool.
  `claude://resume?session=<uuid>` is the right one: it takes the plain CLI
  session UUID, which is already sitting in the `CLAUDE_CODE_SESSION_ID`
  environment variable for any command a session runs — `create` could
  capture it automatically the same way `source` already captures the current
  folder, no new plumbing needed. Confirmed via a public bug report rather
  than guessed at (github.com/anthropics/claude-code/issues/80773), not by
  testing the link live — a bad outcome would have opened an unwanted tab on
  the owner's own screen with no way to detect or undo it from here, unlike
  an earlier probe in that same session that only spawned a background
  process, which could be found and killed cleanly.

  The blocker: that same bug report shows resuming a session that's already
  open as a *native Desktop Code tab* — which is what every ticket-filing
  session here is — creates a duplicate forked snapshot tab instead of
  focusing the live one, because the app's dedup check only matches sessions
  imported from a headless CLI run, not ones started natively in the desktop
  app. Confirmed open and unfixed as of 2026-09-14. Revisit if that issue is
  closed as fixed; until then, the link would fork a frozen copy of the
  conversation on every click rather than returning to the live one.

## Known consequences worth remembering

- The dashboard does not survive a reboot on its own. It's a plain detached
  process, not a Windows service, so a restart kills it exactly as ending it in
  Task Manager would. It's meant to come back by itself: the next Claude Code
  `SessionStart` inside a folder with a `.ticket-scope` marker runs
  `dashboard_launch.py`, which notices the port is free and relaunches it. But
  every error in that script is swallowed on purpose (see the CLAUDE.md
  invariant on `dashboard_launch.py`), so a launch that fails right after boot
  — antivirus scanning the interpreter, disk still spinning up, anything
  merely transient — looks identical from outside to one that succeeds.

  Confirmed 2026-09-03: a reboot at 09:17 left nothing listening on port 5000,
  and the session open at the time was in `tickets/`, which carries no marker
  and so never calls the hook at all. Running `dashboard_launch.py` by hand
  from a folder that does have one is both the fix and the diagnostic — it
  prints the real error if the relaunch itself is broken, rather than the
  silence a session start gives you either way.
- The skill exists twice: `skills/next-task-setup/` here is the source of
  truth, and a working copy lives at `~/.claude/skills/next-task-setup/`.
  Nothing links them, so they can drift. They were identical as of 2026-08-12.
- `~/.claude` holds the memory files, the skill copy and the routing
  instructions, and is not in Dropbox or any repo. It has no backup.
- Auto-memory is keyed on the project's folder path. The 2026-08-11 rename from
  `tickets` to `next-task` orphaned the memory directory; it was copied across
  to the `next-task` key rather than moved. Any future rename does the same
  thing silently.

  This is not just history. As of 2026-08-23 both keys still exist and hold the
  same files — byte-identical, checked, so nothing has diverged yet. But the
  old `tickets` folder is still on disk with its own `.claude` in it, and which
  copy a session reads and writes is decided by the folder it was started from.
  A session launched in the old folder writes to the orphaned copy and the
  other silently goes stale. Nothing warns about this, and the two only agree
  today because neither has been written to since the copy. Listed under Open,
  because the fix is a decision rather than code.

## Done

- **2026-09-20 light/dark theme and a tidier heading** — a toggle in the
  header, starting from the operating system's setting and remembering an
  explicit choice. Every colour on the page had been hardcoded, so the work was
  moving them into two variable palettes rather than adding a second
  stylesheet; a test fails if a rule hardcodes a colour again, since that's how
  a dark mode ends up with one white box, and it was mutation-checked against
  both a named and a hex colour. The theme is applied by a script in the head so
  there is no white flash on load. The first version of the toggle used moon and
  sun glyphs, and the moon fell back to a wrong character in the page's font
  stack — plain text labels instead, since glyph support differs by machine.
  Also removed the store heading's own ticket count: it included done and
  cancelled tickets and so disagreed with the live number already on the tab.
  Suite is 169 tests.
- **2026-09-13 title/description search box** — closed the open item above on
  filtering by title. One search box per tab (each store and Done /
  cancelled), matching title and description together, live as you type. The
  searchable text is computed once server-side per row (`_search`, lowercased
  title plus description) and carried in a `data-search` attribute, the same
  pattern as `_source`/`data-source` for the existing filter — so what gets
  searched can never drift from what the row actually shows, and it's covered
  by a plain unit test rather than only an HTML string match. It combines with
  the source filter (a row must pass both), runs entirely client-side like the
  rest of the interactivity, and adds no route. Confirmed live: a search
  narrows results, combines correctly with an active source filter, and on the
  Done tab correctly excludes filtered-out rows from "select all" and the
  delete count, matching how the source filter already behaved.
- **2026-08-23 dashboard round** — the first batch of real-use reports.
  Bugs: the Created column wouldn't sort (its sort value was the ISO
  timestamp, and the comparison parsed a leading number off it, so every row
  equalled its year — timestamps now go over as epoch seconds, and the script
  only compares numerically when the whole value is numeric); the sort arrow
  wrapped onto its own line in narrow headers (breakable space, no `nowrap`);
  the CLI's `ready` broke priority ties on the ID as text, the exact
  PERS-999/PERS-1000 trap `id_number` exists for; and two done tickets missed
  by an earlier bulk source rename kept a stale name alive in the filter
  dropdown (data, fixed with `update --source`). Features: one tab per store
  plus a shared Done / cancelled tab merging every store's finished tickets;
  a guarded `POST /delete` for clearing old finished tickets — the one
  deliberate exception to the old no-write-routes rule, see CLAUDE.md for the
  three guards; descriptions unfurl on click for rows that have one; the
  chosen tab and sort survive refresh via localStorage; an inline SVG favicon.
  Suite grew from 143 to 159 tests.
- **MCP server for Claude app Projects** — `next_task_mcp.py`. Projects have no
  filesystem, so they had no route to the stores. Every tool runs the CLI as a
  subprocess and returns its output verbatim rather than reimplementing
  anything, so there's no second copy of the rules to drift and the store lock
  and ID ledger apply for free. `scope` is required with no default and every
  reply names the store used. Confirmed working from a real Project on
  2026-08-12; three tickets filed nine seconds apart got three distinct IDs.
- **Setup skill** — `skills/next-task-setup/`. Creates a project's
  `.ticket-scope`, but checks subfolders first: `find_scope_marker` walks up
  only, so it returns nothing both when markers exist further down and when
  there are none at all, and the code cannot tell those apart. Creating one at
  the top of a project that splits its work between stores would route that
  work to a single store.
- **UTF-8 on stdout** — every file open specified `encoding="utf-8"` from the
  start, but stdout didn't. Piped output on Windows lands on cp1252, so one
  accented character in a title crashed `list` and `ready` for the whole store
  and made `create` exit 1 after already writing the ticket. Found by writing
  the first test in the suite to contain a non-ASCII character.
- **`verify` blind to id problems** — it now flags a filename that disagrees
  with the id inside it, and two files claiming the same id. Before, one ticket
  silently disappeared from every command while `verify` reported a clean store.
- **Dependency cycles** — `verify` reports every ticket caught in a loop and
  exits 1; `create --depends-on` refuses one that would close a loop, matching
  `update --add-depends`.
- **Small CLI correctness fixes** — `--status` no longer needs `--all`;
  `verify` exits 1 on problems; `update` survives a ticket with no
  `depends_on`; IDs sort numerically; the `Unblocked:` notice skips finished
  dependents.
- **Test suite** — 143 tests covering the dependency lifecycle, concurrency,
  the delete guard, `ready` ordering, `would_unlock`, scope resolution, the
  dashboard page and the launcher. The lifecycle, delete-guard and ordering
  tests were mutation-checked, since tests written after the behaviour prove
  little until you watch them fail.
- **Serialise concurrent updates** — a store-level lock around the
  read-modify-write in `update`. The lost edit was reproducible after all: six
  simultaneous `--add-depends` calls used to keep only some of them.
- **Revise CLAUDE.md** — kept current as each change landed. The command list
  and flags were verified against `build_parser` mechanically.
