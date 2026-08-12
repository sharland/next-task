# Wiring a Claude app Project to next-task

A Claude Code session finds its store from a `.ticket-scope` file. Projects in
the Claude app have no filesystem, so there's nowhere to put one — the same
information goes in the Project's custom instructions instead.

Paste the block below into a Project's instructions, filling in the two values.
It assumes the `next-task` MCP server is configured in Claude Desktop.

---

## The template

```
This project's work is tracked in next-task, via the next-task MCP tools.

  scope:  personal          <- personal or work; the store tickets go to
  source: AI Governance     <- what to record as the project on each ticket

Pass those exact values on every next-task call.

Before filing anything, draft it and show it to me: the title, the store, the
source, and the priority. File it only once I've agreed. Titles should say what
finishing looks like, not just name a topic — "Confirm AIGP exam pricing at
iapp.org" rather than "AIGP pricing".

Before filing, think about ordering. Blocked tickets are hidden from `ready`,
so a dependency is what stops me picking something up before it can actually be
done — and a dependency nobody set is the difference between a useful list and
a pile.

Two places to look. Check what's already in the store with list_tickets or
list_ready, in case a new ticket waits on something that exists. And when you're
filing several at once, check them against each other: a batch drafted in one go
is where ordering is most obvious and most often skipped, because they arrive
looking like a flat list.

If a real relationship exists, set it. If you're unsure whether one does, ask me
rather than guessing — don't assume there's no relationship just to avoid the
question.

Dependencies are by ID, so within a batch file them in order: create the
prerequisite, take the ID from the reply, then pass it as depends_on when you
create the ticket that waits on it.

If the next-task tools aren't available — you're on a phone or the web, where
the server can't be reached — don't pretend otherwise and don't invent an
alternative. Say the tools aren't there, and write the ticket out in full so I
can file it later. This conversation is the record; I can ask a Claude Code
session to read it back and file from it.
```

---

## Choosing the two values

**scope** decides which store gets written to. On disk that boundary is a
folder path; through MCP it's an argument, which is weaker — so it's worth
being deliberate. If a Project touches material subject to rules the rest of
your work isn't, it belongs in the store for that, and the Project's
instructions are the only thing enforcing it.

**source** is a label. Use whatever you'd recognise in a ticket list months
later — the project's real name, not a folder name. It's free text, so spaces
and punctuation are fine, but the CLI column is narrow, so shorter reads better.

## Why the confirmation step matters

Claude Desktop asks before running an MCP tool. That prompt is the safeguard:
it's the moment you see which store a ticket is about to land in.

Allow the read tools permanently if you like — `list_ready`, `list_blocked`,
`list_tickets`, `show_ticket`, `verify_store`, `list_stores` change nothing.
Leave `create_ticket` and `update_ticket` asking every time. Approving those
once and forgetting means a Project's instructions, rather than you, decide
where work is filed.

## Checking it works

In a Project chat, ask what's ready. You should see the store named in the
reply — every tool states which one it used, so a wrong scope shows up in the
conversation rather than silently.

If the tools aren't offered at all, Claude Desktop hasn't picked up the server:
confirm `next-task` is in `claude_desktop_config.json` and restart the app.
