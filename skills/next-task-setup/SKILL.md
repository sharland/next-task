---
name: next-task-setup
description: Set up next-task ticket tracking in a project by creating its .ticket-scope marker file. Use this whenever the user wants tickets in a project that doesn't have one yet, says something like "set up tickets here" or "enable ticket tracking for this project", asks why a ticket command can't find a store, or when you're about to file a ticket and discover there's no marker. Use it in preference to writing a .ticket-scope by hand, because it checks subfolders before creating anything — in a project that deliberately splits its work between stores, a marker at the top level sends that work to the wrong store, and for a work/personal split that is exactly the mistake the separate stores exist to prevent.
---

# Setting up next-task in a project

next-task decides which ticket store a project uses by finding the nearest
`.ticket-scope` file, walking up from wherever a command is run. No marker
means no tickets for that project, and no dashboard entry either.

Creating one is a single small file. The reason this is a skill rather than a
one-line instruction is that **the absence of a marker doesn't always mean one
is wanted**, and getting that wrong writes someone's work into the wrong store.

## Step 1: work out whether a marker is actually needed

Three cases, and they need different answers.

**A marker already applies.** Look for `.ticket-scope` in the current folder
and every folder above it. If one is found, this project is already set up —
say which file governs it and which store it names, and stop. Creating another
would only shadow it.

**Markers exist further down.** If there's nothing here or above, look in the
subfolders before doing anything else. If you find markers there, the absence
at this level is deliberate: that project splits its work between stores, and
each subfolder has been pointed at the right one. Adding a marker at the top
would capture anything filed from the root into a single store — including
material that belongs in the other one.

Don't create a marker in this case. Explain what you found, and offer the two
things that do work: pass `--store` explicitly, or run ticket commands from the
subfolder that applies.

**Nothing anywhere.** Now a marker is genuinely wanted. Continue to step 2.

## Step 2: ask which store, and what to call the project

**Never guess the store.** The whole point of separate stores is that some work
is subject to rules the rest isn't, and that boundary is only reliable if a
human sets it. Ask, and take the answer literally — a project sounding
work-related is not evidence.

The marker's first line is the store name (typically `personal` or `work`) and
must be a single plain word. Check it matches a store that actually exists
next to `next_task.py`; a name with no store behind it produces a confusing
failure later rather than at setup time.

The second line is optional and names the project. It becomes the `source`
recorded on every ticket filed from here. Without it, source defaults to the
name of the folder holding the marker — which is fine when the marker sits at
the project root, and useless when it sits in a subfolder called `general` or
`company`. So:

- Marker at the project root: the folder name is usually already right, and a
  second line is only worth adding if the folder name is cryptic, or long
  enough to be truncated in the ticket list.
- Marker in a subfolder: suggest the parent project's name, since that's what
  the person reading the ticket later will recognise.

Suggest a value rather than asking an open question — the user can correct a
suggestion faster than they can invent an answer.

## Step 3: write it and prove it works

Write `.ticket-scope` in the chosen folder, UTF-8, one value per line:

```
personal
AI Governance
```

Then confirm it resolves, rather than assuming. Running the tool's own `ready`
command from that folder is the honest check — if the marker is malformed or
names a store that doesn't exist, this is where you find out:

```bash
python /path/to/next_task.py ready
```

A list of tickets (or "Nothing ready.") means it worked. An error about the
scope name or a missing store means the marker needs fixing now, while you
still have the context.

Finally, tell the user which store the project is now pointed at and what
source its tickets will carry. They're the one who has to notice if it's wrong.

## Finding the tool

The path to `next_task.py` is usually recorded in the user's global
`~/.claude/CLAUDE.md` under their task-tracking instructions. Read it from
there. If it isn't recorded, ask — don't search the filesystem for it, since
guessing wrong risks writing a marker that points at a store the user didn't
mean.

Store folders live beside the script, so once you have its path you know where
`personal`, `work` and any others are.

## If the project is in git

`.ticket-scope` is a local routing marker, not project content. Suggest adding
it to `.gitignore` unless the user wants collaborators pointed at their own
stores — which for a shared repo is rarely what they want.
