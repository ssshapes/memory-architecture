# Memory architecture

A working memory system for [Claude Code](https://claude.com/claude-code): retrieval, capture, validation and consolidation, wired to the agent's own hook events. It runs a personal operating system of a few hundred markdown files across three repos, every day, and has since June 2026.

**Start with the page, not the code: [the architecture, explained end to end](https://ssshapes.github.io/memory-architecture/).** It walks one prompt through the whole circulation, names each organ, and gives the lineage for every borrowed idea. The code in this repo is that page made runnable.

> This is how mine works. Fork it. I don't take feature requests.

That sentence is the whole posture. This is a reference implementation, not a product. There is no roadmap, no support, no versioning policy. If it is useful, take it and make it yours. The moat was never the organs anyway, it is the corpus they tend, and that stays private by construction.

---

## What it does

You type a prompt. Before the model sees it, a hook searches everything you have written, finds the handful of documents that bear on it, and injects them. When the session ends, or compacts, or just sits there for a day, another hook writes the conversation down so it stops being something the window was holding. Every write to the memory store that goes through Claude Code's own Write and Edit tools gets checked as it happens; a write from outside the hook's reach (your editor, a script) is caught at the next index rebuild instead. Once a week something reads the pile and proposes what should be distilled, merged or retired, and you approve it by hand.

Nine pieces, each doing one job:

| Organ | Fires on | What it does |
|---|---|---|
| `memory_recall.py` | UserPromptSubmit | Hybrid search (lexical + vector, fused), one hop over your `[[wikilinks]]`, injects the top hits |
| `memory_sync.py` | SessionStart | Rebuilds the index from changed files only |
| `memory_episodic.py` | PreCompact, SessionEnd | Captures the transcript as a verbatim record plus a small indexed card |
| `bin/checkpoint.py` | timer | Runs the capture engine against sessions still alive, so a long session cannot hold days of unwritten conversation |
| `validate_os_memory.py` | PostToolUse (Write, Edit) | Blocks unsourced citation identifiers and index-file overflow; warns on unsourced quantified claims |
| `os_lint.py` | SessionStart | Corpus-wide consistency: broken graph edges, index integrity, generated-text tells |
| `metabolism_stats.py` | on demand, in the closing checklist | Back-pressure: says when the store has grown enough to be worth a pass |
| `recall_stats.py` | on demand | What recall actually surfaces, which is the only honest input to an eviction decision |
| `commands/consolidate.md` | you run it | Distills captured sessions into durable memories. Proposes. Never writes on its own |

Plus two closing commands (`save-clear`, `save-kill`) and a `spinoff` skill, which are the seam discipline around all of it: how a session ends without losing anything, and how work gets handed to a new one.

## The four guarantees

The organs are ordinary code. Anyone can write hybrid retrieval in an afternoon. What is worth copying is the conduct, because every one of these was learned by getting it wrong first.

**Propose, never mutate.** No organ deletes, merges or edits a memory on its own. Consolidation writes a proposal and waits. Metabolism prints a pressure line and waits. This is not caution for its own sake: forgetting is the one operation you cannot audit after the fact, because the evidence is what got removed.

**Fail open, everywhere.** Every hook exits 0 on any error. A recall bug injects nothing, a capture bug captures nothing, and the session carries on. A memory system that can break your prompt is worse than no memory system, and you will find out at the worst moment.

**Files over database.** Memories are markdown, one durable fact per file, readable and editable with anything, including your hands. The SQLite index is derived and disposable: delete it and the next session rebuilds it. Nothing that matters lives anywhere you cannot read it without this code.

**Local embeddings.** Retrieval runs on a small model on your machine: the similarity search itself never leaves the box, and no third-party embedding API sees your memory store. The prompt and whatever the hook injects still go to the model provider, as in any Claude Code session — this bounds what the retrieval layer adds, which is nothing.

## What it deliberately is not

- **Not automatic forgetting.** Eviction is the obvious next organ and it is not built, on purpose. See the limitations below.
- **Not a RAG framework.** It indexes one person's writing, not a document warehouse. Under about ten thousand files this is the right amount of machinery, and past that you want different tools.
- **Not hosted, not synced, not multiplayer.** One machine, one user.
- **Not maintained for you.** See the posture sentence.

## What is here

```
index.html          the architecture page (GitHub Pages front door)
build.py            extracts the organs from a private OS and refuses to publish a leak
hooks/
  config.py         the one file you edit: locations, thresholds, skip lists
  memory_lib.py     retrieval engine (SQLite FTS5 + sqlite-vec, fused with RRF)
  memory_recall.py  UserPromptSubmit hook
  memory_sync.py    index builder
  memory_episodic.py  transcript capture
  validate_os_memory.py  write-time provenance validator
  os_lint.py        corpus-wide lint
  metabolism_stats.py    growth pressure
  recall_stats.py   recall-frequency reader
bin/checkpoint.py   out-of-session capture for long-running sessions
commands/           consolidate, save-clear, save-kill (Claude Code slash commands)
skills/spinoff/     how a scoped thread gets its own session with its context staged on disk
examples/           settings.json wiring, and a three-memory store to look at
```

Install: [INSTALL.md](INSTALL.md). It explains what each step is for, not just what to type, because a system you installed without understanding is one you will disable the first time it prints something you did not expect.

## Honest limitations

Everything below is known and unfixed. Some of it is a decision, some of it is just not done.

- **Mid-session lag.** A file written during a session is not retrievable until the next sync. Indexing on every write buys little and puts an embedding model in the write path.
- **Eviction is unsolved, and the naive version is harmful.** A dry run of the obvious heuristic (dormant for sixty days, few inbound links) selected almost the entire body of stable working doctrine: the memories that are in context every single session, precisely because they are settled enough that nobody edits or links them. Age measures edit recency, link count measures graph centrality, and neither measures value. Recall-frequency logging exists to answer this with usage data instead. The data is still accumulating, so the organ is not built.
- **Episodic cards are dated by session start.** A session that lives for two weeks produces one card dated to the day it opened. Date-scoped recall against long-lived sessions is therefore approximate.
- **Entity resolution is shallow.** It matches names and filename tokens. Two people sharing a first name get flagged as ambiguous rather than resolved, which is the right failure but not a solution.
- **Compaction is still a black box.** The capture hook fires before compaction, so the transcript survives, but what the session itself retains afterwards is not something these organs can see or measure.
- **The generated-text lint is folklore-prone.** A phrase list is a blunt instrument, and a long one produces noise you learn to ignore. Keep it short or delete the check.
- **Linux and macOS only.** The sync lock uses `fcntl`. Nothing else is platform-specific.
- **Proven at one scale, by one person.** A few hundred documents, several sessions a day, one machine. No test suite. No CI. Read the code before you run it.
- **Not packaged as a Claude Code plugin yet.** Installation is manual file placement plus hook wiring. Packaging it as a plugin (marketplace manifest, namespaced commands) is the obvious next step and is not done.

## Lineage

Nothing here is invented. The retrieval is standard hybrid search with Reciprocal Rank Fusion, the wiki form comes from Cunningham and the pipeline from Luhmann by way of Ahrens, the window-as-scarce-memory framing comes from MemGPT, and the write-time validation pattern was harvested from Pawel Huryn's pm-brain by way of Aakash Gupta's Claude Code memory-layer template. The [page](https://ssshapes.github.io/memory-architecture/) has the full table: for each piece, where it was taken from and what changed in translation.

The one bet that is not borrowed: plain markdown files and a disposable index, rather than a hosted memory service. The corpus is the durable asset. The organs are replaceable, and this repo exists so you can replace them.

**Convergent, independently:** Cal Paterson's [Memoryfield](https://calpaterson.com/memoryfields.html) ([spec](https://github.com/calpaterson/memoryfield-spec)) arrives at the same shape from the other direction: agent memory as plain markdown files with YAML frontmatter and a SQLite vector index alongside, on the argument that memory should be inspectable data rather than a retrieval pipeline or a vendor feature. The schemas map almost field for field (`name`/`title`, `description`/`summary`, `modified`/`updated`). Memoryfield is an interchange format, a zip you can hand to another agent, and it carries two fields this repo lacks, `uuid` and `created`. This repo is the working store: provenance (`originSessionId`), a typed vocabulary, non-destructive supersession, and a curated always-loaded index on top of the same markdown-plus-index foundation. Two people who did not talk to each other choosing files over a database is a better argument for the bet than either could make alone.

## License

MIT. See [LICENSE](LICENSE).
