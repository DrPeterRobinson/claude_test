"""Randomised convergence test for the sync design.

Three simulated devices perform random operations (create, edit, close,
delete entries; create and edit projects) and sync with the shared folder in
random order, some while "offline" (not syncing). After a final round of
syncs, every device must hold identical data and no created entry may be
missing.

    python experiments/convergence.py [trials] [ops_per_trial]
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from timetracker import sync                         # noqa: E402
from timetracker.models import Entry, Project, new_id, to_dict   # noqa: E402
from timetracker.store import Store                  # noqa: E402


def snapshot(store):
    # Keyed by id: list order is not meaningful (e.g. projects with the same name).
    return ({p.id: to_dict(p) for p in store.projects(include_archived=True)},
            {e.id: to_dict(e) for e in store.entries(include_deleted=True)})


def trial(rng: random.Random, ops: int) -> tuple[bool, bool, int]:
    with tempfile.TemporaryDirectory() as root:
        shared = os.path.join(root, "shared")
        devices = [Store(os.path.join(root, f"d{i}.db"), f"device{i}") for i in range(3)]
        created: set[str] = set()
        for _ in range(ops):
            store = rng.choice(devices)
            kind = rng.random()
            projects = store.projects(include_archived=True)
            entries = store.entries(include_deleted=True)
            if kind < 0.08 or not projects:
                store.save(Project(id=new_id(), name=f"P{rng.randint(0, 999)}"))
            elif kind < 0.12:
                p = rng.choice(projects)
                p.name, p.archived = f"P{rng.randint(0, 999)}", rng.random() < 0.3
                store.save(p)
            elif kind < 0.40 or not entries:
                start = rng.uniform(0, 1e5)
                e = Entry(id=new_id(), project_id=rng.choice(projects).id, user="alice", device=store.device_id,
                          start=start, end=start + rng.uniform(1, 7200), running=rng.random() < 0.3)
                store.save(e)
                created.add(e.id)
            elif kind < 0.55:
                e = rng.choice(entries)
                e.note, e.end = f"n{rng.randint(0, 99)}", e.end + rng.uniform(0, 600)   # heartbeat / edit
                store.save(e)
            elif kind < 0.65:
                e = rng.choice(entries)
                e.running = False
                store.save(e)
            elif kind < 0.70:
                e = rng.choice(entries)
                e.deleted = True
                store.save(e)
            else:
                sync.sync(store, shared, store.device_id)          # other ops happen "offline"
        for _ in range(2):
            for store in devices:
                sync.sync(store, shared, store.device_id)
        states = [snapshot(s) for s in devices]
        converged = all(s == states[0] for s in states)
        ids = set(states[0][1])
        complete = created <= ids
        for s in devices:
            s.close()
        return converged, complete, len(created)


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    ops = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    rng = random.Random(2027)
    started = time.perf_counter()
    results = [trial(rng, ops) for _ in range(trials)]
    print(f"Trials: {trials}, operations per trial: {ops}")
    print(f"Converged: {sum(r[0] for r in results)}/{trials}")
    print(f"No entries lost: {sum(r[1] for r in results)}/{trials}")
    print(f"Entries created in total: {sum(r[2] for r in results)}")
    print(f"Time: {time.perf_counter() - started:.1f} s")


if __name__ == "__main__":
    main()
