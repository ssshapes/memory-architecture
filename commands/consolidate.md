---
name: consolidate
description: Memory consolidation. Distills recent captured sessions into durable memories, proposes merges for duplicates, and flags stale or contradicted memories. Propose-only. Nothing is written to the semantic store without explicit approval.
---

# /consolidate

The consolidation organ: fold episodic into semantic, dedupe, prune. Without it the captured-session store accretes forever and the durable store slowly duplicates and drifts.

It runs **propose-first**. It never writes or edits a memory without in-session approval. That is not politeness. Consolidation is also the forgetting function, and forgetting is the one operation you cannot audit afterwards, because the evidence is what got removed.

## Inputs

- **Captured sessions:** `~/.cache/<os>-memory/episodic/*.md`. Each card carries a description and a `Mentions:` line. Full transcripts sit in `episodic/full/`; open one only when a candidate fact needs verifying.
- **Watermark:** `~/.cache/<os>-memory/consolidation/last_run`, the ISO date of the last pass. Missing file means process everything.
- **The index:** `MEMORY.md` in the memory directory, which is what already exists. Open individual memory files only to check a specific overlap.

## Steps

1. Read the watermark. Consider only cards dated after it.
2. Extract candidate **durable** facts, in the memory shapes the store uses: who the user is and stable preferences; how to work with them (include the reasoning, not just the rule); ongoing work, goals and constraints that are not derivable from the repo itself. Skip anything ephemeral, and anything already obvious from the files.
3. For each candidate, check the index:
   - Not covered: propose **NEW** (name, type, one-line description, body, and the session id it came from).
   - Covered, but the session adds or changes something: propose **UPDATE** (which file, what changes).
4. Scan the index for duplicates and heavy overlap: propose **MERGE** (which files, which survives).
5. Flag **STALE**: a session contradicts a stored memory, or a memory names a file, flag or tool that no longer exists. Disposition for approved stale items is **archive, not delete**. The archive stays indexed and retrievable, it just leaves the always-loaded index. Delete only what is plainly wrong.
6. **PRUNE** captured sessions: propose deleting junk cards. The capture hook gates most of these at write time, so this mostly catches strays.
7. Write the full proposal to `~/.cache/<os>-memory/consolidation/proposals-YYYY-MM-DD.md`, and surface a tight grouped summary in the session.

## Approval and apply

- Present proposals grouped **NEW / UPDATE / MERGE / STALE / PRUNE**. Approval is per item, by number.
- Apply approved items through the normal write path, so the provenance validator runs on them: standard frontmatter, `[[wikilinks]]` to related memories, a pointer line in the index. Merges retire the losing file.
- **Every new or updated memory cites the session it came from.** Unsourced durable claims are exactly what the validator exists to catch, and a consolidation pass that invents provenance is worse than no pass.
- After applying, write today's date to the watermark. **A zero-proposal run also advances it.** Those sessions were consolidated; nothing durable was found. Do not rescan them forever.

## Eviction

Do not evict on age or link count. A dry run of that heuristic selected nearly the entire body of stable working doctrine, because settled rules are rarely edited and rarely linked while being in context every session. Age measures edit recency, link count measures graph centrality, and neither measures value.

Use recall frequency instead. `recall_stats.py` reads what recall actually surfaced. The rule, once a full window of data exists: zero recalls across the window AND a long time since the last edit means archive candidate. Archive, never delete.

## Guardrails

- Propose only. Never bulk-write memories.
- A mature store yields **few** proposals per run. That is the correct outcome, not a failure. Resist inventing memories to look productive.
- **Cadence:** weekly, or after a heavy multi-session stretch. This command is the tending loop for the whole memory system. Without it the captured store accretes and the durable store duplicates.
