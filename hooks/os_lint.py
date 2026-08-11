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
META_LINKS = {"wikilink", "wikilinks", "name", "page-name", "basename", "their-name", "folder/file"}

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
            raw = m.group(1).strip()
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
        for t in sorted(linked):
            if not (mem / t).exists():
                issues.append(f"MEMORY.md indexes a missing file: {t}")
        for f in mem_files:
            if f.name != "MEMORY.md" and f.name not in linked:
                issues.append(f"memory file not indexed in MEMORY.md: {f.name}")

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
            if (folder / ref).exists() or (repo / ref).exists() or name in basenames_under:
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
