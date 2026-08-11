#!/usr/bin/env python3
"""
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

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402 — the path insert must precede this import


# ----- audited roots ---------------------------------------------------------
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


def _is_audited(fp: Path, roots: list[Path]) -> bool:
    try:
        rp = fp.resolve()
    except Exception:
        return False
    for root in roots:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


# ----- code stripping (so examples / fenced snippets never false-fire) --------
_FENCED_CODE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$",
                             re.DOTALL | re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_code(text: str) -> str:
    text = _HTML_COMMENT_RE.sub("", text)
    text = _FENCED_CODE_RE.sub("", text)
    text = _INLINE_CODE_RE.sub("", text)
    return text


# ----- link patterns ---------------------------------------------------------
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")          # detected only so we can ignore it
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# ----- citation-identifier patterns (the actual failure class) ---------------
_ARXIV_WORD_RE = re.compile(r"arxiv", re.IGNORECASE)
_ARXIV_ID_RE = re.compile(r"(?<![\w.])\d{4}\.\d{4,5}(?:v\d+)?(?![\w.])")
_ARXIV_URL_RE = re.compile(r"arxiv\.org/", re.IGNORECASE)
_DOI_RE = re.compile(r"(?<![\w.])10\.\d{4,9}/\S+", re.IGNORECASE)
# message-id / thread-id style: a long hex blob near an id keyword
_IDKEYWORD_RE = re.compile(r"\b(message[\s-]?id|thread[\s-]?id|msg[\s-]?id)\b", re.IGNORECASE)
_HEXBLOB_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)


def _line_has_link_or_url(line: str) -> bool:
    return bool(_URL_RE.search(line) or _MD_LINK_RE.search(line))


def _check_citation_identifiers(text: str) -> tuple[list[str], list[str]]:
    """Returns (blocking, warnings). Operates line-by-line on code-stripped text."""
    blocking: list[str] = []
    warnings: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        snippet = line[:90] + ("…" if len(line) > 90 else "")

        # arxiv: a mention + an id-shaped token, but no resolvable link/URL on the line.
        # A real https link (arxiv.org, the paper's repo, etc.) counts as sourcing — the
        # failure class is the BARE assertion of an id with nothing to resolve it against.
        if _ARXIV_WORD_RE.search(line) and _ARXIV_ID_RE.search(line) and not _line_has_link_or_url(line):
            blocking.append(f"  - arxiv ID asserted with no resolvable link :: {snippet}")
            continue

        # DOI with no link/URL on the line
        if _DOI_RE.search(line) and not _line_has_link_or_url(line):
            blocking.append(f"  - DOI asserted with no resolvable link :: {snippet}")
            continue

        # message-id-shaped hex near an id keyword, no source — warn only (SHA collisions)
        if _IDKEYWORD_RE.search(line) and _HEXBLOB_RE.search(line) and not _line_has_link_or_url(line):
            warnings.append(f"  - message-id-style identifier with no source :: {snippet}")

    return blocking, warnings


# ----- v2: unsourced quantified-claim auditor (WARN-only, forward-only) -------
# Flags fabrication-prone *quantified external claims* that carry no provenance.
# Provenance on a line = a URL/markdown link, a [[wikilink]], a "per <X>"
# attribution, or an explicit provenance tag:
#   (conversation, <who>, <date>) / (email, <date>) / (intuition, <who>, <date>)
#   / (observed, <date>) / (source, <ref>) / (per <X>)
# Deliberately NARROW — finance figures, funding/valuation, and benchmark metrics
# only — so it stays signal, not noise. WARN-only by design (forces a writing
# convention gently); escalate to blocking only after evaluation in use.
_PROV_TAG_RE = re.compile(r"\((?:conversation|email|intuition|observed|source|per)\b[^)]*\)", re.IGNORECASE)
_PER_ATTR_RE = re.compile(r"\bper\s+[A-Z0-9\"'\[]")          # "per <name>", "per the posting", "per LinkedIn"
# finance terms kept narrow: only unambiguous funding/valuation language (not bare
# "revenue"/"funding"/"Series B" which are common descriptors, not sourced claims).
_FINANCE_TERM_RE = re.compile(r"\b(valuation|pre-money|post-money|funding round|seed round)\b", re.IGNORECASE)
_BIGMONEY_RE = re.compile(r"\$\s?\d[\d,.]*\s?[mMbB]\b|\b\d[\d,.]*\s?(?:million|billion)\b", re.IGNORECASE)
# metric word must sit within ~20 chars of a digit (so "precision hardware" is ignored
# but "65.43% accuracy" / "F1 of 89" fire). Range-hyphens no longer read as deltas.
_METRIC_RE = re.compile(r"\b(F1|mAP|BLEU|SOTA|accuracy|precision|recall)\b[^.\n]{0,20}\d|\d[^.\n]{0,20}\b(F1|mAP|BLEU|SOTA|accuracy|precision|recall)\b")
_DELTA_RE = re.compile(r"[+\-]\d+(?:\.\d+)?\s*(?:points|pts|pp)\b|\b\d+(?:\.\d+)?\s*%\s*(?:improvement|gain|lift|increase|better|higher|faster)\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")


def _line_has_provenance(line: str) -> bool:
    return bool(
        _URL_RE.search(line) or _MD_LINK_RE.search(line) or _WIKILINK_RE.search(line)
        or _PROV_TAG_RE.search(line) or _PER_ATTR_RE.search(line)
    )


def _check_unsourced_claims(text: str) -> list[str]:
    """WARN on quantified external claims (finance / funding / benchmark metrics)
    written without a provenance signal. Skips headings and leading frontmatter."""
    warns: list[str] = []
    lines = text.split("\n")
    # skip a leading YAML frontmatter block
    start = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                start = j + 1
                break
    for raw in lines[start:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        finance = bool(_FINANCE_TERM_RE.search(line) and _DIGIT_RE.search(line))
        bigmoney = bool(_BIGMONEY_RE.search(line))
        metric = bool(_METRIC_RE.search(line) or _DELTA_RE.search(line))
        if not (finance or bigmoney or metric):
            continue
        if _line_has_provenance(line):
            continue
        snippet = line[:90] + ("…" if len(line) > 90 else "")
        warns.append(f"  - quantified claim, no source/provenance tag :: {snippet}")
    return warns


def _check_broken_md_links(text: str, file_parent: Path) -> list[str]:
    """Broken internal markdown links -> WARNING. Skips URLs, anchors, templates,
    and wikilinks (the latter aren't markdown links and are intentionally allowed
    to dangle)."""
    warns: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        target = m.group(1).split("#", 1)[0].strip()
        if not target:
            continue
        if target.startswith(("http://", "https://", "mailto:", "tel:")):
            continue
        if "{{" in target or ("<" in target and ">" in target):
            continue
        target = unquote(target)
        try:
            resolved = (file_parent / target).resolve()
        except Exception:
            continue
        if not resolved.exists():
            warns.append(f"  - internal link doesn't resolve: {target}")
    return warns


# ----- payload parsing -------------------------------------------------------
# ----- index gate -------------------------------------------------------------
# MEMORY.md is an always-loaded pointer index: a cache with a hard read ceiling,
# not a document. Past roughly 24KB it is silently truncated and the bottom of
# the index stops existing — no error, just memories that quietly stop being
# findable. This gate makes that failure class uncreatable. Caps sit under the
# cliff so the block fires while the file still loads whole, and the fix is
# always available in-turn: compress a line to a pointer, push the detail into
# the memory file it points at, or evict a stale entry.
_INDEX_BLOCK_BYTES, _INDEX_WARN_BYTES = cfg.INDEX_BLOCK_BYTES, cfg.INDEX_WARN_BYTES
_INDEX_BLOCK_LINE, _INDEX_WARN_LINE = cfg.INDEX_BLOCK_LINE, cfg.INDEX_WARN_LINE


def _check_index_health(fp: Path, raw_text: str) -> tuple[list[str], list[str]]:
    if fp.name != "MEMORY.md":
        return [], []
    blocking, warnings = [], []
    size = len(raw_text.encode("utf-8"))
    fat = [(i + 1, len(ln)) for i, ln in enumerate(raw_text.splitlines())
           if len(ln) > _INDEX_WARN_LINE]
    over = [(n, l) for n, l in fat if l > _INDEX_BLOCK_LINE]
    if size > _INDEX_BLOCK_BYTES:
        blocking.append(f"  index is {size:,} bytes (> {_INDEX_BLOCK_BYTES:,} hard cap; "
                        f"~24KB = silent truncation). Evict or compress entries NOW — "
                        f"detail belongs in the memory files, not the index.")
    elif size > _INDEX_WARN_BYTES:
        warnings.append(f"  index is {size:,} bytes (soft cap {_INDEX_WARN_BYTES:,}; "
                        f"hard cap {_INDEX_BLOCK_BYTES:,}). Plan an eviction/compression pass.")
    if over:
        sample = ", ".join(f"L{n}({l}ch)" for n, l in over[:5])
        blocking.append(f"  {len(over)} index line(s) over {_INDEX_BLOCK_LINE} chars: {sample}. "
                        f"Index lines are one-line pointers (title + hook); move the detail "
                        f"into the memory file itself.")
    elif fat:
        sample = ", ".join(f"L{n}({l}ch)" for n, l in fat[:5])
        warnings.append(f"  {len(fat)} index line(s) over {_INDEX_WARN_LINE} chars "
                        f"(guideline): {sample}. Compress toward pointers.")
    return blocking, warnings


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _extract_file_paths(payload: dict) -> list[Path]:
    out: list[Path] = []
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        for key in ("file_path", "filePath", "path"):
            v = tool_input.get(key)
            if isinstance(v, str) and v:
                out.append(Path(v))
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            for e in edits:
                if isinstance(e, dict):
                    fp = e.get("file_path") or e.get("filePath")
                    if isinstance(fp, str) and fp:
                        out.append(Path(fp))
    seen, result = set(), []
    for p in out:
        try:
            k = str(p.resolve())
        except Exception:
            k = str(p)
        if k not in seen:
            seen.add(k)
            result.append(p)
    return result


# ----- warn ledger ------------------------------------------------------------
# Warnings that vanish into transient stderr make "should this become a block?"
# unanswerable — there is no data on how often one fires or gets fixed. Every
# warn/block event appends one JSONL line to a local ledger. Fail-open like
# everything else here.
def _log_ledger(files: list[Path], warnings: list[str], blocking: list[str]) -> None:
    try:
        import datetime
        ledger_dir = cfg.cache_dir()
        ledger_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": [p.name for p in files],
            "warns": warnings,
            "blocks": blocking,
        }
        with (ledger_dir / "provenance_ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    payload = _read_payload()
    file_paths = _extract_file_paths(payload)
    if not file_paths:
        return 0

    roots = _audited_roots()
    blocking: list[str] = []
    warnings: list[str] = []

    for fp in file_paths:
        try:
            if not fp.is_absolute():
                fp = fp.resolve()
            if fp.suffix != ".md" or not fp.exists():
                continue
            if not _is_audited(fp, roots):
                continue
            raw = fp.read_text(encoding="utf-8")
            text = _strip_code(raw)
        except Exception:
            continue  # fail-open per file

        try:
            name = fp.name
            idx_block, idx_warn = _check_index_health(fp, raw)
            if idx_block:
                blocking.append(f"{name} — index over budget (the always-loaded cache has a hard read ceiling):")
                blocking.extend(idx_block)
            if idx_warn:
                warnings.append(f"{name} — index approaching budget:")
                warnings.extend(idx_warn)
            cite_block, cite_warn = _check_citation_identifiers(text)
            link_warn = _check_broken_md_links(text, fp.parent)
            claim_warn = _check_unsourced_claims(text)
            if cite_block:
                blocking.append(f"{name} — unsourced citation identifier(s):")
                blocking.extend(cite_block)
                blocking.append("  (identifiers must resolve: include the arxiv.org/doi.org URL, or remove the claim)")
            if cite_warn:
                warnings.append(f"{name} — possible unsourced identifier(s):")
                warnings.extend(cite_warn)
            if link_warn:
                warnings.append(f"{name} — internal links don't resolve (maybe ordering):")
                warnings.extend(link_warn)
            if claim_warn:
                warnings.append(f"{name} — quantified claims w/o provenance (v2, warn-only):")
                warnings.extend(claim_warn)
        except Exception:
            continue  # fail-open per file

    if warnings or blocking:
        _log_ledger(file_paths, warnings, blocking)

    if warnings:
        print("[os-provenance hook] warnings (non-blocking):\n\n" + "\n".join(warnings),
              file=sys.stderr)

    if blocking:
        print(
            "[os-provenance hook] BLOCKING — fix in THIS turn before continuing:\n\n"
            + "\n".join(blocking) + "\n",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # absolute last-resort fail-open: never let this hook block a write on a bug
        sys.exit(0)
