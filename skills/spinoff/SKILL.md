---
name: spinoff
description: Spin off a dedicated session to own a scoped thread of work. Stages the context into the repo as a brief, launches the session with the brief as its opening instruction, registers it, verifies it started, and reports how to reach it. Use when a thread deserves its own channel instead of crowding this one. Do not spin off vague help; the brief has to name concrete deliverables.
---

# /spinoff

A spun-off session is a second Claude Code session that owns one scoped thread and dies when the thread is finished. It is worth doing when a piece of work has its own deliverables and its own context, and keeping it here would crowd out everything else.

The hard constraint that shapes this entire skill: **the new session cannot see this conversation.** Not the messages, not the tool results, not the scratch files that were never written down. Anything it needs has to exist on disk before it starts. That is the whole job.

## 0. Sweep before writing the brief

Do not brief from the one thing that triggered the spinoff. Check each surface and note it in the brief, both what was found and what was checked and empty:

- **Memory:** search the durable store and the captured sessions for the topic.
- **Entity files:** the people, companies or projects the thread touches. Exact details live there.
- **Status files:** what the open-work ledger already says about this, so the new session neither duplicates it nor contradicts it.
- **Prior drafts:** earlier briefs, notes and half-finished files on the same topic.

## 1. Stage the context into the repo

Transcripts, extracts and reference material go into a real file. Decisions and constraints that exist only in this conversation get written into the brief itself. If it is not on disk, it does not exist.

## 2. Write the brief

To `scratch/session-brief-<slug>-<YYYY-MM-DD>.md`:

- **Header:** what was spun off and from where. "You are the `<slug>` session."
- **Sources, read in order.** Most distilled first. Name the ground truth explicitly.
- **Exact details it must not have to guess:** addresses, names, ids, paths.
- **Deliverables, in order.** Concrete outputs. Name the exact tools. Any draft-only rule stated explicitly.
- **Constraints.** What not to invent. Standing doctrine that applies. Anything it must NOT duplicate.
- **Death step.** Run the closing checklist, update its status entry, then end itself. A finished spinoff left running is clutter. Whoever kills it also removes it from any respawn roster, or the next reboot brings back a ghost.
- **Definition of done**, ending with "then this brief dies."

## 3. Launch

```bash
tmux new-session -d -s <slug> -c /path/to/repo
tmux send-keys -t <slug> "claude 'Read scratch/session-brief-<slug>-<date>.md and execute it. You are the <slug> session.'" Enter
```

Send keys into the shell, so the launch inherits the environment, and pass the brief as the command-line argument. Do not rely on typing into the running interface; injection there is unreliable.

## 4. Register and verify

- Add it to whatever resurrects sessions after a reboot, so it survives one.
- Wait a few seconds, capture the pane, and confirm it is actually reading the brief. Handle any trust prompt.

## 5. Report

One short block: how to attach, what the new session owns, and what stays here.

## 6. Bookkeeping in this session

Add a status entry for the new session in the owning section of your open-work ledger, with its scope and the brief path, marked live. The thread now has an address; the ledger has to know it.

## Rules

- One spinoff, one scoped thread, concrete deliverables.
- A spinoff never sends anything outward on its own authority.
- Misfired launch: capture anything worth keeping, kill it, clean up, relaunch.
- Standing channels persist. Spinoffs never do. Born scoped, die finished, context on disk first.
