---
name: save-clear
description: Defined session closure. Sweep the conversation for every context type, write each to its durable home, force the episodic capture that /clear would skip, output a manifest, and give the clear-safe verdict. Run before /clear, /exit, or killing a session.
---

# /save-clear

"Save everything" is not a feeling, it is this checklist. Work it top to bottom, then report the manifest.

It exists because **`/clear` fires no hooks.** Killed and exited sessions do get captured; cleared ones were vanishing silently. Step 5 closes that.

## Step 1: sweep the conversation

Go back through the session and route anything not already written:

| Context type | Durable home |
|---|---|
| **Decisions made** | The file that owns the thing decided. Update the status, add the wikilinks. |
| **Durable facts** (who the user is, how to work with them, ongoing work) | The memory store, plus a pointer line in the index. Check for an existing file before creating a new one. |
| **Open threads and next actions** | Wherever open work lives. If it fits nowhere, it dies here rather than living on in a transcript nobody will read. |
| **Things that actually shipped** | The shipped log. Irreversible external actions only. |
| **Ideas and seeds** | The ideas log, in the original phrasing, with a note on when it should resurface. |
| **Conversation-only artifacts** (drafts, analysis, tool output worth keeping) | A real file. If it exists only in this conversation, it is not saved. |

Most of this should already be done, because the discipline is to write live. A sweep that finds a lot is itself a signal that the session drifted. Note that in the manifest rather than quietly fixing it.

## Step 2: consolidation review

You are holding the whole session in context. Use it. **Propose only**, present a short list, each item individually approvable, write nothing until approved. Skip the step in silence if the session produced nothing durable.

1. **Distill:** what this session taught that step 1 missed. A lesson that should become a memory. A correction to an existing memory, named by file. A procedure repeated by hand that deserves to be a command.
2. **Consolidation candidates:** memories this session revealed as stale, duplicative or contradicted. Name the pairs, propose the merge, never silently overwrite.
3. **Back-pressure:** run `metabolism_stats.py` and include the output. If it prints a pressure line, ask whether a full `/consolidate` pass is worth doing. Do not do the deep pass here.

## Step 3: verify the ledgers match the session

Quick pass. Does anything that happened here contradict what the status files currently say? Fix the mismatches now: dates, statuses, things marked open that are done.

## Step 4: force the episodic capture

The hook will not fire on `/clear`, so run it by hand. First emit a unique marker so this session's transcript can be found among the parallel ones. Write a line like `SAVECLEAR-MARKER-<short-unique-token>` into your response, then:

```bash
J=$(grep -l "SAVECLEAR-MARKER-<token>" ~/.claude/projects/<project-dir>/*.jsonl | head -1)
SID=$(basename "$J" .jsonl)
printf '{"transcript_path":"%s","session_id":"%s","hook_event_name":"manual-preclear"}' "$J" "$SID" \
  | "$HOME/.venvs/memory/bin/python" "$CLAUDE_PROJECT_DIR/.claude/hooks/memory_episodic.py"
ls -t ~/.cache/<os>-memory/episodic/*_${SID%%-*}.md | head -1
```

Confirm the card path prints. It is idempotent, so re-running is safe and overwrites with the fuller version. The raw transcript persists on disk either way; capture is what makes it *retrievable*.

## Step 5: manifest and verdict

Output a short **SAVED manifest**: what was written where in steps 1 to 4 (or "already current" per line), the episodic card path, and anything deliberately not saved with the reason. End with the verdict: **clear-safe**.

If a step could not complete, say so in the manifest instead of declaring clear-safe. The confidence comes from the manifest being honest, not from it being green.
