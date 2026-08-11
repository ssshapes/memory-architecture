#!/usr/bin/env python3
"""
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
from __future__ import annotations

import sys
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


def main() -> int:
    now = time.time()
    try:
        idx = INDEX.read_text(encoding="utf-8")
    except OSError:
        print("[metabolism] MEMORY.md unreadable — skipping")
        return 0
    entries = sum(1 for l in idx.splitlines() if l.lstrip().startswith("- "))
    size = len(idx.encode("utf-8"))

    dormant = 0
    total_files = 0
    for f in MEM_DIR.glob("*.md"):
        if f.name == "MEMORY.md":
            continue
        total_files += 1
        try:
            if (now - f.stat().st_mtime) > DORMANT_DAYS * 86400:
                dormant += 1
        except OSError:
            pass

    old_cards = 0
    if EPI_DIR.is_dir():
        for f in EPI_DIR.glob("*.md"):
            try:
                if (now - f.stat().st_mtime) > EPISODIC_DAYS * 86400:
                    old_cards += 1
            except OSError:
                pass

    print(f"[metabolism] index {entries} entries / {size:,}B "
          f"(warn {BYTES_WARN:,}) · {total_files} memories, {dormant} dormant >{DORMANT_DAYS}d "
          f"· {old_cards} episodic cards >{EPISODIC_DAYS}d undistilled")

    pressure = []
    if entries > ENTRY_PRESSURE:
        pressure.append(f"index at {entries} entries (>{ENTRY_PRESSURE})")
    if size > BYTES_WARN:
        pressure.append(f"index {size:,}B over soft cap")
    if dormant > DORMANT_PRESSURE:
        pressure.append(f"{dormant} dormant memories")
    if old_cards > EPISODIC_PRESSURE:
        pressure.append(f"{old_cards} undistilled episodic cards")
    if pressure:
        print("[metabolism] PRESSURE: " + "; ".join(pressure) +
              " — time for a consolidation pass (propose-only, per doctrine)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
