# Install

Every step below says what it is *for*. A hook you installed without knowing what it closes is a hook you will disable the first time it prints something unexpected, and then the system quietly stops being a system.

The shape you are building: a repo of markdown you write into, a derived index you never edit, and a set of hooks that fire on the agent's own lifecycle events. Roughly twenty minutes, most of it downloading an embedding model.

## Before you start

- Claude Code, with a project you actually work in. This is not useful on an empty repo. It gets useful somewhere around fifty files you have written yourself.
- Python 3.10 or later.
- Linux or macOS. The sync lock uses `fcntl`; nothing else is platform-specific.

---

## 1. A virtual environment for the retrieval dependencies

**Purpose:** keep two unusual dependencies out of your system Python, and make the hook command a fixed path that cannot be broken by a shell that resolved a different interpreter.

```bash
python3 -m venv ~/.venvs/memory
~/.venvs/memory/bin/pip install sqlite-vec fastembed
```

`sqlite-vec` is a SQLite extension that gives you a vector index inside the same database file as the text index, which is why this system needs no database server. `fastembed` runs a small sentence-transformer (all-MiniLM-L6-v2, 384 dimensions, about 90MB) locally, on CPU.

Local embeddings are the point, not an optimization. Every prompt you type gets embedded to search your own notes. If that call went to an API, the record of what you were thinking about, on every prompt, would live somewhere you do not control. It also means no API key, no rate limit, and no network dependency in the prompt path.

The model downloads on first use. Do that now rather than inside a hook (step 4).

## 2. Put the organs in place

**Purpose:** hooks resolve paths relative to the project, and `config.py` derives the repo root from its own location. Putting the files at the standard path means nothing needs configuring to find anything.

```bash
cp -r hooks/ /path/to/your-repo/.claude/hooks/
mkdir -p /path/to/your-repo/.claude/commands
cp commands/*.md /path/to/your-repo/.claude/commands/
cp -r skills/spinoff /path/to/your-repo/.claude/skills/
```

`config.py` expects to sit at `<repo>/.claude/hooks/config.py`, and takes the repo root to be its own grandparent's parent. Keep the hooks somewhere else and set `MEMORY_OS_ROOT` instead.

## 3. Configure the corpus

**Purpose:** the corpus list decides what can ever be recalled. Nothing outside it exists as far as the memory system is concerned, so this is the highest-leverage file in the install.

Open `hooks/config.py`. The defaults work as-is for a store of durable memories plus captured sessions. Two things are worth setting deliberately:

**Where memories live.** The default is Claude Code's own per-project directory (`~/.claude/projects/<flattened-repo-path>/memory/`), so that the agent's memory tooling and these organs read the same files. Set `MEMORY_DIR` if you would rather keep memories inside the repo, or in a private sibling repo.

**What else gets indexed.** `ENTITY_STORES` is where you add folders of one-file-per-thing: people, companies, properties, projects. This is what makes named retrieval sharp. A prompt that says someone's name should surface that person's file first, and that only works if the file exists and is tagged as an entity rather than as prose.

Leave `ROUTINE_SIGNATURES` empty for now. You will fill it in the first time a scheduled job starts minting weekly transcripts you never want to see again (step 8).

## 4. Build the index by hand, once

**Purpose:** prove the dependencies work while you are watching. Every hook here fails open, which is correct in production and terrible for a first install: a missing package would mean the system silently never works, with no error anywhere.

```bash
cd /path/to/your-repo
~/.venvs/memory/bin/python .claude/hooks/memory_sync.py -v
```

Expect a line per indexed file and a summary. The first run downloads the model and takes a minute; later runs are incremental by content hash and take a fraction of a second, since only changed files get re-embedded.

## 5. Look at what retrieval returns, before you wire anything

**Purpose:** this is the whole system. If retrieval returns the wrong things, injecting them automatically makes your sessions worse, not better.

```bash
~/.venvs/memory/bin/python .claude/hooks/memory_recall.py --dry-run "a question you would actually ask"
```

Run it against five or six real prompts. You are looking for the right documents in the top few, and for junk NOT appearing. If a document you expected is missing, it is usually not in the corpus (step 3) rather than ranked badly.

## 6. Wire the hooks

**Purpose:** each hook closes one specific gap. Wire them one at a time and you will know which one caused any behaviour you did not expect.

Copy `examples/settings.single-repo.json` into `<repo>/.claude/settings.json`, or merge its `hooks` block into what you already have.

| Event | Organ | What it closes |
|---|---|---|
| `UserPromptSubmit` | `memory_recall.py` | The model not knowing what you already told it. This is the one that makes the rest matter. |
| `SessionStart` | `memory_sync.py` | The index drifting behind the files. Also cheap: unchanged files are skipped. |
| `SessionStart` | `os_lint.py` | Corpus drift: broken graph edges, memories missing from the index. Silent when clean. |
| `PreCompact` | `memory_episodic.py` | Compaction discarding a conversation that was never written down. |
| `SessionEnd` | `memory_episodic.py` | The same, at normal session close. |
| `PostToolUse` on Write/Edit | `validate_os_memory.py` | Fabricated citation identifiers, and index-file overflow, becoming permanent files. |

Two things to know about the hook path:

**The commands run through a shell**, so `$HOME` and `$CLAUDE_PROJECT_DIR` both expand. That is why the examples have no absolute paths in them.

**`/clear` fires no hooks at all.** A cleared conversation is never captured. That gap is exactly why `commands/save-clear.md` exists: it runs the capture by hand as its final step, so "clear-safe" means something.

## 7. Verify each organ, deliberately

**Purpose:** fail-open means a broken organ is invisible. Check them once now, and you never have to wonder again.

```bash
# recall, in hook mode: expect a JSON blob with additionalContext
printf '{"prompt":"something from your notes","session_id":"test"}' \
  | ~/.venvs/memory/bin/python .claude/hooks/memory_recall.py

# the validator, on a deliberately bad file: expect exit 2 and an explanation
printf '{"tool_input":{"file_path":"<a memory file with an unsourced arxiv id>"}}' \
  | ~/.venvs/memory/bin/python .claude/hooks/validate_os_memory.py; echo "exit=$?"

# the lint: silence means clean
~/.venvs/memory/bin/python .claude/hooks/os_lint.py --verbose

# growth pressure
~/.venvs/memory/bin/python .claude/hooks/metabolism_stats.py
```

Then open a session and confirm the recall block appears in your context. If it does not, check that the interpreter path in `settings.json` is the venv one.

## 8. The capture timer, for sessions that stay open

**Purpose:** capture hooks fire at compaction and at close. A session you leave open for days, below the compaction threshold, holds a conversation that exists nowhere else. Lose the machine and it was never anywhere.

`bin/checkpoint.py` runs the same capture engine from outside every live session. It is deterministic and idempotent, so a later real close-out simply overwrites the checkpoint card with the fuller version. Wire it to a timer at a quiet hour:

```ini
# ~/.config/systemd/user/claude-checkpoint.service
[Unit]
Description=Episodic capture for live Claude Code sessions

[Service]
Type=oneshot
ExecStart=%h/.venvs/memory/bin/python %h/path/to/bin/checkpoint.py
```

```ini
# ~/.config/systemd/user/claude-checkpoint.timer
[Unit]
Description=Nightly episodic checkpoint

[Timer]
OnCalendar=*-*-* 23:50:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now claude-checkpoint.timer
```

Worst-case exposure while the timer is running: one interval of uncaptured conversation. If the timer stops, so does the guarantee — `systemctl --user list-timers` is the thirty-second check that it hasn't.

**While you are here:** if you run scheduled or headless sessions, their transcripts recur forever and contain nothing durable. Put the opening line of each one in `config.ROUTINE_SIGNATURES` and they are never captured. Doing this at the source is much less work than pruning weekly chaff by hand later.

### 8b. The weekly page regenerate

`build_memory_index.py` turns MEMORY.md into a derived view: run it once by hand to see the dry run (it writes a preview under the cache directory and prints who made the page and who fell below the fold), then `--apply`, which backs the old page up under `memory/archive/` first. After that it belongs on a timer, once a week at a quiet hour, and at the end of every `/consolidate`. It needs at least a couple of weeks of `recall_log.db` to rank on; before that, every memory scores by its inbound links and the newborn floor, which is fine.

```ini
# ~/.config/systemd/user/memory-index.service
[Unit]
Description=Regenerate MEMORY.md from measured use

[Service]
Type=oneshot
WorkingDirectory=%h/your-os
ExecStart=/usr/bin/python3 %h/your-os/.claude/hooks/build_memory_index.py --apply

# ~/.config/systemd/user/memory-index.timer
[Unit]
Description=Weekly page regenerate

[Timer]
OnCalendar=Sun 03:00
Persistent=true

[Install]
WantedBy=timers.target
```

```
systemctl --user enable --now memory-index.timer
```

The lint knows the page is derived (it starts with `*DERIVED VIEW`) and stops asking for a line per memory; instead it flags a page older than `INDEX_STALE_DAYS` and any memory file that is absent from the recall index, which is now the layer that guarantees every memory is reachable.

## 9. Several repos, one set of organs

**Purpose:** if you keep work and personal in separate repos, they should not share a memory store, and you should not maintain two copies of the code.

Set `MEMORY_OS` per repo in that repo's `settings.json`, pointing every hook command at the same checkout of the organs. Each name gets its own cache directory, its own index, and its own episodic archive, so the two can never bleed into each other. `examples/settings.multi-repo.json` shows the pattern.

The corpus for each is resolved by name in `config.ENTITY_STORES`, so one config file can describe repos with quite different shapes.

## 10. The part that is not installation

Two of these organs are commands you run, not hooks that fire, and they are the ones that keep the store from rotting:

**`/consolidate`** reads captured sessions and proposes durable memories, merges and retirements. Weekly is about right. It proposes, you approve. A mature store yields few proposals per run, and that is the correct outcome rather than a failure.

**`/save-clear`** is the closing checklist: route what the session produced, force the capture that `/clear` would skip, and print a manifest of what was written where.

Both need a human. That is the design, and it is also the cost. Before you install any of this, answer the question that decides whether it survives: who tends it, and when? An organ with no tending schedule is not an organ, it is a folder that fills up.
