#!/usr/bin/env python3
"""
config — the one file you edit to fit these organs to your own OS.

Everything else in hooks/ reads its locations, thresholds and skip-lists from
here, so a fork changes this file and leaves the organs alone. Nothing here
requires editing to get a working system: every value has a default that works
for a single repo with a memory directory and nothing else. The lists exist so
that when your corpus grows past that, you add to a config file instead of
patching an organ.

Three questions this file answers:

  WHERE IS THE REPO?    os_root()   — the OS itself (markdown you write)
  WHERE IS THE STORE?   memory_dir(), cache_dir() — what the organs write
  WHAT COUNTS AS CORPUS? corpus_sources() — what the index reads

Resolution order everywhere: environment variable, then a derived default. The
derived defaults assume the layout these hooks ship in:

    <repo>/.claude/hooks/*.py       <- this file
    <repo>/.claude/settings.json    <- the hook wiring

so `os_root()` is this file's grandparent's parent, and nothing is hardcoded to
one machine. Set MEMORY_OS_ROOT if you keep the hooks somewhere else.

MULTI-REPO: one copy of these organs can serve several OS repos. The MEMORY_OS
environment variable, set per repo in that repo's settings.json, selects which
corpus and which index a process operates on. Each repo gets its own SQLite
index under ~/.cache/<name>-memory/, so two repos never collide.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- locations --


def os_root() -> Path:
    """Repo root — the markdown OS these organs serve.

    Default: this file is at <repo>/.claude/hooks/config.py, so parents[2] is
    the repo. Override with MEMORY_OS_ROOT when the hooks live elsewhere (a
    shared checkout serving several repos, for instance).
    """
    env = os.environ.get("MEMORY_OS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def os_name() -> str:
    """Short name of the OS this process is operating on.

    Names the cache directory and appears in recall output ("Relevant context
    from <name> memory"). Default: the repo directory name. Override with
    MEMORY_OS — that is the multi-repo switch.
    """
    env = os.environ.get("MEMORY_OS", "").strip()
    return env or os_root().name


def cache_dir() -> Path:
    """Everything the organs WRITE that is not markdown you'd read by hand:
    the SQLite index, the episodic archive, the recall log, the provenance
    ledger. Deliberately outside the repo — it churns, it is binary, and it
    must not end up in your git history or a file-sync client's queue.
    """
    env = os.environ.get("MEMORY_CACHE_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".cache" / f"{os_name()}-memory"


def memory_dir() -> Path:
    """The semantic store: one markdown file per durable fact, plus MEMORY.md
    as the always-loaded index.

    Default is Claude Code's per-project directory, whose name is the repo path
    with separators flattened to dashes (a repo at /srv/my-os becomes the
    project directory -srv-my-os under ~/.claude/projects/). Putting
    memories there means the agent's own memory tooling and these organs read
    the same files. Override with MEMORY_DIR to keep memories in the repo, in a
    private sibling repo, or anywhere else.
    """
    env = os.environ.get("MEMORY_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    slug = "-" + str(os_root()).strip("/").replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


def episodic_dir() -> Path:
    """Session transcripts, captured automatically. Cards here are indexed;
    the verbatim transcripts under episodic/full/ are not (greppable, not
    retrievable — they would swamp the index)."""
    return cache_dir() / "episodic"


def db_path() -> Path:
    """The retrieval index. Rebuildable from the corpus at any time: delete it
    and the next sync reconstructs it. Nothing here is a source of truth."""
    return cache_dir() / "index.db"


# ------------------------------------------------------------------- corpus --
# What the index reads. Each entry is (kind, path-relative-to-repo, recursive,
# skip-basenames). `kind` is a free-form tag that shows up in recall output and
# can be filtered on; it is also how one-hop wikilink expansion and the entity
# registry decide what a document IS.
#
# Two kinds are special:
#   "memory"  — the semantic store (paths starting with "@memory" resolve there)
#   "episode" — captured transcripts (paths starting with "@episodic")
#
# The default corpus is the minimum honest system: durable memories, archived
# memories, and captured sessions. ENTITY_STORES below shows the shape of the
# next step — folders of one-file-per-thing — which is what makes named
# retrieval ("what do I know about X") sharp rather than fuzzy.

DEFAULT_SOURCES = [
    ("memory",  "@memory",          False, {"MEMORY.md"}),
    ("memory",  "@memory/archive",  False, set()),
    ("episode", "@episodic",        False, set()),
]

# Per-OS additions, keyed by os_name(). Uncomment / edit to match your repo.
# Anything listed here that does not exist on disk is skipped silently, so a
# shared config can name folders only some repos have.
ENTITY_STORES: dict[str, list] = {
    # "my-os": [
    #     ("person",   "people",             False, {"CLAUDE.md", "index.md"}),
    #     ("company",  "research/companies", False, {"_template.md"}),
    #     ("property", "properties",         True,  set()),
    # ],
}

# Kinds treated as ENTITIES: one file = one nameable thing, so the name can be
# registered as an alias and pinned when a prompt says it. Only worth it for
# stores where that assumption holds — a folder of one-file-per-person, not a
# folder of notes. ENTITY_KINDS get alias entries; MENTION_KINDS are what an
# episodic card counts to decide what a conversation was about.
ENTITY_KINDS = ("person", "company")
MENTION_KINDS = ("person", "company", "property")

# Basenames never indexed anywhere: navigation and index files. They describe
# the corpus rather than being part of it, and they rank well on every query
# precisely because they mention everything.
GLOBAL_SKIP = {"CLAUDE.md", "MEMORY.md", "README.md"}


def corpus_sources(name: str | None = None) -> list:
    """Resolved corpus for this OS: (kind, absolute Path, recursive, skip)."""
    name = name or os_name()
    root, mem, epi = os_root(), memory_dir(), episodic_dir()
    out = []
    for kind, rel, recursive, skip in DEFAULT_SOURCES + ENTITY_STORES.get(name, []):
        if rel.startswith("@memory"):
            path = mem / rel[len("@memory"):].lstrip("/")
        elif rel.startswith("@episodic"):
            path = epi / rel[len("@episodic"):].lstrip("/")
        else:
            path = root / rel
        out.append((kind, path, recursive, set(skip)))
    return out


# ------------------------------------------------------- retrieval tuning ----
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # local, 384d, ~90MB
EMBED_DIM = 384
MAX_RECALL_RESULTS = 8      # hits injected per prompt. Higher = more context tax.
RRF_K = 60                  # Reciprocal Rank Fusion constant (the standard 60)
CANDIDATES_PER_ARM = 40     # how deep FTS and vector each go before fusion
LINK_EXPANSION = 3          # extra docs pulled in over [[wikilinks]] from hits

# Tokens that must never, alone, identify an entity. Add the words your own
# corpus is full of — institutions, industries, product lines — or a query
# mentioning them will pin every entity that shares the word.
EXTRA_ALIAS_STOPWORDS: set[str] = set()


# ------------------------------------------------------- episodic capture ----
# A transcript becomes a card only if it is substantive. Without these gates,
# trivial sessions ("hi" / "thanks") become documents, and because they are
# short they are excellent vector matches for other short prompts — junk
# recalls junk. Observed, not theoretical.
EPISODIC_MIN_TURNS = 4          # below this, and no entity mentions -> no card
EPISODIC_MIN_CHARS = 400        # total conversation characters, same rule
EPISODIC_MENTION_MIN = 3        # times an entity must appear to count as central

# Scheduled/automated sessions whose real output lands somewhere else: their
# transcripts are recurring chaff. Any session whose FIRST user message starts
# with one of these prefixes is never captured. Fill this with the opening
# lines of your own cron/headless prompts — exact prefixes, checked with
# startswith().
ROUTINE_SIGNATURES: tuple[str, ...] = (
    # "You are running the weekly digest routine",
    # "You are the nightly inbox sweep",
)


# -------------------------------------------------- provenance validator ----
# Roots the write-time validator inspects, relative to the repo. The memory
# directory is always audited. Everything outside these roots passes through
# untouched — the validator is a gate on the stores that must stay trustworthy,
# not a linter for your whole repo.
AUDITED_SUBPATHS: tuple[str, ...] = (
    "people",
)

# MEMORY.md is an always-loaded index, which makes it a cache with a read
# ceiling, not a document. Past roughly 24KB an agent silently truncates it and
# the bottom of your index stops existing — a failure with no error message.
# These caps sit under that cliff so the block fires while the file still loads
# whole. BLOCK is enforced at write time (exit 2, the agent fixes and retries);
# WARN is advisory.
INDEX_BLOCK_BYTES, INDEX_WARN_BYTES = 21_000, 18_000
INDEX_BLOCK_LINE, INDEX_WARN_LINE = 250, 200


# ------------------------------------------------------------ corpus lint ----
# Folders whose prose is written BY the agent, and so should be checked for
# the tells of generated text. Do not point this at sources you have merely
# collected: quoting someone else's writing is not your model writing badly.
SLOP_DIRS: tuple[str, ...] = (
    # "wiki",
    # "digests",
)

SLOP_PATTERNS = (
    r"\b(delv(?:e[sd]?|ing)|seamless(?:ly)?|game-?chang(?:er|ing)|"
    r"unlock(?:ing)?\s+value|let'?s\s+dive|leverag(?:ing|es))\b|"
    r"it'?s\s+important\s+to\s+note|in\s+today'?s\s+fast-?paced|great\s+question"
)

# Directories never walked by the lint or the corpus scan.
SKIP_DIRS = {".git", ".claude", "node_modules", "__pycache__", "scratch", "_archived"}


# -------------------------------------------------------------- metabolism ----
# Back-pressure thresholds. Crossing one prints a PRESSURE line suggesting a
# consolidation pass; nothing is ever deleted automatically. These are soft
# doctrine for a knowledge base, not hard caps for a working set — tune them to
# how much index your agent can carry without crowding out the actual work.
ENTRY_PRESSURE = 150        # MEMORY.md index lines
DORMANT_DAYS = 60           # untouched memory files
DORMANT_PRESSURE = 50       # how many dormant files before it is worth a pass
EPISODIC_DAYS = 14          # cards older than this the consolidator hasn't read
EPISODIC_PRESSURE = 10


# --------------------------------------------------------------- checkpoint ---
# Sessions that stay open for days only get captured at compaction or close.
# The checkpoint runs the capture engine from outside every live session on a
# timer, so the worst case is one day of uncaptured conversation.
CHECKPOINT_ACTIVE_WINDOW_H = 26   # transcript touched this recently = live
CHECKPOINT_MIN_BYTES = 5_000      # below this there is nothing worth a card

# (claude-projects-dir, MEMORY_OS value) pairs the checkpoint sweeps. Empty =
# derive one entry from this config. Add a line per repo for a multi-repo box.
CHECKPOINT_TARGETS: list[tuple[str, str]] = []
