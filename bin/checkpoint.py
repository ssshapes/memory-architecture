#!/usr/bin/env python3
"""
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
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PY = sys.executable          # whichever interpreter runs this (venv-aware)
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


def latest_card_mtime(episodic_dir: Path, sid: str) -> float:
    short = sid.split("-")[0]
    best = 0.0
    for card in episodic_dir.glob(f"*_{short}.md"):
        best = max(best, card.stat().st_mtime)
    return best


def main() -> int:
    now = time.time()
    total = skipped = 0
    for proj_dir, os_name in targets():
        if not proj_dir.is_dir():
            continue
        episodic = Path.home() / ".cache" / f"{os_name}-memory" / "episodic"
        for jsonl in sorted(proj_dir.glob("*.jsonl")):
            if jsonl.name.startswith("agent-"):
                continue
            st = jsonl.stat()
            if now - st.st_mtime > ACTIVE_WINDOW_H * 3600 or st.st_size < MIN_BYTES:
                continue
            sid = jsonl.stem
            if latest_card_mtime(episodic, sid) >= st.st_mtime:
                skipped += 1          # card already covers the latest activity
                continue
            payload = json.dumps({
                "session_id": sid,
                "transcript_path": str(jsonl),
                "hook_event_name": "checkpoint",
            })
            r = subprocess.run([PY, str(ENGINE)], input=payload, text=True,
                               env={**os.environ, "MEMORY_OS": os_name},
                               capture_output=True, timeout=300)
            ok = "ok" if r.returncode == 0 else f"rc={r.returncode}"
            print(f"[checkpoint] {os_name} {sid[:8]} "
                  f"({st.st_size//1024}KB) -> {ok}")
            total += 1
    print(f"[checkpoint] done: {total} captured, {skipped} already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
