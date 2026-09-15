# Memory architecture

A memory system for [Claude Code](https://claude.com/claude-code) - retrieval, capture, validation and consolidation, wired to the agent's own hook events.

I use it to run a couple of personal operating systems: a few hundred markdown files across three repos, covering a day job, three properties, an EMBA, a family and everything that falls out of those. The corpus goes back to January 2026 and has been evolving since then.

I was working out business and product strategy in chat windows, and the thinking kept drifting.

So I asked what was actually going wrong. The answer split drift in two. One kind is context loss, where the model forgets what you told it. The other is semantic drift, where the language slowly changes: definitions blur, prior decisions get reinterpreted, tone and intent mutate. Mine was the second kind. A friend who'd recently joined a frontier lab told me agents can write notes to themselves, and that some of his colleagues were obsessed with OpenClaw memory techniques.

The problem I care about is dynamic context management: saving what's important and recalling what's important in a way that feels natural, frictionless and additive, with some measure of validation and trustworthiness. Everything in this repo is one attempt at that.

**Start with the page, not the code: [the architecture, explained end to end](https://ssshapes.github.io/memory-architecture/).** It walks one prompt through the whole circulation, names each organ, and gives the lineage for every borrowed idea. The code here is that page made runnable.

This is a nights-and-weekends build. I started from other people's work: the OpenClaw memory-harness ideas, how GrepSeek and Mem0 handle retrieval, and the personal operating system templates Aakash Gupta and Wyndo publish. It's a reference implementation, not a product.

---

## What it does

You type a prompt. Before the model sees it, a hook searches everything you have written, finds the handful of documents that bear on it, and injects them. When the session ends, or compacts, or just sits there for a day, another hook writes the conversation down so it stops being something the window was holding. Every write to the memory store that goes through Claude Code's own Write and Edit tools gets checked the moment it lands, and a bad one is handed back to the agent to fix (the check runs after the write, not before it); a write from outside the hook's reach (your editor, a script) is caught at the next index rebuild instead. Once a week something reads the pile and proposes what should be distilled, merged or retired, and you approve it by hand.

Eleven pieces, each doing one job:

| Organ | Fires on | What it does |
|---|---|---|
| `memory_recall.py` | UserPromptSubmit | Hybrid search (lexical + vector, fused), one hop over your `[[wikilinks]]`, injects the top hits |
| `memory_sync.py` | SessionStart | Rebuilds the index from changed files only |
| `memory_episodic.py` | PreCompact, SessionEnd | Captures the transcript as a verbatim record plus a small indexed card |
| `bin/checkpoint.py` | timer | Runs the capture engine against sessions still alive, so a long session cannot hold days of unwritten conversation |
| `validate_os_memory.py` | PostToolUse (Write, Edit) | Checks each memory write as it lands: unsourced citation identifiers and index-file overflow are flagged back to the agent to repair, quantified claims without a source get a warning. It runs after the write, so it corrects rather than prevents |
| `os_lint.py` | SessionStart | Corpus-wide consistency: broken graph edges, a stale derived page, memories missing from the recall index, generated-text tells |
| `build_memory_index.py` | weekly timer, and at the end of `/consolidate` | Derives MEMORY.md from measured use: pins the rules-of-engagement memories, ranks the rest by recall count and inbound links, fills a fixed byte budget, lists the rest below the fold |
| `memory_archive.py` | you run it | Retires a memory with a reason and a pointer to what replaced it, so the archive answers "what did this used to believe?" instead of being a graveyard |
| `metabolism_stats.py` | on demand, in the closing checklist | Back-pressure: says when the store has grown enough to be worth a pass |
| `recall_stats.py` | on demand | What recall actually surfaces, which is the only honest input to an eviction decision |
| `commands/consolidate.md` | you run it | Distills captured sessions into durable memories. Proposes. Never writes on its own |

Plus two closing commands (`save-clear`, `save-kill`) and a `spinoff` skill, which are the seam discipline around all of it: how a session ends without losing anything, and how work gets handed to a new one.

**Three organs added in September 2026, on the page and in `hooks/`.** The always-loaded index file is no longer written by hand: a generator rebuilds it from measured use (every hit the recall hook returns is counted), pins the rules-of-engagement memories, and fills a fixed byte budget, so the store grows while the loaded page stays one page. Retirement goes through an archive tool that stamps why and what replaced a memory, so the archive can answer "what did this system used to believe?" And because the page may now omit things by design, the lint checks the layer that actually carries reachability: every memory on disk must be in the recall index. The page's section "The derived index" walks all three with diagrams; `build_memory_index.py` and `memory_archive.py` are in `hooks/`, and the coverage check is in `os_lint.py`.

## The four guarantees

The organs are ordinary code. Anyone can write hybrid retrieval in an afternoon. What's worth copying is the conduct, and I learned every one of these by getting it wrong first.

**Propose, never mutate.** No organ deletes, merges or edits a memory on its own. Consolidation writes a proposal and waits. Metabolism prints a pressure line and waits. The consolidation pass once proposed retiring eight captured sessions. Six were genuinely junk. Two weren't, and I only caught it because I was reading a list instead of a diff of something already gone. Forgetting is the one operation you can't audit afterward, because the evidence is the thing that got removed.

**Fail open, everywhere.** Every hook exits 0 on any error. A recall bug injects nothing, a capture bug captures nothing, the session carries on. I'm confident about this one because I got the other half wrong: I had a routine that ran hourly, exited clean every time, and reported "nothing to do" for three hours straight while the tool it needed wasn't loaded at all. Exiting 0 is the right behavior. Exiting 0 without saying what you couldn't do is how a dead system looks healthy.

**Files over database.** Memories are markdown, one durable fact per file, readable and editable with anything, including your hands. The SQLite index is derived and disposable - delete it and the next session rebuilds it. The reason I care: a cached snapshot on my dashboard rendered perfectly for thirty-seven days after the data behind it went stale. It looked more trustworthy than the live thing, because it sat still. Anything that matters should live somewhere you can open without this code.

**Local embeddings.** Retrieval runs on a small model on your machine. The similarity search never leaves the box and no third-party embedding API sees your memory store. Your prompt and whatever the hook injects still go to the model provider, same as any Claude Code session - this bounds what the retrieval layer adds, which is nothing.

## What it deliberately is not

- **Not automatic forgetting.** Eviction is the obvious next organ and it is not built, on purpose. See the limitations below.
- **Not a RAG framework.** It indexes one person's writing, not a document warehouse. Under about ten thousand files this is the right amount of machinery, and past that you want different tools.
- **Not hosted, not synced, not multiplayer.** One machine, one user.

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
  build_memory_index.py  derives MEMORY.md from the recall log (weekly)
  memory_archive.py  retire a memory with a reason and a successor
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
