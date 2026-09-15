#!/usr/bin/env python3
"""
build — produce the public organs from a private OS, and refuse to publish a leak.

This repo is a reference implementation extracted from a working private system.
The extraction is a script rather than a one-time copy-paste for one reason: the
private originals keep changing, and a copy edited by hand rots silently. Run
this before every publish and the extraction is re-proved instead of assumed.

    python3 build.py --source /path/to/your-private-os
    python3 build.py --check          # leak-check the tree, build nothing

WHAT IS DERIVED vs WHAT IS WRITTEN BY HAND

  DERIVED (this script generates them; do not edit by hand, your edit is lost
  on the next build):
      hooks/memory_lib.py        hooks/memory_recall.py
      hooks/memory_sync.py       hooks/memory_episodic.py
      hooks/validate_os_memory.py  hooks/os_lint.py
      hooks/metabolism_stats.py  hooks/recall_stats.py
      bin/checkpoint.py

  HAND-WRITTEN (generic from birth, never contained private content):
      hooks/config.py   README.md   INSTALL.md   LICENSE
      commands/*.md     skills/*    examples/*   index.html

  HAND-PORTED (private originals rewritten against config.py by hand, once;
  the build leaves them alone, the leak check does not):
      hooks/build_memory_index.py   hooks/memory_archive.py

  Both classes are leak-checked. The check is the guarantee; the derivation is
  only how the code class gets there.

HOW THE TRANSFORMS WORK

Every transform is anchored: it asserts the text it expects to find, and the
build dies if that anchor is gone. So when a private organ is refactored, this
script fails loudly at the changed transform instead of quietly shipping the
un-genericized version. That is the entire safety model of the code path — an
un-asserted `replace()` is a leak waiting for a refactor.

Anchors themselves must never quote private content. Where a private line
contains a name or a path, the anchor is a regex over the surrounding structure.
That is why several transforms below look more indirect than they need to be:
this file is public too.

THE LEAK CHECK

Two tiers, both fatal:

  derived bans — read from the private side at build time, never stored here:
      · a required ban file in the private repo (identity tokens)
      · every filename in the private people store (real individuals)
      · the private repo's own path, and this machine's home directory
  pattern bans — email addresses, private-range and CGNAT IPs, home-directory
      and mount-point absolute paths.

The only allowlist is the author byline on index.html, which is deliberate: the
page is signed. Everything else, everywhere in the tree, fails the build.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# transform primitives
# ---------------------------------------------------------------------------


class BuildError(RuntimeError):
    pass


class Organ:
    """One file in transit from private original to public artifact."""

    def __init__(self, name: str, text: str):
        self.name = name
        self.text = text
        self.applied: list[str] = []

    def sub(self, old: str, new: str, why: str, count: int = 1) -> None:
        found = self.text.count(old)
        if found != count:
            raise BuildError(
                f"{self.name}: transform lost its anchor ({why}) — expected {count} "
                f"occurrence(s), found {found}: {old[:70]!r}")
        self.text = self.text.replace(old, new)
        self.applied.append(why)

    def resub(self, pattern: str, new: str, why: str, count: int = 1, flags: int = 0) -> None:
        rx = re.compile(pattern, flags)
        found = len(rx.findall(self.text))
        if found != count:
            raise BuildError(
                f"{self.name}: transform lost its anchor ({why}) — expected {count} "
                f"match(es), found {found} for /{pattern[:70]}/")
        self.text = rx.sub(lambda m: new, self.text)
        self.applied.append(why)

    def docstring(self, new: str) -> None:
        """Replace the whole module docstring. Private modules carry dated build
        notes, incident references and internal file paths in theirs; the public
        ones get written here, where they can be read as documentation."""
        rx = re.compile(r'\A(#![^\n]*\n)?"""(.*?)"""\n', re.DOTALL)
        if not rx.match(self.text):
            raise BuildError(f"{self.name}: no module docstring to replace")
        self.text = rx.sub(lambda m: f'#!/usr/bin/env python3\n"""\n{new.strip()}\n"""\n',
                           self.text, count=1)
        self.applied.append("module docstring")

    def forbid(self, pattern: str, why: str, flags: int = re.IGNORECASE) -> None:
        """Post-condition on a single file: this pattern must be gone."""
        hits = re.findall(pattern, self.text, flags)
        if hits:
            raise BuildError(f"{self.name}: {why} — still present: {hits[:5]}")


# Post-condition applied to every derived file: no absolute path from the machine
# it was extracted on. Instance NAMES are checked tree-wide by the leak check,
# using patterns derived from the private side — they are deliberately not
# spelled out here, because this file is published too.
ABSOLUTE_PATHS = r"/mnt/|/home/|/Users/"

CONFIG_IMPORT = (
    "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
    "import config as cfg  # noqa: E402 — the path insert must precede this import\n"
)


# ---------------------------------------------------------------------------
# public docstrings
# ---------------------------------------------------------------------------

DOC_LIB = """
memory_lib — the retrieval engine. Everything else in hooks/ is an envelope
around this file.

THE STORE: one local SQLite file (kept out of the repo — it is a churning
binary) holding, for every markdown file in the corpus:

    docs      metadata: path, kind, title, summary, sha256, [[wikilinks]]
    docs_fts  FTS5/BM25 lexical index over title + body
    vec       sqlite-vec KNN index over a 384-dimension local embedding
    entities  name -> document registry, for exact-name pinning

RETRIEVAL: lexical (FTS5) and semantic (vector) search run independently, and
their two ranked lists are fused with Reciprocal Rank Fusion — the fusion, not
either arm alone, is what makes this work. Lexical alone misses paraphrase;
vector alone drifts to whatever is topically adjacent and is actively harmful on
short prompts. Then one hop is taken over the [[wikilink]] graph you wrote by
hand, which is the cheapest source of relevance in the system: a human already
said these two documents are related.

Before either arm runs, entities the prompt NAMES are pinned to the top. A query
that says someone's name should return that person's file first, not eighth;
soft ranking is bad at this and exact matching is trivially good at it.

Embeddings are computed locally (fastembed, all-MiniLM-L6-v2). Nothing in the
retrieval path makes a network call, which is not a performance decision — a
memory system that phones home about what you asked is a different product.

Design rule: fail open, everywhere. Callers are hooks in the prompt path. A
retrieval bug must degrade to no-context, never to a broken session.

Dependencies: the standard library, sqlite-vec, fastembed.
"""

DOC_RECALL = """
UserPromptSubmit hook — relevance-based recall.

Reads the prompt, runs hybrid retrieval against the index, and injects the top
hits as additionalContext. This is the organ that makes the rest matter: a
memory store nothing reads at the right moment is a filing cabinet.

    memory_recall.py                      hook mode, reads JSON on stdin
    memory_recall.py --dry-run "<prompt>" what it would inject, human-readable

The dry-run is the tuning tool. Run it against prompts you actually type before
changing any threshold; retrieval quality is not something to reason about in
the abstract.

Every injected hit is also logged to a small separate database. That log answers
the only honest question about eviction — what does recall ACTUALLY surface —
and it lives outside the main index so that rebuilding the index can never
destroy the usage history. See recall_stats.py.

Fail-open: any error injects nothing and returns 0. A recall bug must never cost
you a prompt.
"""

DOC_SYNC = """
memory_sync — (re)build the retrieval index from the markdown corpus.

Wired to SessionStart, and safe to run by hand after a large edit.

    memory_sync.py            incremental sync, print stats
    memory_sync.py -v         list each new/updated file
    memory_sync.py --rebuild  drop the index and reindex from scratch

Incremental by sha256: unchanged files are not re-embedded, so a normal sync is
milliseconds and only new writing costs anything.

Single-flight by lock file, because every open session fires SessionStart. Ten
sessions starting together should produce one sync, not ten racing writers.

KNOWN LAG, not worked around: a file written mid-session is not retrievable
until the next sync. The alternative — indexing on every write — buys little and
costs a write-path dependency.
"""

DOC_EPISODIC = """
Episodic capture — snapshot the conversation so it survives the window.

Fires on PreCompact (before compaction discards context) and SessionEnd. Reads
the session transcript named in the hook payload, keeps the user and assistant
TEXT turns, and writes two files:

    episodic/full/<date>_<id>.md   verbatim transcript — NOT indexed, greppable
    episodic/<date>_<id>.md        small card — indexed, points at the full text

The split is the whole design. Indexing full transcripts would swamp retrieval
with conversational filler; indexing nothing would mean sessions cease to exist
at close. The card carries what the session was ABOUT — the entities it kept
returning to — so a later question can find the conversation, and the verbatim
record is one hop away when it turns out to matter.

TWO GATES, both learned by being burned:

  ROUTINE GATE — scheduled/automated runs whose real output goes somewhere else
  produce recurring transcripts with no durable content. Cards for them are pure
  recall chaff, weekly. Configure the opening lines of your automated prompts in
  config.ROUTINE_SIGNATURES and they are never captured.

  JUNK GATE — a two-turn session is not an episode. This matters more than it
  sounds: short junk cards are short, and short documents are excellent vector
  neighbours for short prompts, so unfiltered junk does not sit there harmlessly,
  it actively crowds out real hits.

Idempotent: re-running overwrites that session's files with the fuller version.
Fail-open: any error exits 0 and captures nothing, never blocking compaction or
session end.
"""

DOC_VALIDATOR = """
PostToolUse hook — write-time provenance validator.

Runs after every Write/Edit and inspects ONLY files under the audited roots (the
memory store, plus whatever you list in config.AUDITED_SUBPATHS). Everything
else passes through instantly and untouched.

It exists to make one failure structural instead of behavioural. A model that
fabricates a citation identifier and writes it down as fact has not made a
transient error — it has written a durable file that will be retrieved later and
believed. "Don't assert an unsourced identifier" enforced by the harness beats
the same rule enforced by the model's good intentions.

THREE CHECKS, two tiers:

  BLOCKING (exit 2; the message goes back to the model, which fixes and retries)
    · an arxiv-style ID with no resolvable link
    · a DOI with no resolvable link
    · index-file health: MEMORY.md over the byte cap, or index lines over the
      length cap (see below)
  WARNING (exit 0 + stderr)
    · internal markdown links that do not resolve
    · message-ID-shaped hex with no source
    · quantified external claims — funding/valuation figures, benchmark metrics
      — written with no provenance signal (a link, a [[wikilink]], a "per <X>"
      attribution, or an explicit tag). Warn-only on purpose: it teaches a
      writing convention, and a false block on your own notes is worse than a
      missed warning.

THE INDEX GATE deserves its own note, because the failure it prevents is
invisible. MEMORY.md is loaded into every session, which makes it a cache with a
read ceiling rather than a document. Past roughly 24KB it gets silently
truncated and the bottom of the index simply stops existing — no error, no
warning, just memories that quietly stop being findable. The caps sit under that
cliff so the block fires while the file still loads whole.

DELIBERATELY NOT CHECKED: unresolved [[wikilinks]]. Forward-reference links to
files that do not exist yet are a feature — they mark what is worth writing
next. A validator that fights your conventions gets disabled.

Every warning and block is appended to a JSONL ledger, because "should this
warning become a block?" is unanswerable without data on how often it fires.

Fail-open: any exception, unreadable file, or unparseable payload exits 0. A
validator must never be the reason a legitimate write fails.

Lineage: the write-time-gate pattern was harvested from Pawel Huryn's pm-brain
validator (github.com/phuryn/pm-brain) by way of Aakash Gupta's Claude Code
memory-layer template. Those audit a PM evidence schema; this is a from-scratch
rewrite for prose memory files, not a port.
"""

DOC_LINT = """
SessionStart hook — corpus-wide consistency lint.

The provenance validator checks each file as it is written. This is its
corpus-wide sibling: the mechanical part of a manual audit, run at every session
open so the same drift classes cannot silently re-accumulate. It prints ONLY
when something is wrong — a healthy corpus costs zero context.

FOUR CHECKS, mechanical only. Judgment classes (stale statuses, cross-file
contradictions) stay with the human and the model; a lint that guesses at
meaning produces noise you learn to scroll past.

  1. WIKILINK NEAR-MISSES — a [[target]] resolving to no file, whose
     hyphen/underscore/case variant DOES exist. These are broken graph edges
     with an unambiguous fix, and every one of them silently degrades the
     one-hop expansion in retrieval. Genuinely dangling forward-references are
     NOT reported: they are allowed on purpose.
  2. INDEX INTEGRITY, both directions — the index points at a file that does not
     exist, or a memory file exists that the index never mentions. The second
     direction is the one that matters: an unindexed memory is invisible to
     every session that loads the index.
  3. DOC-INDEX REFERENCES — a backtick-quoted *.md path in a CLAUDE.md that
     resolves nowhere. Checked against the folder, the repo root, and by
     basename anywhere below, which is what keeps this from crying wolf.
  4. SLOP TELLS — generated-text vocabulary in prose the AGENT wrote (configure
     which folders in config.SLOP_DIRS). Turns a style rule into a check
     instead of a hope. Precision over recall: a short high-signal phrase list,
     and quoted lines are skipped so that quoting someone else's prose is not
     an offence.

Read-only, fail-open. It never modifies a file and never breaks a session start.

    os_lint.py            report
    os_lint.py --verbose  also print the counts when clean
"""

DOC_METABOLISM = """
metabolism_stats — deterministic back-pressure on a growing store.

Prints one line of corpus health, and appends a PRESSURE line only when a
threshold worth acting on is crossed. Quiet when healthy, like the lint.

    entries    index lines in MEMORY.md
    bytes      index size against the validator's caps
    dormant    memory files untouched for a long time
    episodic   captured sessions the consolidator has not distilled yet

The one design rule: pressure is a FORCING PROMPT, never an action. It says the
store has grown enough to be worth a consolidation pass, and a human decides
what that pass does. An organ that deletes memories on a threshold is not a
metabolism, it is a leak with a schedule.

Thresholds live in config.py and are soft doctrine, not caps. Tune them to how
much index your agent can carry without crowding out the actual work.

Stdlib only. Always exits 0 — informational, never a gate.
"""

DOC_RECALL_STATS = """
recall_stats — read the recall log and answer the eviction question honestly.

Every memory store eventually asks: what can I drop? The obvious heuristics are
wrong, and wrong in a specific direction. Age plus link-count over-selects the
most load-bearing documents in the store: stable working doctrine is rarely
edited and rarely linked, and it is in context every single session. Those two
metrics measure edit-recency and graph-centrality. Neither one measures value.

The honest metric is what recall actually surfaces. memory_recall.py logs every
injected hit; this reads the log.

    recall_stats.py                summary: top-recalled, never-recalled
    recall_stats.py --days 30      window (default 30)
    recall_stats.py --all-kinds    include entity documents in the tables

RULE OF THUMB, and do not act on it before a full window of data exists: a
memory with ZERO recalls across the window AND a long time since its last edit
is an archive candidate. Archive, do not delete — the archive stays indexed and
retrievable, it just leaves the always-loaded index. Eviction from a memory
system should be reversible; forgetting is the one operation you cannot audit
after the fact.
"""

DOC_CHECKPOINT = """
checkpoint — episodic capture for sessions that are still alive.

The gap this closes: capture hooks fire at compaction and at session end, so a
long-running session that idles below the compaction threshold can carry days of
conversation that exist nowhere but its own window. Close it badly — or lose the
machine — and that conversation was never anywhere.

This runs the SAME capture engine (memory_episodic.py) from OUTSIDE every live
session, on a timer. No interaction with the session, no model calls: the engine
is deterministic and idempotent, so a later real close-out simply overwrites the
checkpoint card with the fuller version.

Sweeps every transcript touched recently, in each configured project directory,
and skips any whose card is already newer than the transcript.

Worst-case exposure with this running: one interval of uncaptured conversation
in any session, ever.

    checkpoint.py           sweep now (safe any time — idempotent)

Wire it to a timer (systemd user timer, launchd, cron) at a quiet hour. See
INSTALL.md.
"""


# ---------------------------------------------------------------------------
# per-organ transforms
# ---------------------------------------------------------------------------


def t_memory_lib(o: Organ) -> None:
    o.docstring(DOC_LIB)

    # config import; `os` becomes unused once the env lookups move to config
    o.sub("import json\nimport os\nimport re\n", "import json\nimport re\n",
          "drop now-unused os import")
    o.sub("import sqlite3\nfrom pathlib import Path\n",
          "import sqlite3\nimport sys\nfrom pathlib import Path\n\n" + CONFIG_IMPORT,
          "config import")

    # the whole locations + corpus-registry block is instance-specific: absolute
    # paths, and a hardcoded list of one OS's entity folders.
    o.resub(
        r"# ----- locations \(per-repo, env-driven\).*?SOURCES = _sources_for\(OS_NAME\)\n",
        """# ----- locations (config-driven) ---------------------------------------------
# Every path comes from config.py, so a fork edits one file and leaves the
# organs alone. MEMORY_OS selects which OS this process serves when one copy of
# these hooks is wired into several repos; each gets its own index.
OS_NAME = cfg.os_name()
OS_ROOT = cfg.os_root()
_CACHE = cfg.cache_dir()
DB_PATH = cfg.db_path()
EPISODIC_DIR = cfg.episodic_dir()
MEMORY_DIR = cfg.memory_dir()

EMBED_MODEL = cfg.EMBED_MODEL
EMBED_DIM = cfg.EMBED_DIM

# corpus: (kind, root, recursive, skip-basenames) — see config.corpus_sources().
SOURCES = cfg.corpus_sources(OS_NAME)
""",
        "locations + corpus registry -> config", flags=re.DOTALL)

    # alias stop-list: institution / place words specific to one corpus
    o.resub(r'    "capital","studio","studios",[^\n]*\n',
            '    "capital","studio","studios","new","old","house","street","road",\n',
            "alias stop-list: drop instance-specific words")
    o.sub('    "de","van","von","la","le","el","al","da","di","san",  # name connectors\n}\n',
          '    "de","van","von","la","le","el","al","da","di","san",  # name connectors\n}\n'
          "_ALIAS_STOP |= cfg.EXTRA_ALIAS_STOPWORDS  # the generic words in YOUR corpus\n",
          "alias stop-list extension point")

    o.sub('GLOBAL_SKIP = {"CLAUDE.md", "MEMORY.md"}  # navigation/index files, not content',
          "GLOBAL_SKIP = cfg.GLOBAL_SKIP  # navigation/index files, not content",
          "global skip -> config")

    # entity registry: which kinds get alias entries
    o.resub(r'    for doc_id, kind, title, stem in db\.execute\(\n'
            r'            "SELECT id, kind, title, stem FROM docs WHERE kind IN \([^)]*\)"\):',
            '    placeholders = ",".join("?" * len(cfg.ENTITY_KINDS))\n'
            '    for doc_id, kind, title, stem in db.execute(\n'
            '            f"SELECT id, kind, title, stem FROM docs WHERE kind IN ({placeholders})",\n'
            '            tuple(cfg.ENTITY_KINDS)):',
            "entity kinds -> config")

    # tuning constants
    o.sub("def _rrf(*ranked_lists, k=60):", "def _rrf(*ranked_lists, k=cfg.RRF_K):",
          "RRF constant -> config")
    o.sub("def retrieve(db, query, k=6, n=40, expand=3):",
          "def retrieve(db, query, k=cfg.MAX_RECALL_RESULTS, n=cfg.CANDIDATES_PER_ARM,\n"
          "             expand=cfg.LINK_EXPANSION):",
          "retrieval defaults -> config")

    # comments naming real people from the private corpus
    o.resub(r"    ambiguous = an alias the prompt used maps to >1 entity \([^)]*\)\.",
            "    ambiguous = an alias matching >1 entity (two people who share a name).",
            "resolve() docstring: named example")
    o.resub(r"    # Seed 3: pin entities the prompt explicitly names \(fixes soft ranking for\n"
            r"    # named queries:[^\n]*\n",
            "    # Pin the entities the prompt NAMES: an exact name should outrank soft\n"
            "    # similarity, and a name matching two entities should be flagged, not merged.\n",
            "retrieve() comment: named example")
    o.resub(r"    # collisions surfaced up front and flagged \(so the model disambiguates rather\n"
            r"    # than treating two [^\n]*\n",
            "    # collisions surface first, carrying a flag, so the model disambiguates\n"
            "    # instead of silently treating two same-named entities as one hit\n",
            "retrieve() comment: collision example")

    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_memory_recall(o: Organ) -> None:
    o.docstring(DOC_RECALL)
    o.sub("sys.path.insert(0, str(Path(__file__).resolve().parent))\n\nMAX_RESULTS = 8\n",
          CONFIG_IMPORT + "\nMAX_RESULTS = cfg.MAX_RECALL_RESULTS\n",
          "config import + recall width")
    o.resub(r'    """Recall-frequency log \(\d{4}-\d{2}-\d{2}\): the usage signal.*?"""',
            '    """Log every injected hit. This is the data eviction decisions need:\n'
            '    what recall ACTUALLY surfaces, as opposed to what looks unused by age or\n'
            '    link count (both of which flag load-bearing doctrine — see recall_stats).\n'
            '    Separate database from the index, so no rebuild can ever wipe the usage\n'
            '    history. Fail-open, about a millisecond."""',
            "recall-log docstring", flags=re.DOTALL)
    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_memory_sync(o: Organ) -> None:
    o.docstring(DOC_SYNC)
    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_memory_episodic(o: Organ) -> None:
    o.docstring(DOC_EPISODIC)
    o.sub("sys.path.insert(0, str(Path(__file__).resolve().parent))\n",
          CONFIG_IMPORT, "config import")

    # the injected-context marker names the instance's OS in the private copy
    o.resub(r'        "<local-command", "Caveat:", "<command-name>",\n'
            r'        "[^"]*", "Current datetime:"\)\)',
            '        "<local-command", "Caveat:", "<command-name>",\n'
            '        "Relevant context from", "Current datetime:"))',
            "injected-context markers")

    # transcript role label names the instance's operator. The SEARCH side is a
    # regex so this build script never spells the name it exists to strip; the
    # replacement is literal (resub replacements are always literal — no
    # backreferences), anchored to the user branch via the _looks_injected line.
    o.resub(r'_looks_injected\(text\):\n                continue\n'
            r'            turns\.append\(\("\w+", ts, text\)\)',
            '_looks_injected(text):\n                continue\n'
            '            turns.append(("User", ts, text))',
            "role label")

    # frequency-gate docstring cites a real person by name and count
    o.resub(r'    """Entities the transcript is actually ABOUT — frequency-gated so a card names\n'
            r'    the central few [^\n]*\n',
            '    """Entities the transcript is actually ABOUT — frequency-gated, so a card\n'
            '    names the few things a session kept returning to rather than every name\n'
            '    that flickered past once."""\n',
            "mentions docstring: named example")
    o.resub(r'        rows = db\.execute\("SELECT title, stem FROM docs WHERE kind IN \([^)]*\)"\)\.fetchall\(\)',
            '        placeholders = ",".join("?" * len(cfg.MENTION_KINDS))\n'
            '        rows = db.execute(\n'
            '            f"SELECT title, stem FROM docs WHERE kind IN ({placeholders})",\n'
            '            tuple(cfg.MENTION_KINDS)).fetchall()',
            "mention kinds -> config")
    o.resub(r"        if count >= 3:                       # central, not incidental",
            "        if count >= cfg.EPISODIC_MENTION_MIN:   # central, not incidental",
            "mention threshold -> config")

    # the routine gate: the private list is one instance's cron prompts
    o.resub(r"    # Routine gate \([^)]*\).*?    _ROUTINE_SIGS = \(\n.*?\n    \)\n"
            r"    if any\(_fu\.startswith\(s\) for s in _ROUTINE_SIGS\):\n        return 0\n",
            """    # Routine gate: scheduled/headless runs file their real output somewhere
    # else, and their transcripts recur forever. Capturing them is pure recall
    # chaff, weekly. Configure the opening lines of your automated prompts in
    # config.ROUTINE_SIGNATURES; matched sessions are never captured.
    _fu = (first_user or "").lstrip()
    if any(_fu.startswith(s) for s in cfg.ROUTINE_SIGNATURES if s):
        return 0
""",
            "routine gate -> config", flags=re.DOTALL)
    # junk gate thresholds
    o.resub(r"    if not mentions and \(len\(turns\) < 4 or total_chars < 400\):",
            "    if not mentions and (len(turns) < cfg.EPISODIC_MIN_TURNS\n"
            "                         or total_chars < cfg.EPISODIC_MIN_CHARS):",
            "junk gate -> config")

    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_validator(o: Organ) -> None:
    o.docstring(DOC_VALIDATOR)
    o.sub("import json\nimport os\nimport re\nimport sys\nfrom pathlib import Path\n"
          "from urllib.parse import unquote\n",
          "import json\nimport os\nimport re\nimport sys\nfrom pathlib import Path\n"
          "from urllib.parse import unquote\n\n" + CONFIG_IMPORT,
          "config import")

    # provenance examples cite the instance's operator by name (regex, so this
    # build script never spells the name it exists to strip)
    o.resub(r"\(intuition, \w+, <date>\)",
            "(intuition, <who>, <date>)", "operator name in provenance example")
    o.resub(r'# "per \w+", "per the posting", "per LinkedIn"',
            '# "per <name>", "per the posting", "per LinkedIn"',
            "operator name in per-attribution example")

    # audited roots: absolute instance paths + one instance's folder history
    o.resub(r"# ----- audited roots ------.*?def _is_audited",
            '''# ----- audited roots ---------------------------------------------------------
# Only these roots are inspected; everything else passes through instantly. The
# memory store is always audited. config.AUDITED_SUBPATHS adds repo folders whose
# contents must stay trustworthy (entity records, research notes). Roots that do
# not exist are harmless — nothing under them ever matches.
# Override for testing with CLAUDE_MEMORY_VALIDATOR_ROOTS (os.pathsep-separated).
def _os_name() -> str:
    return cfg.os_name()


def _audited_roots() -> list[Path]:
    override = os.environ.get("CLAUDE_MEMORY_VALIDATOR_ROOTS")
    if override:
        return [Path(p).resolve() for p in override.split(os.pathsep) if p.strip()]
    roots: list[Path] = []
    try:
        roots.append(cfg.memory_dir().resolve())
        repo = cfg.os_root()
        for rel in cfg.AUDITED_SUBPATHS:
            roots.append((repo / rel).resolve())
    except Exception:
        pass
    return roots


def _is_audited''',
            "audited roots -> config", flags=re.DOTALL)

    # index gate: the comment recounts one instance's incident; caps -> config
    o.resub(r"# ----- index gate \(v3, [^)]*\) -+\n.*?_INDEX_BLOCK_LINE, _INDEX_WARN_LINE = \d+, \d+\n",
            """# ----- index gate -------------------------------------------------------------
# MEMORY.md is an always-loaded pointer index: a cache with a hard read ceiling,
# not a document. Past roughly 24KB it is silently truncated and the bottom of
# the index stops existing — no error, just memories that quietly stop being
# findable. This gate makes that failure class uncreatable. Caps sit under the
# cliff so the block fires while the file still loads whole, and the fix is
# always available in-turn: compress a line to a pointer, push the detail into
# the memory file it points at, or evict a stale entry.
_INDEX_BLOCK_BYTES, _INDEX_WARN_BYTES = cfg.INDEX_BLOCK_BYTES, cfg.INDEX_WARN_BYTES
_INDEX_BLOCK_LINE, _INDEX_WARN_LINE = cfg.INDEX_BLOCK_LINE, cfg.INDEX_WARN_LINE
""",
            "index caps -> config", flags=re.DOTALL)

    # ledger location
    o.resub(r"# ----- warn ledger \([^)]*\) -+\n.*?def _log_ledger",
            """# ----- warn ledger ------------------------------------------------------------
# Warnings that vanish into transient stderr make "should this become a block?"
# unanswerable — there is no data on how often one fires or gets fixed. Every
# warn/block event appends one JSONL line to a local ledger. Fail-open like
# everything else here.
def _log_ledger""",
            "ledger comment", flags=re.DOTALL)
    o.resub(r'        ledger_dir = Path\.home\(\) / "\.cache" / f"\{_os_name\(\)\}-memory"',
            "        ledger_dir = cfg.cache_dir()", "ledger dir -> config")

    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_os_lint(o: Organ) -> None:
    o.docstring(DOC_LINT)
    o.sub("import os\nimport re\nimport sys\nfrom pathlib import Path\n",
          "import re\nimport sys\nfrom pathlib import Path\n\n" + CONFIG_IMPORT,
          "config import")

    o.resub(r"SKIP_DIRS = \{[^}]*\}\n", "SKIP_DIRS = cfg.SKIP_DIRS\n", "skip dirs -> config")

    o.resub(r'def _os_name\(\) -> str:\n    return os\.environ[^\n]*\n\n\n'
            r"def _roots\(\) -> tuple\[Path, Path\]:\n.*?    return repo, mem\n",
            """def _roots() -> tuple[Path, Path]:
    return cfg.os_root(), cfg.memory_dir()
""",
            "roots -> config", flags=re.DOTALL)

    # config.memory_dir() is free to point inside the repo, in which case the
    # wikilink pass would walk the same files twice and report every near-miss
    # twice. The index checks below still use the unfiltered memory list.
    o.sub("    for f in repo_files + mem_files:\n",
          "    _seen = {f.resolve() for f in repo_files}\n"
          "    for f in repo_files + [f for f in mem_files if f.resolve() not in _seen]:\n",
          "dedupe when the memory store lives inside the repo")

    o.resub(r"    SLOP_RE = re\.compile\(\n.*?\n        re\.IGNORECASE,\n    \)\n"
            r"    slop_dirs = \[[^]]*\]\n",
            "    SLOP_RE = re.compile(cfg.SLOP_PATTERNS, re.IGNORECASE)\n"
            "    slop_dirs = [repo / d for d in cfg.SLOP_DIRS]\n",
            "slop config -> config", flags=re.DOTALL)

    # derived-page awareness (2026-09-03): thresholds and the index path come
    # from config; the instance's timer name and hook path do not travel.
    o.sub("            if age_d > 8:\n", "            if age_d > cfg.INDEX_STALE_DAYS:\n", "stale days -> config")
    o.sub('".claude/hooks/build_memory_index.py --apply (is memory-index.timer alive?)"',
          '"build_memory_index.py --apply (is the weekly timer alive?)"', "stale message, generic")
    o.resub(r'    _os = os\.environ\.get\("MEMORY_OS", "[\w-]+"\)\n'
            r'    idx_db = Path\.home\(\) / "\.cache" / f"\{_os\}-memory" / "index\.db"\n',
            "    idx_db = cfg.db_path()\n", "index path -> config")
    o.sub("                if now - f.stat().st_mtime < 600:\n",
          "                if now - f.stat().st_mtime < cfg.INDEX_SYNC_GRACE_S:\n", "sync grace -> config")
    o.resub(r"    # the healthy-neighbour pattern one layer down \(P1-main, 2026-09-03; the\n"
            r"    # evidence was a memory written minutes earlier, pre-sync\)\. memory_sync runs\n",
            "    # the same failure one layer down (found in the field the day the page went\n"
            "    # derived: a memory on disk, not yet indexed). memory_sync runs\n",
            "field note, generic")

    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_metabolism(o: Organ) -> None:
    o.docstring(DOC_METABOLISM)
    o.resub(r"import os\nimport sys\nimport time\nfrom pathlib import Path\n\n"
            r'_OS = os\.environ[^\n]*\n'
            r"MEM_DIR = [^\n]*\n"
            r"EPI_DIR = [^\n]*\n"
            r'INDEX = MEM_DIR / "MEMORY\.md"\n\n'
            r"ENTRY_PRESSURE = \d+[^\n]*\n"
            r"BYTES_WARN = [\d_]+[^\n]*\n"
            r"DORMANT_DAYS = \d+\n"
            r"DORMANT_PRESSURE = \d+[^\n]*\n"
            r"EPISODIC_DAYS = \d+\n"
            r"EPISODIC_PRESSURE = \d+[^\n]*\n",
            """import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402 — the path insert must precede this import

MEM_DIR = cfg.memory_dir()
EPI_DIR = cfg.episodic_dir()
INDEX = MEM_DIR / "MEMORY.md"

# All thresholds live in config.py — soft doctrine for a knowledge base, not
# hard caps for a working set.
ENTRY_PRESSURE = cfg.ENTRY_PRESSURE
BYTES_WARN = cfg.INDEX_WARN_BYTES        # mirrors the validator's soft cap
DORMANT_DAYS = cfg.DORMANT_DAYS
DORMANT_PRESSURE = cfg.DORMANT_PRESSURE
EPISODIC_DAYS = cfg.EPISODIC_DAYS
EPISODIC_PRESSURE = cfg.EPISODIC_PRESSURE
""",
            "locations + thresholds -> config")
    o.resub(r'              " — propose a /consolidate pass to [^"]*"\)',
            '              " — time for a consolidation pass (propose-only, per doctrine)")',
            "pressure message")
    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_recall_stats(o: Organ) -> None:
    o.docstring(DOC_RECALL_STATS)
    o.resub(r'        print\(f"\[recall-stats\] no log yet at \{LOG_DB\}[^"]*"\)',
            '        print(f"[recall-stats] no log yet at {LOG_DB} — '
            'recall logging starts with the first hook run")',
            "no-log message")
    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


def t_checkpoint(o: Organ) -> None:
    o.docstring(DOC_CHECKPOINT)
    o.resub(r'PY = "[^"]*"\nENGINE = "[^"]*"\n'
            r"TARGETS = \[.*?\n\]\n"
            r"ACTIVE_WINDOW_H = \d+[^\n]*\n"
            r"MIN_BYTES = [\d_]+[^\n]*\n",
            '''PY = sys.executable          # whichever interpreter runs this (venv-aware)
ENGINE = Path(os.environ.get("MEMORY_EPISODIC_ENGINE", "")
              or Path(__file__).resolve().parents[1] / "hooks" / "memory_episodic.py")
if not ENGINE.exists():
    sys.exit(f"[checkpoint] capture engine not found at {ENGINE} — "
             f"set MEMORY_EPISODIC_ENGINE to your memory_episodic.py")

sys.path.insert(0, str(ENGINE.parent))
import config as cfg  # noqa: E402 — the path insert must precede this import

ACTIVE_WINDOW_H = cfg.CHECKPOINT_ACTIVE_WINDOW_H
MIN_BYTES = cfg.CHECKPOINT_MIN_BYTES


def targets() -> list[tuple[Path, str]]:
    """(transcript directory, MEMORY_OS value) pairs to sweep.

    Default: the project directory holding this OS's transcripts, which is the
    parent of the memory directory in a stock layout. Set config.CHECKPOINT_TARGETS
    explicitly for a multi-repo box, or if you moved the memory directory.
    """
    if cfg.CHECKPOINT_TARGETS:
        return [(Path(d).expanduser(), name) for d, name in cfg.CHECKPOINT_TARGETS]
    return [(cfg.memory_dir().parent, cfg.os_name())]
''',
            "checkpoint locations -> config", flags=re.DOTALL)
    o.sub("import json\nimport subprocess\nimport sys\nimport time\nfrom pathlib import Path\n",
          "import json\nimport os\nimport subprocess\nimport sys\nimport time\n"
          "from pathlib import Path\n",
          "os import")
    o.sub("    for proj_dir, os_name in TARGETS:", "    for proj_dir, os_name in targets():",
          "targets call")
    o.resub(r'            r = subprocess\.run\(\[PY, ENGINE\], input=payload, text=True,\n'
            r"                               env=\{[^}]*\},\n"
            r"                               capture_output=True, timeout=300\)",
            "            r = subprocess.run([PY, str(ENGINE)], input=payload, text=True,\n"
            "                               env={**os.environ, \"MEMORY_OS\": os_name},\n"
            "                               capture_output=True, timeout=300)",
            "subprocess env")
    o.forbid(ABSOLUTE_PATHS, "absolute instance paths must be gone")


# source basename -> (output path, transform)
ORGANS = {
    "memory_lib.py":         ("hooks/memory_lib.py", t_memory_lib),
    "memory_recall.py":      ("hooks/memory_recall.py", t_memory_recall),
    "memory_sync.py":        ("hooks/memory_sync.py", t_memory_sync),
    "memory_episodic.py":    ("hooks/memory_episodic.py", t_memory_episodic),
    "validate_os_memory.py": ("hooks/validate_os_memory.py", t_validator),
    "os_lint.py":            ("hooks/os_lint.py", t_os_lint),
    "metabolism_stats.py":   ("hooks/metabolism_stats.py", t_metabolism),
    "recall_stats.py":       ("hooks/recall_stats.py", t_recall_stats),
}

# Ported by hand from private originals (paths and thresholds moved to
# config.py, instance notes removed), then left alone: the build does not
# regenerate them, the leak check covers them like everything else.
HAND_PORTED = [
    "hooks/build_memory_index.py", "hooks/memory_archive.py",
]

HAND_WRITTEN = [
    "hooks/config.py", "README.md", "INSTALL.md", "LICENSE", "index.html",
    "commands/consolidate.md", "commands/save-clear.md", "commands/save-kill.md",
    "skills/spinoff/SKILL.md",
    "examples/settings.single-repo.json", "examples/settings.multi-repo.json",
]


# ---------------------------------------------------------------------------
# leak check
# ---------------------------------------------------------------------------

# Words that are also common names. A private corpus full of people will
# otherwise ban ordinary English and make the check impossible to satisfy.
NAME_STOPWORDS = {
    # names that are also ordinary words
    "will", "mark", "grace", "hunter", "parker", "chase", "field", "frank",
    "green", "hall", "king", "lane", "long", "love", "page", "park", "rich",
    "sharp", "stone", "white", "young", "brown", "cook", "hope", "joy", "may",
    "june", "july", "august", "bell", "case", "drew", "bill", "art", "moss",
    "reed", "rose", "wood", "banks", "brooks", "fields", "rivers", "summer",
    "winter", "north", "south", "east", "west", "small", "short", "best",
    # words that appear in an entity FOLDER without naming a person: indexes,
    # rosters, group files. Their tokens are vocabulary, not identity.
    "index", "memory", "template", "claude", "network", "rolodex", "cohort",
    "people", "person", "contact", "contacts", "notes", "session", "sessions",
    "list", "roster", "group", "team", "school", "cohorts",
}

# People-store stems exempt from the name sweep because the repo cites them on
# purpose. Keep this list short and only for genuinely published work.
PUBLIC_CITATIONS = {"cal-paterson"}

# Stem tokens that mark a file in the people store as a catalog of the network
# (roster, index, map, template) rather than a record of one person. derive_bans
# skips these files entirely — see the comment at the skip site.
GROUP_FILE_TOKENS = {
    "cohort", "cohorts", "roster", "group", "team", "list", "index",
    "rolodex", "network", "map", "template", "claude", "readme", "touches",
}

PATTERN_BANS = [
    (r"[\w.+-]+@[\w-]+\.[\w.]{2,}", "email address"),
    (r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b", "CGNAT/Tailscale IP"),
    (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "private IP"),
    (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "private IP"),
    (r"/home/[a-zA-Z0-9._-]+", "absolute home path"),
    (r"/Users/[a-zA-Z0-9._-]+", "absolute home path"),
    (r"/mnt/[a-zA-Z0-9._-]+", "absolute mount path"),
]

SCAN_SUFFIXES = {".py", ".md", ".json", ".html", ".txt", ".sh", ".toml", ".yml",
                 ".yaml", ".js", ".css", ".svg", ".xml", ".ini", ".cfg", ""}
SCAN_SKIP_DIRS = {".git", "__pycache__", ".venv"}

# Signed files: the author's own name belongs on them. The front page carries a
# byline, and a copyright line IS a signature — a license with the holder
# redacted is not a license. Nothing else in the tree gets an exemption, and the
# corpus bans (other people, private paths, IPs) still apply even here.
SIGNED_PAGES = {"index.html", "LICENSE"}


def derive_bans(source: Path, ban_file: Path) -> list[tuple[str, str]]:
    """Identity and corpus tokens, read from the PRIVATE side at build time.

    Deliberately not stored in this repo: a public file listing the exact strings
    that must never appear is itself a disclosure, and it would go stale the
    moment the private corpus grew.
    """
    bans: list[tuple[str, str]] = []
    if not ban_file.exists():
        raise BuildError(
            f"no ban file at {ban_file}. Create it in the PRIVATE repo, one token per "
            f"line (your name, handles, other repos, any string that must never ship). "
            f"An empty identity ban list is not a passing check, it is an unchecked build.")
    tokens = [ln.strip() for ln in ban_file.read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")]
    if not tokens:
        raise BuildError(f"{ban_file} is empty — see the note above.")
    for tok in tokens:
        # word-bounded where the token starts/ends on a word character, so a short
        # name cannot match inside an unrelated word ("atom", "custom", ...)
        pat = re.escape(tok)
        if tok[:1].isalnum():
            pat = r"\b" + pat
        if tok[-1:].isalnum():
            pat = pat + r"\b"
        bans.append((pat, "identity/instance token"))

    # Every filename in the private people store: the names of real individuals,
    # derived rather than listed. Organisation stores are deliberately NOT swept —
    # company names are public, and banning them would ban ordinary technical
    # vocabulary. If your corpus holds private organisations, add the folder here.
    for folder in ("people", "contacts"):
        d = source / folder
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            # Published authors this repo credits by name. A citation is not a
            # leak: the same reasoning that leaves organisation names unswept.
            # Keyed on the people-store stem so that filing a contact card for
            # someone you already cite in public does not retroactively ban the
            # citation — which is exactly how this exemption got written.
            if f.stem.lower() in PUBLIC_CITATIONS:
                continue
            parts = [p for p in re.split(r"[-_]", f.stem.lower()) if p]
            # A stem carrying any of these tokens catalogs the network — a
            # roster, index, or map — rather than naming one person. Skip the
            # whole file: its tokens are vocabulary ("network map", "spring
            # cohort"), and banning vocabulary poisons the check with false
            # positives while protecting no one.
            if any(p in GROUP_FILE_TOKENS for p in parts):
                continue
            if len(parts) > 1:
                bans.append((r"\b" + r"[\s-]+".join(re.escape(p) for p in parts) + r"\b",
                             f"corpus entity ({folder})"))
            for p in parts:
                if len(p) >= 4 and p not in NAME_STOPWORDS and not p.isdigit():
                    bans.append((r"\b" + re.escape(p) + r"\b", f"corpus entity ({folder})"))

    # the private repo's own location and this machine's home directory
    bans.append((re.escape(str(source)), "private repo path"))
    bans.append((r"\b" + re.escape(source.name) + r"\b", "private repo name"))
    bans.append((r"\b" + re.escape(Path.home().name) + r"\b", "home directory name"))
    return bans


def leak_check(root: Path, bans: list[tuple[str, str]], author_allow: list[str]) -> list[str]:
    findings = []
    checks = list(PATTERN_BANS) + bans
    for fp in sorted(root.rglob("*")):
        if not fp.is_file() or any(p in SCAN_SKIP_DIRS for p in fp.parts):
            continue
        rel = fp.relative_to(root).as_posix()
        # filenames leak too — a path is published exactly like content
        for pattern, why in checks:
            if re.search(pattern, rel, re.IGNORECASE):
                findings.append(f"{rel}  [{why} in path]  {rel!r}")
        if fp.suffix.lower() not in SCAN_SUFFIXES:
            findings.append(f"{rel}  [unscannable suffix]  publishable file "
                            f"outside SCAN_SUFFIXES — add the suffix or remove the file")
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError as e:
            findings.append(f"{rel}  [unreadable]  {e}")
            continue
        except UnicodeDecodeError:
            findings.append(f"{rel}  [not UTF-8]  cannot be scanned — re-encode or remove")
            continue
        # A signed page gets ONLY its declared byline strings stripped before
        # scanning. Every ban still applies to the rest of it: signing licenses
        # the byline, not the page.
        scan = text
        if rel in SIGNED_PAGES:
            for allowed in author_allow:
                scan = scan.replace(allowed, "")
        for pattern, why in checks:
            for m in re.finditer(pattern, scan, re.IGNORECASE):
                line = scan[:m.start()].count("\n") + 1
                findings.append(f"{rel}:{line}  [{why}]  {m.group(0)[:60]!r}")
    return findings


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--source", type=Path,
                    help="private OS repo the organs are extracted from")
    ap.add_argument("--ban-file", type=Path,
                    help="private token list (default: <source>/.claude/leakcheck-bans.txt)")
    ap.add_argument("--author-allow", action="append", default=[],
                    help="string permitted on the signed front page (repeatable); "
                         "default reads .signed-byline in the source repo")
    ap.add_argument("--checkpoint", type=Path,
                    help="path to the private checkpoint script, if it is not on the "
                         "default search path")
    ap.add_argument("--check", action="store_true",
                    help="leak-check the existing tree, build nothing")
    args = ap.parse_args()

    if not args.source:
        return _fail("--source is required (the private repo to extract from); "
                     "use --check --source <repo> to only verify the tree")
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        return _fail(f"source repo not found: {source}")
    hooks_src = source / ".claude" / "hooks"
    ban_file = args.ban_file or (source / ".claude" / "leakcheck-bans.txt")

    author_allow = list(args.author_allow)
    byline = source / ".claude" / "signed-byline.txt"
    if not author_allow and byline.exists():
        author_allow = [ln.strip() for ln in byline.read_text(encoding="utf-8").splitlines()
                        if ln.strip()]

    try:
        bans = derive_bans(source, ban_file)
    except BuildError as e:
        return _fail(str(e))
    print(f"[build] {len(bans)} derived ban patterns + {len(PATTERN_BANS)} pattern bans")

    if not args.check:
        for basename, (out_rel, transform) in ORGANS.items():
            src = hooks_src / basename
            if not src.exists():
                # the checkpoint lives outside the hooks directory in most setups
                src = _find_elsewhere(source, basename)
            if src is None or not src.exists():
                return _fail(f"private original not found: {basename}")
            organ = Organ(basename, src.read_text(encoding="utf-8"))
            try:
                transform(organ)
            except BuildError as e:
                return _fail(str(e))
            out = HERE / out_rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(organ.text, encoding="utf-8")
            out.chmod(0o755)
            # a transform that mangles syntax must fail the build, not ship —
            # the leak check reads text and cannot notice broken Python
            if out.suffix == ".py":
                try:
                    compile(organ.text, str(out), "exec")
                except SyntaxError as e:
                    return _fail(f"{out_rel}: derived file no longer compiles "
                                 f"(line {e.lineno}): {e.msg}")
            print(f"[build] {out_rel:38} {len(organ.applied)} transforms")

        # the checkpoint is a standalone script, not a hook
        cp_src = _checkpoint_source(source, args.checkpoint)
        if cp_src is None:
            return _fail("checkpoint source not found (pass --checkpoint or place it "
                         "at <source>/.claude/hooks/checkpoint.py)")
        organ = Organ(cp_src.name, cp_src.read_text(encoding="utf-8"))
        try:
            t_checkpoint(organ)
        except BuildError as e:
            return _fail(str(e))
        out = HERE / "bin" / "checkpoint.py"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(organ.text, encoding="utf-8")
        out.chmod(0o755)
        print(f"[build] {'bin/checkpoint.py':38} {len(organ.applied)} transforms")

    missing = [p for p in HAND_WRITTEN + HAND_PORTED if not (HERE / p).exists()]
    if missing:
        return _fail("hand-written files missing from the tree: " + ", ".join(missing))

    findings = leak_check(HERE, bans, author_allow)
    if findings:
        print(f"\n[build] LEAK CHECK FAILED — {len(findings)} finding(s):", file=sys.stderr)
        for f in findings[:60]:
            print(f"  {f}", file=sys.stderr)
        if len(findings) > 60:
            print(f"  ... and {len(findings) - 60} more", file=sys.stderr)
        print("\nNothing was deleted; the tree is on disk but MUST NOT be pushed "
              "until these are zero.", file=sys.stderr)
        return 1

    print(f"[build] leak check clean across {_scanned(HERE)} files — safe to publish")
    return 0


def _checkpoint_source(source: Path, explicit: Path | None) -> Path | None:
    """The checkpoint runs on a timer, not as a hook, so it usually lives on PATH
    rather than in the hooks directory. Look in both, or take it as an argument."""
    cands = [explicit] if explicit else []
    cands += [source / ".claude" / "hooks" / "checkpoint.py",
              Path.home() / ".local" / "bin" / "claude-checkpoint"]
    for cand in cands:
        if cand and cand.exists():
            return cand
    return None


def _find_elsewhere(source: Path, basename: str) -> Path | None:
    for cand in source.rglob(basename):
        return cand
    return None


def _scanned(root: Path) -> int:
    return sum(1 for p in root.rglob("*")
               if p.is_file() and p.suffix.lower() in SCAN_SUFFIXES
               and not any(d in SCAN_SKIP_DIRS for d in p.parts))


def _fail(msg: str) -> int:
    print(f"[build] FAILED: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
