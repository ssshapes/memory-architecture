#!/usr/bin/env python3
"""
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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_lib as ml


def main():
    verbose = "-v" in sys.argv
    # Single-flight: 10+ sessions spawn together and every SessionStart fires this.
    # One sync does the work; the rest skip instantly (the first one's result serves all).
    import fcntl
    ml.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = open(ml.DB_PATH.parent / "sync.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("sync: another sync in progress, skipping")
        return
    if "--rebuild" in sys.argv and ml.DB_PATH.exists():
        ml.DB_PATH.unlink()
        print(f"removed {ml.DB_PATH}")
    t0 = time.time()
    db = ml.connect()
    stats = ml.sync(db, verbose=verbose)
    dt = time.time() - t0
    print(f"sync: +{stats['added']} new, ~{stats['updated']} updated, "
          f"-{stats['removed']} removed, {stats['total']} total docs in {dt:.1f}s")
    db.close()


if __name__ == "__main__":
    main()
