import json
import os
import unittest
from unittest import mock

from timetracker import sync
from timetracker.models import Entry, new_id

from helpers import Device, project, temp_root


class SyncTests(unittest.TestCase):
    def setUp(self):
        root = temp_root(self)
        self.shared = os.path.join(root, "shared")
        self.a = Device(root, "desktop")
        self.b = Device(root, "laptop")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def sync(self, device):
        return sync.sync(device.store, self.shared, device.settings.device_id, device.settings.device_name)

    def add_entry(self, device, pid, start, end):
        return device.store.save(Entry(id=new_id(), project_id=pid, user="alice",
                                       device=device.settings.device_id, start=start, end=end))

    def test_records_from_both_devices_reach_both(self):
        p = self.a.store.save(project("Shared"))
        self.add_entry(self.a, p.id, 0, 100)
        self.sync(self.a)
        self.sync(self.b)
        self.add_entry(self.b, p.id, 200, 300)
        self.sync(self.b)
        self.sync(self.a)
        for d in (self.a, self.b):
            self.assertEqual(len(d.store.entries()), 2)
            self.assertEqual(d.store.get_project(p.id).name, "Shared")

    def test_offline_work_on_both_devices_loses_nothing(self):
        p = self.a.store.save(project())
        self.sync(self.a)
        self.sync(self.b)
        # Both devices now work offline, then sync in either order.
        for i in range(5):
            self.add_entry(self.a, p.id, i * 1000, i * 1000 + 100)
            self.add_entry(self.b, p.id, i * 1000 + 500, i * 1000 + 600)
        self.sync(self.b)
        self.sync(self.a)
        self.sync(self.b)
        ids_a = {e.id for e in self.a.store.entries()}
        ids_b = {e.id for e in self.b.store.entries()}
        self.assertEqual(len(ids_a), 10)
        self.assertEqual(ids_a, ids_b)

    def test_concurrent_edits_converge(self):
        p = self.a.store.save(project())
        e = self.add_entry(self.a, p.id, 0, 100)
        self.sync(self.a)
        self.sync(self.b)
        on_a = self.a.store.get_entry(e.id)
        on_a.note = "from desktop"
        self.a.store.save(on_a)
        on_b = self.b.store.get_entry(e.id)
        on_b.note = "from laptop"
        self.b.store.save(on_b)
        for d in (self.a, self.b, self.a):
            self.sync(d)
        self.assertEqual(self.a.store.get_entry(e.id), self.b.store.get_entry(e.id))

    def test_sync_is_idempotent(self):
        p = self.a.store.save(project())
        self.add_entry(self.a, p.id, 0, 100)
        self.sync(self.a)
        self.assertTrue(self.sync(self.b).changed)
        self.assertFalse(self.sync(self.b).changed)

    def test_corrupt_and_foreign_files_are_skipped(self):
        os.makedirs(self.shared, exist_ok=True)
        with open(os.path.join(self.shared, "tt-broken.json"), "w") as f:
            f.write("{not json")
        with open(os.path.join(self.shared, "tt-old.json"), "w") as f:
            json.dump({"format": 99}, f)
        result = self.sync(self.a)
        self.assertEqual(len(result.errors), 2)
        self.assertTrue(os.path.exists(sync.snapshot_path(self.shared, self.a.settings.device_id)))

    def test_unchanged_snapshot_is_not_reparsed(self):
        p = self.a.store.save(project())
        self.add_entry(self.a, p.id, 0, 100)
        self.sync(self.a)
        self.assertEqual(self.sync(self.b).files_read, 1)       # a's file (b's own did not exist yet)
        result = self.sync(self.b)
        self.assertEqual(result.files_read, 0)
        self.assertIn(self.a.settings.device_id, result.devices)
        self.add_entry(self.a, p.id, 200, 300)
        self.sync(self.a)
        self.assertEqual(self.sync(self.b).entries_changed, 1)

    def test_same_size_rewrite_is_not_mistaken_for_unchanged(self):
        p = self.a.store.save(project())
        e = self.add_entry(self.a, p.id, 0, 100)
        e.note = "aa"
        self.a.store.save(e)
        self.sync(self.a)
        self.sync(self.b)
        e.note = "bb"                              # same length, written immediately afterwards
        self.a.store.save(e)
        self.sync(self.a)
        self.assertEqual(self.sync(self.b).entries_changed, 1)
        self.assertEqual(self.b.store.get_entry(e.id).note, "bb")

    def test_rename_retried_while_target_is_locked(self):
        real_replace, calls = os.replace, []

        def flaky_replace(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(src, dst)

        with mock.patch("timetracker.sync.os.replace", flaky_replace):
            self.sync(self.a)
        self.assertEqual(len(calls), 3)
        self.assertEqual([f for f in os.listdir(self.shared) if f.startswith(".tmp")], [])

    def test_rename_gives_up_and_cleans_up(self):
        with mock.patch("timetracker.sync.os.replace", side_effect=PermissionError(5, "denied")), \
                mock.patch("timetracker.sync.time.sleep"):
            with self.assertRaises(PermissionError):
                self.sync(self.a)
        self.assertEqual(os.listdir(self.shared), [])

    def test_device_names_are_reported(self):
        self.sync(self.a)
        result = self.sync(self.b)
        self.assertEqual(result.devices[self.a.settings.device_id], "desktop")


if __name__ == "__main__":
    unittest.main()
