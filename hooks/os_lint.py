#!/usr/bin/env python3
"""
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

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402 — the path insert must precede this import

WIKI_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")
MDLINK_RE = re.compile(r"\]\(([^)]+\.md)\)")
TICKPATH_RE = re.compile(r"`([^`\n]+\.md)`")
FENCED_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.DOTALL | re.MULTILINE)

# meta-usage wikilinks that are prose about the convention, never targets
META_LINKS = {"wikilink", "wikilinks", "name", "page-name", "node-name", "basename", "their-name",
              "folder/file", "file", "x", "target", "memory-name"}

# logs/: captured records, not doctrine; never edited to satisfy a linter
SKIP_DIRS = cfg.SKIP_DIRS


def _roots() -> tuple[Path, Path]:
    return cfg.os_root(), cfg.memory_dir()


def _md_files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*.md"):
        if not any(part in SKIP_DIRS for part in p.parts):
            out.append(p)
    return out


def _norm(name: str) -> str:
    """Normalization key for near-miss matching: case-fold, unify -/_ and spaces."""
    return re.sub(r"[-_ ]+", "_", name.strip().casefold())


def main() -> int:
    verbose = "--verbose" in sys.argv
    repo, mem = _roots()
    issues: list[str] = []

    repo_files = _md_files(repo) if repo.is_dir() else []
    mem_files = sorted(mem.glob("*.md")) if mem.is_dir() else []

    # ---- stem universe + normalized lookup --------------------------------
    stems: set[str] = {f.stem for f in repo_files} | {f.stem for f in mem_files}
    # .claude/ is skipped by the corpus walk (hooks, caches), but its rules, docs,
    # commands and skills are legitimate [[link]] targets by doctrine, so they join
    # the resolution universe (J1-main, 2026-09-04: [[dashboard-content]] and
    # [[git-workflow]] are correct links, not near-misses).
    for sub_dir in ("rules", "docs", "commands", "skills"):
        d = repo / ".claude" / sub_dir
        if d.is_dir():
            stems |= {f.stem for f in d.rglob("*.md")}
    by_norm: dict[str, set[str]] = {}
    for s in stems:
        by_norm.setdefault(_norm(s), set()).add(s)

    # ---- 1. wikilink near-misses ------------------------------------------
    _seen = {f.resolve() for f in repo_files}
    for f in repo_files + [f for f in mem_files if f.resolve() not in _seen]:
        try:
            text = FENCED_RE.sub("", f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in WIKI_RE.finditer(text):
            raw = m.group(1).strip().split("|", 1)[0].strip()   # [[target|display]]
            base = raw.split("/")[-1]
            base_noext = base[:-3] if base.endswith(".md") else base
            if base_noext in stems or base_noext in META_LINKS or base_noext.casefold() in META_LINKS:
                continue
            hits = by_norm.get(_norm(base_noext), set())
            if hits:
                issues.append(f"wikilink near-miss in {f.name}: [[{raw}]] -> existing file '{sorted(hits)[0]}'")

    # ---- 2. MEMORY.md index integrity --------------------------------------
    memory_md = mem / "MEMORY.md"
    if memory_md.exists():
        idx_text = memory_md.read_text(encoding="utf-8")
        linked = set(MDLINK_RE.findall(idx_text))
        # Derived-index format (2026-09-03): dense '- name — hook' lines, plus a
        # below-the-fold trailer of bare names. Both count as indexed.
        linked |= {f"{n}.md" for n in re.findall(r"^- ([\w-]+) —", idx_text, flags=re.M)}
        # Trailers list bare names, wrapped across lines, and may END with a prose
        # sentence ("*… and N more, retrieved per-prompt by the recall hook.*") or be
        # followed by the expired list. Memory names always carry a type prefix and an
        # underscore, so match that shape and nothing else (J1-main, 2026-09-04: the old
        # tokeniser reported and.md, by.md, hook.md, 96.md as missing files).
        _NAME = r"\b([a-z]+_[\w-]+)\b"
        for marker in ("*Retrievable by recall", "*Expired, off the page"):
            k = idx_text.find(marker)
            if k != -1:
                tail = idx_text[k:].split("*", 2)[-1].split("*…", 1)[0]
                linked |= {f"{n}.md" for n in re.findall(_NAME, tail)}

        for t in sorted(linked):
            if not (mem / t).exists():
                issues.append(f"MEMORY.md indexes a missing file: {t}")
        # Under a DERIVED index (2026-09-03), "not in MEMORY.md" is a legitimate
        # state: the generator fills a byte budget and the recall hook serves
        # everything else. The drift this direction caught — a memory written
        # by hand and never indexed — cannot happen any more; what CAN fail is
        # the generator not running. Derived + fresh → skip; derived + stale →
        # one actionable issue; hand-maintained index → the original check.
        import time as _time
        if idx_text.lstrip().startswith("*DERIVED VIEW"):
            age_d = (_time.time() - memory_md.stat().st_mtime) / 86400
            if age_d > cfg.INDEX_STALE_DAYS:
                issues.append(f"MEMORY.md is a derived view but {age_d:.0f} days stale — run "
                              "build_memory_index.py --apply (is the weekly timer alive?)")
        else:
            for f in mem_files:
                if f.name != "MEMORY.md" and f.name not in linked:
                    issues.append(f"memory file not indexed in MEMORY.md: {f.name}")

    # ---- 2a. validity dates (2026-09-04) ----------------------------------------
    # A memory may carry valid_until: YYYY-MM-DD. Past that date it is still on
    # disk and still recalled (flagged EXPIRED by the hook), but it has left the
    # derived page and should be archived (memory_archive.py) or extended.
    import re as _re, time as _time2
    _today = _time2.strftime("%Y-%m-%d")
    for f in sorted(mem_files):
        if f.name == "MEMORY.md":
            continue
        try:
            head = f.read_text(encoding="utf-8", errors="ignore")[:800]
        except OSError:
            continue
        m = _re.search(r"^\s*valid_until:\s*['\"]?(\d{4}-\d{2}-\d{2})", head, _re.M)
        if m and m.group(1) < _today:
            issues.append(f"memory expired {m.group(1)} — archive it (memory_archive.py) or extend valid_until: {f.name}")

    # ---- 2b. recall-index coverage (the layer that now carries reachability) --
    # With a derived, capped MEMORY.md, "every memory is reachable" rests on the
    # recall hook's index.db, not on the index file. A memory on disk that never
    # landed in index.db is invisible to every session while lint says clean —
    # the same failure one layer down (found in the field the day the page went
    # derived: a memory on disk, not yet indexed). memory_sync runs
    # BEFORE this lint in the SessionStart chain, so a gap here means sync
    # failed or fell open — except a file written in the last 10 minutes,
    # which may simply be racing the start. stdlib sqlite only: this script
    # runs under system python, and memory_lib pulls sqlite-vec at import.
    import sqlite3 as _sqlite3, time as _time
    idx_db = cfg.db_path()
    if not idx_db.is_file():
        issues.append("recall index missing: %s — memory_recall will fail open and NOTHING is retrievable" % idx_db)
    else:
        try:
            _db = _sqlite3.connect(str(idx_db))
            indexed = {Path(r[0]).name for r in _db.execute("select path from docs where kind='memory'")}
            now = _time.time()
            for f in sorted(mem_files):
                if f.name == "MEMORY.md" or f.name in indexed:
                    continue
                if now - f.stat().st_mtime < cfg.INDEX_SYNC_GRACE_S:
                    continue  # just written; sync races the session start
                issues.append(f"memory on disk but ABSENT from the recall index (sync stale or failed): {f.name}")
        except Exception as e:  # noqa: BLE001
            issues.append(f"recall index unreadable ({e.__class__.__name__}) — retrieval may be failing open")

    # ---- 3. CLAUDE.md reference existence ----------------------------------
    for cm in repo_files:
        if cm.name != "CLAUDE.md":
            continue
        try:
            text = FENCED_RE.sub("", cm.read_text(encoding="utf-8"))
        except Exception:
            continue
        folder = cm.parent
        basenames_under = {p.name for p in folder.rglob("*.md")}
        for line in text.split("\n"):
          # external-file mentions (a path inside someone's GitHub repo etc.) aren't refs
          if "github" in line.casefold():
              continue
          for m in TICKPATH_RE.finditer(line):
            ref = m.group(1).replace("\\", "/").strip()
            if ref.startswith(("~", "/", "http", "D:", "C:")) or any(
                tok in ref for tok in ("*", "[", "{", "YYYY", "X.X", "<")
            ):
                continue
            name = ref.split("/")[-1]
            # Resolution attempts, in order: the CLAUDE.md's own folder, the repo root,
            # basename-match within this folder's subtree, and — added 2026-08-20 — the
            # folder's PARENT. That last one is the wiki's own convention: a note in
            # knowledge/wiki/ writes `raw/foo.md` meaning knowledge/raw/foo.md, a sibling
            # directory. Without it every correctly-written sibling ref was a false positive
            # (caught on raw/damodaran-industry-roic-wacc-2026-01.md, which existed all along).
            parent_ok = False
            try:
                cand = (folder.parent / ref)
                parent_ok = folder.parent.is_relative_to(repo) and cand.exists()
            except Exception:
                parent_ok = False
            if (folder / ref).exists() or (repo / ref).exists() or name in basenames_under or parent_ok:
                continue
            # ".." references: resolve relative to folder
            try:
                if (folder / ref).resolve().exists():
                    continue
            except Exception:
                pass
            issues.append(f"{cm.relative_to(repo)} references a file that resolves nowhere: `{ref}`")

    # ---- 4. slop-tells in Claude-written prose ------------------------------
    SLOP_RE = re.compile(cfg.SLOP_PATTERNS, re.IGNORECASE)
    slop_dirs = [repo / d for d in cfg.SLOP_DIRS]
    slop_hits = 0
    for d in slop_dirs:
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            if "transcript" in f.name:  # verbatim speech is a quotation surface
                continue
            try:
                text = FENCED_RE.sub("", f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for line in text.split("\n"):
                # skip quoted material: source notes legitimately quote others' slop
                if '"' in line or line.lstrip().startswith(">"):
                    continue
                m = SLOP_RE.search(line)
                if m:
                    slop_hits += 1
                    if slop_hits <= 10:
                        issues.append(f"slop-tell in {f.name}: '{m.group(0)}' — {line.strip()[:70]}")
    if slop_hits > 10:
        issues.append(f"... and {slop_hits - 10} more slop-tells")

    # ---- report -------------------------------------------------------------
    if issues:
        print(f"[os-lint] {len(issues)} consistency issue(s) found at session start "
              f"(mechanical drift — fix opportunistically):")
        for i in issues[:40]:
            print(f"  - {i}")
        if len(issues) > 40:
            print(f"  ... and {len(issues) - 40} more")
    elif verbose:
        print(f"[os-lint] clean: {len(repo_files)} repo files, {len(mem_files)} memory files, "
              f"{len(stems)} link targets")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-open: lint must never break a session start
