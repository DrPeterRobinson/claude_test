"""Measures sync cost as the number of entries grows.

    python experiments/performance.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from timetracker import sync                         # noqa: E402
from timetracker.models import Entry, Project, new_id   # noqa: E402
from timetracker.store import Store                  # noqa: E402

SIZES = (1_000, 10_000, 50_000)


def main():
    rng = random.Random(1)
    print(f"{'Entries':>8} {'File (KB)':>10} {'Export (s)':>11} {'First import (s)':>17} "
          f"{'Re-merge (s)':>13} {'Unchanged (s)':>14}")
    for n in SIZES:
        with tempfile.TemporaryDirectory() as root:
            shared = os.path.join(root, "shared")
            a = Store(os.path.join(root, "a.db"), "a")
            projects = [a.save(Project(id=new_id(), name=f"P{i}")) for i in range(20)]
            for i in range(n):
                start = i * 3600.0
                a._write(Entry(id=new_id(), project_id=rng.choice(projects).id, user="alice", device="a",
                               start=start, end=start + 1800, updated_at=start, updated_by="a"), commit=False)
            a.commit()
            t0 = time.perf_counter()
            path = sync.export_snapshot(a, shared, "a")
            t1 = time.perf_counter()
            b = Store(os.path.join(root, "b.db"), "b")
            sync.import_snapshots(b, shared)
            t2 = time.perf_counter()
            b.seen_snapshots.clear()                   # force a full parse and merge of the same data
            sync.import_snapshots(b, shared)
            t3 = time.perf_counter()
            sync.import_snapshots(b, shared)           # file unchanged since last read
            t4 = time.perf_counter()
            size_kb = os.path.getsize(path) / 1024
            print(f"{n:8d} {size_kb:10.0f} {t1 - t0:11.2f} {t2 - t1:17.2f} {t3 - t2:13.2f} {t4 - t3:14.4f}")
            a.close()
            b.close()


if __name__ == "__main__":
    main()
