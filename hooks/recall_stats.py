#!/usr/bin/env python3
"""
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
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_lib as ml  # noqa: E402

LOG_DB = ml.DB_PATH.parent / "recall_log.db"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all-kinds", action="store_true")
    args = ap.parse_args()

    if not LOG_DB.exists():
        print(f"[recall-stats] no log yet at {LOG_DB} — recall logging starts with the first hook run")
        return 0
    con = sqlite3.connect(LOG_DB)
    since = int(time.time()) - args.days * 86400
    n, sessions, first = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT session), MIN(ts) FROM recalls WHERE ts>?",
        (since,)).fetchone()
    if not n:
        print(f"[recall-stats] no recalls in the last {args.days}d")
        return 0
    days_covered = max(1, round((time.time() - first) / 86400))
    print(f"[recall-stats] {n} recalls / {sessions} sessions over ~{days_covered}d "
          f"(window {args.days}d, {ml.OS_NAME})")

    kinds = "" if args.all_kinds else "AND kind='memory'"
    print("\nTOP RECALLED" + ("" if args.all_kinds else " (memories)"))
    for path, c in con.execute(
            f"SELECT path, COUNT(*) c FROM recalls WHERE ts>? {kinds} "
            "GROUP BY path ORDER BY c DESC LIMIT 15", (since,)):
        print(f"  {c:4d}  {Path(path).name}")

    # the eviction view: memory files recall never touched in the window
    recalled = {Path(p).name for (p,) in con.execute(
        "SELECT DISTINCT path FROM recalls WHERE ts>? AND kind='memory'", (since,))}
    now = time.time()
    cold = []
    for f in sorted(ml.MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md" or f.name in recalled:
            continue
        age_d = int((now - f.stat().st_mtime) / 86400)
        cold.append((age_d, f.name))
    cold.sort(reverse=True)
    print(f"\nZERO RECALLS in window: {len(cold)} memory files"
          f" (eviction candidates ONLY if window ≥30d AND edit-age >60d)")
    for age, name in cold[:20]:
        flag = " ←candidate" if days_covered >= 30 and age > 60 else ""
        print(f"  {age:4d}d  {name}{flag}")
    if len(cold) > 20:
        print(f"  ... +{len(cold)-20} more (run with wider terminal patience)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
