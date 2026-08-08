# Backlog — internal work on the tracker

Engineering work on `next_task.py` and `dashboard.py` that the owner would never act
on. It lives here rather than in `personal/`, because the ticket stores are his
own memory and shouldn't fill up with the tool's own repairs. Anything that
changes what he sees or decides goes in the store instead.

Moved out of the personal store on 2026-08-07 (was PERS-026, 028, 029, 036, 037).

## Open

Nothing outstanding as of 2026-08-08.

Ideas that have come up and were deliberately not done:

- **A `--stop` for the dashboard.** It runs until killed. Task Manager works,
  and a stop command is more moving parts than the problem deserves so far.
- **Showing status in `ready`.** The dashboard distinguishes in-progress from
  untouched; `ready` doesn't. Only worth it if the CLI starts feeling blind.
- **Backfilling `source` on older tickets.** Deliberately not guessed at. The owner
  supplies it per ticket when he asks for one to be updated.

## Done

- **UTF-8 on stdout** — every file open specified `encoding="utf-8"` from the
  start, but stdout didn't. Piped output on Windows lands on cp1252, so one
  accented character in a title crashed `list` and `ready` for the whole store
  and made `create` exit 1 after already writing the ticket. Found by writing
  the first test in the suite to contain a non-ASCII character.
- **`verify` blind to id problems** — it now flags a filename that disagrees
  with the id inside it, and two files claiming the same id. Before, one ticket
  silently disappeared from every command while `verify` reported a clean store.
- **Detect dependency cycles** (was PERS-026) — `verify` reports every ticket
  caught in a loop and exits 1; `create --depends-on` refuses one that would
  close a loop, matching what `update --add-depends` already did.
- **Small CLI correctness fixes** (was PERS-028) — `--status` no longer needs
  `--all`; `verify` exits 1 on problems; `update` survives a ticket with no
  `depends_on`; IDs sort numerically; the `Unblocked:` notice skips finished
  dependents.
- **Extend the test suite** (was PERS-029) — 137 tests. The gaps that remained
  after the feature work were the lifecycle sequence, the delete guard, `ready`
  ordering, `would_unlock`, `--remove-depends`, `show`'s error and dependency
  output, empty-store messages, and non-ASCII content. All covered. The
  lifecycle, delete-guard and ordering tests were mutation-checked, since tests
  written after the behaviour prove little until you watch them fail.
- **Serialise concurrent updates** (was PERS-036) — a store-level lock around
  the read-modify-write in `update`. The lost edit was reproducible after all:
  six simultaneous `--add-depends` calls used to keep only some of them.
- **Revise CLAUDE.md** (was PERS-037) — kept current as each change landed, so
  the pass was a check rather than a rewrite. Verified the command list and
  flags against `build_parser` mechanically, every named file exists, and the
  two stale forward-looking sections are gone.
