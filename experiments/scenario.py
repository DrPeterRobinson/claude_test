"""Simulated working day: measures user interactions and recording accuracy.

A scripted day is replayed against the real Tracker, Store and sync code with
a simulated clock, simulated keyboard/mouse activity and simulated window
titles. Two computers share a sync folder. The recorded time per project is
compared with the scripted ground truth.

    python experiments/scenario.py [--suggest]

--suggest runs in suggestion mode (no automatic switching); the simulated
user clicks "Switch" as soon as a suggestion appears, and each click is
counted as an interaction.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from timetracker import sync                                   # noqa: E402
from timetracker.config import Settings                        # noqa: E402
from timetracker.models import Project, new_id                 # noqa: E402
from timetracker.store import Store                            # noqa: E402
from timetracker.tracker import Tracker                        # noqa: E402

DAY = datetime(2027, 2, 10)


def at(hm: str) -> float:
    h, m = map(int, hm.split(":"))
    return DAY.replace(hour=h, minute=m).timestamp()


WEB_TITLE = "index.html - acme website - Visual Studio Code"
APP_TITLE = "MainActivity.kt - Android Studio"

# (device, start, end, window title, project) -- periods of active work
SCHEDULE = [
    ("desktop", "09:00", "10:30", WEB_TITLE, "Web"),
    ("desktop", "10:30", "11:00", APP_TITLE, "App"),
    ("desktop", "11:15", "12:30", APP_TITLE, "App"),
    ("desktop", "13:15", "15:00", WEB_TITLE, "Web"),
    ("laptop", "16:00", "17:30", APP_TITLE, "App"),
]
APP_RUNNING = {"desktop": ("08:55", "18:00"), "laptop": ("16:00", "18:00")}   # the app is left open all day
MANUAL_TIMER_INTERACTIONS = 9   # ideal disciplined user: every start, switch and stop (see report)

STEP = 2          # seconds between ticks (as in the GUI)
HEARTBEAT = 30
SYNC_EVERY = 60


def ground_truth() -> dict[str, float]:
    totals: dict[str, float] = {}
    for _, s, e, _, name in SCHEDULE:
        totals[name] = totals.get(name, 0) + at(e) - at(s)
    return totals


class SimDevice:
    def __init__(self, name, root, shared, clock, suggest_mode):
        self.name = name
        self.dir = os.path.join(root, name)
        os.makedirs(self.dir)
        self.settings = Settings(user="alice", device_name=name, auto_switch=not suggest_mode)
        self.store = Store(os.path.join(self.dir, "db.sqlite"), self.settings.device_id)
        self.shared = shared
        self.clock = clock
        self.last_input = clock.t
        self.title = "Desktop"
        self.tracker = None

    def launch(self):
        self.tracker = Tracker(self.store, self.settings, clock=self.clock,
                               idle_fn=lambda: self.clock.t - self.last_input, window_fn=lambda: self.title)
        self.tracker.recover()

    def do_sync(self):
        sync.sync(self.store, self.shared, self.settings.device_id, self.name)
        self.tracker.after_sync()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def run(suggest_mode: bool = False) -> dict:
    with tempfile.TemporaryDirectory() as root:
        shared = os.path.join(root, "shared")
        clock = Clock(at("08:55"))
        devices = {n: SimDevice(n, root, shared, clock, suggest_mode) for n in APP_RUNNING}
        projects = {}
        desktop = devices["desktop"]
        for name, keywords in (("Web", "acme, website"), ("App", "acme-app, android studio")):
            projects[name] = desktop.store.save(Project(id=new_id(), name=name, client="Acme", rate=50,
                                                        keywords=keywords))
        interactions = 0
        end = at("18:00")
        while clock.t <= end:
            for d in devices.values():
                start_s, end_s = APP_RUNNING[d.name]
                if d.tracker is None and clock.t >= at(start_s):
                    d.launch()
                    d.do_sync()                                  # pick up projects from the other device
                if d.tracker is None or clock.t > at(end_s):
                    continue
                for dev, s, e, title, _ in SCHEDULE:
                    if dev == d.name and at(s) <= clock.t < at(e):
                        d.last_input, d.title = clock.t, title
                d.tracker.tick()
                if d.tracker.suggestion:                          # simulated user clicks "Switch"
                    d.tracker.accept_suggestion()
                    interactions += 1
                elapsed = int(clock.t - at("08:55"))
                if elapsed % HEARTBEAT == 0:
                    d.tracker.heartbeat()
                if elapsed % SYNC_EVERY == 0:
                    d.do_sync()
            clock.t += STEP
        for d in devices.values():
            d.tracker.shutdown()
            d.do_sync()
        for d in devices.values():
            d.do_sync()

        truth = ground_truth()
        names = {p.id: name for name, p in projects.items()}
        stores_agree = all(
            [(e.id, e.start, e.end, e.deleted) for e in d.store.entries(include_deleted=True)]
            == [(e.id, e.start, e.end, e.deleted) for e in desktop.store.entries(include_deleted=True)]
            for d in devices.values())
        recorded: dict[str, float] = {}
        entries = desktop.store.entries()
        for e in entries:
            recorded[names[e.project_id]] = recorded.get(names[e.project_id], 0) + e.duration
        for d in devices.values():
            d.store.close()
        return {"truth": truth, "recorded": recorded, "entries": len(entries), "interactions": interactions,
                "stores_agree": stores_agree}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suggest", action="store_true")
    args = parser.parse_args()
    result = run(args.suggest)
    mode = "suggestion" if args.suggest else "automatic"
    print(f"Mode: {mode} switching")
    print(f"{'Project':8} {'Truth (min)':>12} {'Recorded (min)':>15} {'Error (s)':>10} {'Error %':>8}")
    for name, truth in result["truth"].items():
        rec = result["recorded"].get(name, 0.0)
        print(f"{name:8} {truth / 60:12.1f} {rec / 60:15.2f} {rec - truth:10.0f} {100 * (rec - truth) / truth:8.2f}")
    total_t, total_r = sum(result["truth"].values()), sum(result["recorded"].values())
    print(f"{'Total':8} {total_t / 60:12.1f} {total_r / 60:15.2f} {total_r - total_t:10.0f} "
          f"{100 * (total_r - total_t) / total_t:8.2f}")
    print(f"Entries recorded: {result['entries']}")
    print(f"User interactions: {result['interactions']} (ideal manual timer: {MANUAL_TIMER_INTERACTIONS})")
    print(f"Both devices hold identical data: {result['stores_agree']}")


if __name__ == "__main__":
    main()
