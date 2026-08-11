#!/usr/bin/env python3
"""
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
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402 — the path insert must precede this import

# harness noise wrappers that prepend real user content — strip, don't drop the turn
_NOISE_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>"
    r"|<local-command[^>]*>.*?</local-command[^>]*>"
    r"|<command-(?:name|message|args)>.*?</command-(?:name|message|args)>"
    r"|<command-stdout>.*?</command-stdout>",
    re.DOTALL | re.IGNORECASE)


def _strip_noise(text):
    return _NOISE_RE.sub("", text or "").strip()


def _read_payload():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _user_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            # tool_result / image blocks: skipped (recoverable from raw JSONL)
        return "\n".join(p for p in parts if p).strip()
    return ""


def _assistant_text(content):
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    if isinstance(content, str):
        return content.strip()
    return ""


def _looks_injected(text):
    # hook-injected context / local-command echoes / compact summaries are not
    # real conversational turns; keep the transcript readable by dropping them.
    head = text[:60]
    return any(m in head for m in (
        "<local-command", "Caveat:", "<command-name>",
        "Relevant context from", "Current datetime:"))


def parse_transcript(jsonl_path):
    turns, first_ts, last_ts, first_user = [], None, None, None
    p = Path(jsonl_path)
    if not p.exists():
        return turns, first_ts, last_ts, first_user
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("isMeta") or obj.get("isCompactSummary"):
            continue
        t = obj.get("type")
        msg = obj.get("message") or {}
        ts = obj.get("timestamp")
        if t == "user":
            text = _strip_noise(_user_text(msg.get("content")))
            if not text or _looks_injected(text):
                continue
            turns.append(("User", ts, text))
            if first_user is None:
                first_user = text
        elif t == "assistant":
            text = _assistant_text(msg.get("content"))
            if not text:
                continue
            turns.append(("Claude", ts, text))
        else:
            continue
        if first_ts is None:
            first_ts = ts
        last_ts = ts
    return turns, first_ts, last_ts, first_user


_GENERIC = {"systems","system","labs","home","homes","group","global","capital",
            "ventures","partners","company","the","and"}


def _mentions(db, text):
    """Entities the transcript is actually ABOUT — frequency-gated, so a card
    names the few things a session kept returning to rather than every name
    that flickered past once."""
    low = text.lower()
    scored = []
    try:
        placeholders = ",".join("?" * len(cfg.MENTION_KINDS))
        rows = db.execute(
            f"SELECT title, stem FROM docs WHERE kind IN ({placeholders})",
            tuple(cfg.MENTION_KINDS)).fetchall()
    except Exception:
        return []
    for title, stem in rows:
        toks = [t for t in re.split(r"[-_\s]+", stem or "") if len(t) >= 4 and t not in _GENERIC]
        count = sum(len(re.findall(r"\b" + re.escape(t) + r"\b", low)) for t in toks)
        if count >= cfg.EPISODIC_MENTION_MIN:   # central, not incidental
            scored.append((count, title))
    scored.sort(reverse=True)
    return [title for _, title in scored[:15]]


def main():
    import memory_lib as ml
    payload = _read_payload()
    tpath = payload.get("transcript_path")
    session = payload.get("session_id") or "unknown"
    event = payload.get("hook_event_name") or "?"
    if not tpath:
        return 0
    turns, first_ts, last_ts, first_user = parse_transcript(tpath)
    if not turns:
        return 0

    # Routine gate: scheduled/headless runs file their real output somewhere
    # else, and their transcripts recur forever. Capturing them is pure recall
    # chaff, weekly. Configure the opening lines of your automated prompts in
    # config.ROUTINE_SIGNATURES; matched sessions are never captured.
    _fu = (first_user or "").lstrip()
    if any(_fu.startswith(s) for s in cfg.ROUTINE_SIGNATURES if s):
        return 0

    # Junk gate: don't mint a card for a trivial session ("hi" + a greeting back).
    # Junk cards poison recall — on short prompts vector-only retrieval surfaces them
    # as nearest neighbors (observed 2026-07-01: four "hi" cards in live recall).
    # Substantive = enough turns AND enough content, OR any central entity mentions.
    total_chars = sum(len(t) for _, _, t in turns)
    db = None
    try:
        db = ml.connect()
        mentions = _mentions(db, "\n".join(t for _, _, t in turns))
    except Exception:
        mentions = []
    if not mentions and (len(turns) < cfg.EPISODIC_MIN_TURNS
                         or total_chars < cfg.EPISODIC_MIN_CHARS):
        if db is not None:
            db.close()
        return 0

    date = (first_ts or "0000-00-00")[:10]
    short = session.split("-")[0]
    full_dir = ml.EPISODIC_DIR / "full"     # lossless archive — NOT indexed (greppable)
    full_dir.mkdir(parents=True, exist_ok=True)
    ml.EPISODIC_DIR.mkdir(parents=True, exist_ok=True)
    full_out = full_dir / f"{date}_{short}.md"   # full verbatim transcript
    card_out = ml.EPISODIC_DIR / f"{date}_{short}.md"  # small indexed card -> points to full

    # 1) full verbatim transcript (the diary; recall verbatim by grep or by card->path)
    flines = [f"# Session {date} — {(first_user or '')[:80]}", f"*session {session}, captured on {event}*", ""]
    for role, ts, text in turns:
        flines.append(f"## {role}  {(ts or '')[11:16]}".rstrip())
        flines.append(text)
        flines.append("")
    full_out.write_text("\n".join(flines), encoding="utf-8")

    # 2) small episode card (indexed by Seed 1 — surfaces the conversation by who/what it's about)
    desc = (first_user or "").replace("\n", " ")[:160]
    opening = "\n\n".join(f"{r}: {t[:300]}" for r, _, t in turns[:4])
    clines = [
        "---",
        f"session: {session}",
        f"date: {date}",
        f"description: {desc}",
        f"full_transcript: {full_out}",
        f"source_jsonl: {tpath}",
        f"captured_on: {event}",
        "---",
        f"# Session {date} — {desc[:80]}",
        "",
        f"**Mentions:** {', '.join(mentions) if mentions else '(none detected)'}",
        "",
        f"**Full verbatim transcript:** {full_out}",
        "",
        "**Opening:**",
        opening,
    ]
    card_out.write_text("\n".join(clines), encoding="utf-8")

    # 3) pointer row (best-effort)
    try:
        if db is None:
            db = ml.connect()
        db.execute("""CREATE TABLE IF NOT EXISTS episodic(
            session TEXT PRIMARY KEY, date TEXT, card_path TEXT, full_path TEXT,
            n_turns INT, source_jsonl TEXT, updated_event TEXT)""")
        db.execute("INSERT INTO episodic(session,date,card_path,full_path,n_turns,source_jsonl,updated_event) "
                   "VALUES (?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET "
                   "date=excluded.date, card_path=excluded.card_path, full_path=excluded.full_path, "
                   "n_turns=excluded.n_turns, source_jsonl=excluded.source_jsonl, updated_event=excluded.updated_event",
                   (session, date, str(card_out), str(full_out), len(turns), tpath, event))
        db.commit()
    except Exception:
        pass
    finally:
        if db is not None:
            db.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # fail-open: never block compaction / session end
