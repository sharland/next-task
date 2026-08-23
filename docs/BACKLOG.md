# Backlog — internal work on the tracker

Engineering work on the tool that the owner would never act on directly. It
lives here rather than in a ticket store, because those stores are their own
task list. Anything that changes what the owner sees or decides goes in the
store instead; the test is whether they would ever act on the outcome.

## Open

- **Filtering by title, not just source.** The dashboard filters by source
  project, which was enough at twenty tickets and is getting coarse now there
  are three times that many. A type-to-filter box matching titles and
  descriptions would narrow a tab without needing a source that happens to line
  up with what's being looked for. Browser-side like the rest of the
  interactivity, so it adds no route — the delete route stays the only one.
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

## Known consequences worth remembering

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
