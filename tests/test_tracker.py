import os
import unittest

from timetracker import sync
from timetracker.tracker import Tracker

from helpers import Device, FakeClock, project, temp_root


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_root(self)
        self.device = Device(self.root, "desktop")
        self.addCleanup(self.device.close)
        self.store, self.settings = self.device.store, self.device.settings
        self.settings.dwell_seconds = 30
        self.clock = FakeClock()
        self.idle = 0.0
        self.title = None
        self.messages = []
        self.tracker = self.make_tracker(self.device)
        self.web = self.store.save(project("Web", keywords="acme, website"))
        self.app = self.store.save(project("App", keywords="android studio"))

    def make_tracker(self, device):
        return Tracker(device.store, device.settings, clock=self.clock, idle_fn=lambda: self.idle,
                       window_fn=lambda: self.title, notify=self.messages.append)

    def live(self):
        return [e for e in self.store.entries() if not e.deleted]

    def test_one_click_switch_and_stop(self):
        self.tracker.toggle(self.web.id)
        self.clock.advance(600)
        self.tracker.toggle(self.app.id)
        self.clock.advance(300)
        self.tracker.toggle(self.app.id)          # second click stops
        entries = self.live()
        self.assertEqual([(e.project_id, e.duration, e.running) for e in entries],
                         [(self.web.id, 600, False), (self.app.id, 300, False)])
        self.assertIsNone(self.tracker.current)

    def test_idle_pauses_and_resumes_and_gap_can_be_reclaimed(self):
        self.settings.idle_minutes = 5
        self.tracker.start(self.web.id)
        self.clock.advance(1200)                  # worked 10 min, then away 10 min
        self.idle = 600
        self.tracker.tick()
        self.assertIsNone(self.tracker.current)
        self.assertEqual(self.tracker.paused_project, self.web.id)
        self.assertEqual(self.live()[0].duration, 600)

        self.idle = 1
        self.tracker.tick()
        self.assertEqual(self.tracker.current.project_id, self.web.id)
        gap_start, gap_end, _ = self.tracker.idle_gap
        self.assertEqual(gap_end - gap_start, 599)

        self.tracker.reclaim_idle()
        reclaimed = [e for e in self.live() if e.source == "idle-reclaim"]
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].duration, 599)

    def test_context_auto_starts_after_dwell(self):
        self.title = "index.html - acme website - Visual Studio Code"
        first_seen = self.clock()
        self.tracker.tick()
        self.assertIsNone(self.tracker.current)
        self.clock.advance(31)
        self.tracker.tick()
        self.assertEqual(self.tracker.current.project_id, self.web.id)
        self.assertEqual(self.tracker.current.source, "auto")
        self.assertEqual(self.tracker.current.start, first_seen)     # dated from when the window appeared

    def test_backdated_switch_does_not_overlap_previous_entry(self):
        self.title = "Android Studio"
        self.tracker.tick()                          # candidate App seen from t0
        self.clock.advance(10)
        self.tracker.start(self.web.id)              # user picks Web manually at t0+10
        self.title = "Android Studio - build"
        self.clock.advance(31)
        self.tracker.tick()
        self.clock.advance(31)
        self.tracker.tick()
        web, app = self.live()
        self.assertEqual(self.tracker.current.project_id, self.app.id)
        self.assertLessEqual(web.end, app.start)
        self.assertGreater(web.duration, 0)

    def test_longest_keyword_wins(self):
        self.store.save(project("Acme app", keywords="acme app"))
        self.assertEqual(self.tracker.match_project("ACME APP - notes"),
                         next(p.id for p in self.store.projects() if p.name == "Acme app"))

    def test_brief_window_change_does_not_switch(self):
        self.tracker.start(self.web.id)
        self.title = "Android Studio"
        self.tracker.tick()
        self.clock.advance(10)
        self.title = "acme website"
        self.tracker.tick()
        self.clock.advance(60)
        self.tracker.tick()
        self.assertEqual(self.tracker.current.project_id, self.web.id)

    def test_suggestion_mode(self):
        self.settings.auto_switch = False
        self.tracker.start(self.web.id)
        self.title = "Android Studio"
        self.tracker.tick()
        self.clock.advance(31)
        self.tracker.tick()
        self.assertEqual(self.tracker.current.project_id, self.web.id)
        self.assertEqual(self.tracker.suggestion, self.app.id)
        self.tracker.accept_suggestion()
        self.assertEqual(self.tracker.current.project_id, self.app.id)

    def test_manual_stop_is_not_immediately_undone(self):
        self.title = "acme website"
        self.tracker.start(self.web.id)
        self.tracker.stop()
        self.clock.advance(120)
        self.tracker.tick()
        self.clock.advance(120)
        self.tracker.tick()
        self.assertIsNone(self.tracker.current)

    def test_recover_closes_entry_left_running_after_crash(self):
        self.tracker.start(self.web.id)
        self.clock.advance(60)
        self.tracker.heartbeat()
        restarted = self.make_tracker(self.device)
        restarted.recover()
        (e,) = self.live()
        self.assertFalse(e.running)
        self.assertEqual(e.duration, 60)

    def test_tracking_hands_over_between_devices(self):
        shared = os.path.join(self.root, "shared")
        laptop = Device(self.root, "laptop")
        self.addCleanup(laptop.close)
        laptop.settings.user = self.settings.user
        laptop_tracker = self.make_tracker(laptop)

        def sync_all():
            for d, t in ((self.device, self.tracker), (laptop, laptop_tracker)):
                sync.sync(d.store, shared, d.settings.device_id, d.settings.device_name)
                t.after_sync()

        self.tracker.start(self.web.id)           # desktop at the office
        sync_all()
        self.clock.advance(3600)
        self.tracker.heartbeat()                  # desktop left running
        laptop_tracker.start(self.web.id)         # continue at home on the laptop
        self.clock.advance(60)
        sync_all()
        sync_all()

        self.assertIsNone(self.tracker.current)
        self.assertIsNotNone(laptop_tracker.current)
        self.assertTrue(any("moved" in m for m in self.messages))
        for store in (self.store, laptop.store):
            entries = [e for e in store.entries() if not e.deleted]
            self.assertEqual(len(entries), 2)
            desk = next(e for e in entries if e.device == self.settings.device_id)
            self.assertFalse(desk.running)
            self.assertEqual(desk.duration, 3600)


if __name__ == "__main__":
    unittest.main()
