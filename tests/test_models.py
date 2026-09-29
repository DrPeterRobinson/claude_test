import unittest
from dataclasses import replace

from timetracker.models import merge_entry, merge_project

from helpers import entry, project


class MergeEntryTests(unittest.TestCase):
    def test_newer_version_wins(self):
        a = entry(note="old", updated_at=1, updated_by="d1")
        b = replace(a, note="new", updated_at=2)
        self.assertEqual(merge_entry(a, b).note, "new")
        self.assertEqual(merge_entry(b, a).note, "new")

    def test_merge_is_commutative_and_idempotent(self):
        a = entry(running=True, end=500, updated_at=5, updated_by="d1")
        b = replace(a, running=False, end=300, updated_at=3, updated_by="d2")
        ab, ba = merge_entry(a, b), merge_entry(b, a)
        self.assertEqual(ab, ba)
        self.assertEqual(merge_entry(ab, b), ab)
        self.assertEqual(merge_entry(ab, ab), ab)

    def test_closing_cannot_be_undone_by_later_heartbeat(self):
        closed = entry(running=False, end=300, updated_at=3, updated_by="d2")
        heartbeat = replace(closed, running=True, end=900, updated_at=9, updated_by="d1")
        merged = merge_entry(heartbeat, closed)
        self.assertFalse(merged.running)
        self.assertEqual(merged.end, 300)

    def test_merge_is_associative(self):
        # Found by experiments/convergence.py: one running and two closed copies
        # used to give different results depending on merge order.
        running = entry(running=True, end=900, updated_at=3, updated_by="d1")
        closed_late = replace(running, running=False, end=300, updated_at=2, updated_by="d2")
        closed_early = replace(running, running=False, end=200, updated_at=1, updated_by="d3")
        copies = (running, closed_late, closed_early)
        results = {tuple(sorted(vars(merge_entry(merge_entry(x, y), z)).items()))
                   for x in copies for y in copies for z in copies if len({id(x), id(y), id(z)}) == 3}
        self.assertEqual(len(results), 1)
        self.assertEqual(dict(results.pop())["end"], 300)

    def test_delete_is_permanent(self):
        deleted = entry(deleted=True, updated_at=1, updated_by="d1")
        edited = replace(deleted, deleted=False, note="edit", updated_at=2)
        self.assertTrue(merge_entry(edited, deleted).deleted)

    def test_tie_broken_by_device(self):
        a = entry(note="a", updated_at=1, updated_by="aaa")
        b = replace(a, note="b", updated_by="bbb")
        self.assertEqual(merge_entry(a, b).note, "b")
        self.assertEqual(merge_entry(b, a).note, "b")


class MergeProjectTests(unittest.TestCase):
    def test_last_writer_wins_including_unarchive(self):
        archived = project(archived=True, updated_at=1, updated_by="d1")
        restored = replace(archived, archived=False, updated_at=2)
        self.assertFalse(merge_project(archived, restored).archived)


if __name__ == "__main__":
    unittest.main()
