#!/usr/bin/env python3
"""
memory_archive.py — retire a memory WITHOUT losing what replaced it.

WHY. A corrected fact overwritten in place loses the trail. The archive tier
(memory/archive/) already keeps retired files out of the always-loaded page
while leaving them searchable, but a folder of retired files is a graveyard
unless each one says why it left and what took its place. Prose a human can
read is not a field a session can query. This tool writes both: a structured
record inside the existing `metadata:` block, and a one-line banner at the top
of the body. (The idea of closing a fact and linking it to its replacement,
rather than overwriting it, is borrowed from the M3 memory design.)

Schema, inside `metadata:` so the provenance validator and every existing
reader are unaffected:

    metadata:
      archived: 2026-08-16          # date it left the active store
      archived_reason: merged       # merged | superseded | evicted | wrong
      superseded_by: <memory-name>  # omitted when nothing replaced it

Reasons, kept deliberately few:
  merged      — content carried into a survivor (superseded_by required)
  superseded  — a newer memory states the correct version (superseded_by required)
  evicted     — still true, just unused (rare once the page is derived by use)
  wrong       — the claim was false; kept only for provenance

Never move a memory into archive/ by hand; use this, so the record exists.

USAGE
  memory_archive.py archive <name> --reason merged --into <survivor> [--note "..."]
  memory_archive.py why <name>          # the trail: what replaced it, what it replaced
  memory_archive.py audit               # archive entries missing the fields
  memory_archive.py backfill [--apply]  # infer fields from existing banners or mtime
Set MEMORY_OS to operate on another store (see config.py).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402 — the path insert must precede this import

ROOT = cfg.memory_dir()
ARCHIVE = ROOT / "archive"
INDEX = ROOT / "MEMORY.md"
REASONS = {"merged", "superseded", "evicted", "wrong"}
NEEDS_SUCCESSOR = {"merged", "superseded"}


def split_frontmatter(raw: str) -> tuple[list[str], str]:
    """([frontmatter lines without fences], body). ([], raw) when absent."""
    if not raw.startswith("---"):
        return [], raw
    end = raw.find("\n---", 3)
    if end == -1:
        return [], raw
    fm = raw[3:end].strip("\n").split("\n")
    return fm, raw[end + 4:].lstrip("\n")


def get_field(fm: list[str], key: str) -> str | None:
    for ln in fm:
        m = re.match(rf"^\s*{re.escape(key)}:\s*(.*)$", ln)
        if m:
            return m.group(1).strip() or None
    return None


def set_metadata_fields(fm: list[str], fields: dict) -> list[str]:
    """Insert or replace keys inside the `metadata:` block, preserving its
    indent. Creates the block if the file has frontmatter but no metadata."""
    out, mi = list(fm), None
    for i, ln in enumerate(out):
        if re.match(r"^metadata:\s*$", ln):
            mi = i
            break
    if mi is None:
        out.append("metadata:")
        mi = len(out) - 1
    indent, end = "  ", len(out)
    for i in range(mi + 1, len(out)):
        if re.match(r"^\S", out[i]):
            end = i
            break
        if out[i].strip():
            indent = re.match(r"^(\s*)", out[i]).group(1)
    else:
        end = len(out)
    for key, val in fields.items():
        if val is None:
            continue
        line = f"{indent}{key}: {val}"
        for i in range(mi + 1, end):
            if re.match(rf"^\s*{re.escape(key)}:", out[i]):
                out[i] = line
                break
        else:
            out.insert(end, line)
            end += 1
    return out


def banner(reason: str, when: str, into: str | None, note: str | None) -> str:
    head = {"merged": f"⚠ **RETIRED {when} — merged into [[{into}]].**",
            "superseded": f"⚠ **RETIRED {when} — superseded by [[{into}]].**",
            "evicted": f"⚠ **ARCHIVED {when} — unused, not wrong.**",
            "wrong": f"⚠ **RETIRED {when} — this claim was WRONG.**"}[reason]
    tail = f" {note}" if note else ""
    return f"> {head}{tail} Kept for provenance.\n"


def write_fields(path: Path, reason: str, when: str, into: str | None,
                 note: str | None, add_banner: bool) -> None:
    raw = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(raw)
    if not fm:
        raise SystemExit(f"{path.name}: no frontmatter — fix by hand")
    fm = set_metadata_fields(fm, {"archived": when, "archived_reason": reason,
                                  "superseded_by": into})
    if add_banner and "⚠" not in body[:400]:
        body = banner(reason, when, into, note) + "\n" + body
    path.write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body.lstrip("\n"),
                    encoding="utf-8")


def drop_index_line(name: str) -> bool:
    """Remove the memory's line from MEMORY.md, whichever format the page uses
    (a hand-written `[title](name.md)` link or the derived `- name — hook`).
    Under a derived page this is belt-and-braces: the next regeneration would
    drop it anyway."""
    if not INDEX.is_file():
        return False
    lines = INDEX.read_text(encoding="utf-8").split("\n")
    keep = [ln for ln in lines
            if f"({name}.md)" not in ln and not ln.startswith(f"- {name} ")]
    if len(keep) != len(lines):
        INDEX.write_text("\n".join(keep), encoding="utf-8")
        return True
    return False


def cmd_archive(a) -> None:
    if a.reason in NEEDS_SUCCESSOR and not a.into:
        raise SystemExit(f"--reason {a.reason} requires --into <survivor>")
    src = ROOT / f"{a.name}.md"
    if not src.is_file():
        src = ARCHIVE / f"{a.name}.md"
        if not src.is_file():
            raise SystemExit(f"no memory named {a.name}")
    if a.into and not (ROOT / f"{a.into}.md").is_file():
        raise SystemExit(f"survivor {a.into} does not exist in the active store")
    when = a.date or date.today().isoformat()
    ARCHIVE.mkdir(exist_ok=True)
    dst = ARCHIVE / src.name
    if src.parent != ARCHIVE:
        shutil.move(str(src), str(dst))
    write_fields(dst, a.reason, when, a.into, a.note, add_banner=True)
    dropped = drop_index_line(a.name)
    print(f"archived {a.name} ({a.reason}"
          f"{' → ' + a.into if a.into else ''}, {when})"
          f"{'; index line removed' if dropped else ''}")


def cmd_why(a) -> None:
    """The trail: what replaced this, and what this replaced."""
    hits = list(ARCHIVE.glob(f"{a.name}.md")) + list(ROOT.glob(f"{a.name}.md"))
    if not hits:
        raise SystemExit(f"no memory named {a.name}")
    fm, _ = split_frontmatter(hits[0].read_text(encoding="utf-8"))
    where = "archive" if hits[0].parent == ARCHIVE else "active"
    print(f"{a.name} [{where}]")
    for k in ("archived", "archived_reason", "superseded_by"):
        v = get_field(fm, k)
        if v:
            print(f"  {k}: {v}")
    back = [p.stem for p in ARCHIVE.glob("*.md")
            if get_field(split_frontmatter(p.read_text(encoding="utf-8"))[0],
                         "superseded_by") == a.name]
    if back:
        print(f"  replaced: {', '.join(sorted(back))}")


def cmd_audit(a) -> None:
    missing = []
    for p in sorted(ARCHIVE.glob("*.md")):
        fm, _ = split_frontmatter(p.read_text(encoding="utf-8"))
        if not get_field(fm, "archived"):
            missing.append(p.stem)
    print(f"{cfg.os_name()}: {len(list(ARCHIVE.glob('*.md')))} archived, "
          f"{len(missing)} missing fields")
    for m in missing:
        print(f"  · {m}")


_BANNER_RE = re.compile(
    r"(?:RETIRED|ARCHIVED)\s+(\d{4}-\d{2}-\d{2})(?:\*\*)?\s*(?:—|-)?\s*"
    r"(?:(merged|superseded)\s+(?:into|by)\s+\[\[([\w-]+)\]\])?", re.I)


def cmd_backfill(a) -> None:
    """Infer fields from an existing prose banner; fall back to mtime + evicted.
    Prints the plan; only writes with --apply."""
    for p in sorted(ARCHIVE.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        fm, body = split_frontmatter(raw)
        if get_field(fm, "archived"):
            continue
        m = _BANNER_RE.search(body[:600])
        if m:
            when, reason, into = m.group(1), (m.group(2) or "evicted").lower(), m.group(3)
            src = "banner"
        else:
            when = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
            reason, into, src = "evicted", None, "mtime (approximate)"
        print(f"{'APPLY' if a.apply else 'PLAN '} {p.stem}: {reason}"
              f"{' → ' + into if into else ''} @ {when}  [{src}]")
        if a.apply:
            write_fields(p, reason, when, into, None, add_banner=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ar = sub.add_parser("archive")
    ar.add_argument("name")
    ar.add_argument("--reason", required=True, choices=sorted(REASONS))
    ar.add_argument("--into")
    ar.add_argument("--note")
    ar.add_argument("--date")
    ar.set_defaults(fn=cmd_archive)
    wh = sub.add_parser("why")
    wh.add_argument("name")
    wh.set_defaults(fn=cmd_why)
    au = sub.add_parser("audit")
    au.set_defaults(fn=cmd_audit)
    bf = sub.add_parser("backfill")
    bf.add_argument("--apply", action="store_true")
    bf.set_defaults(fn=cmd_backfill)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
