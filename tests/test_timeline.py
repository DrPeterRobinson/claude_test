import unittest

from timetracker.timeline import find_overlaps, resolve

from helpers import entry


class OverlapTests(unittest.TestCase):
    def test_detects_overlap_for_same_user_only(self):
        a = entry(start=0, end=100)
        b = entry(start=50, end=150)
        c = entry(start=60, end=70, user="bob")
        d = entry(start=150, end=200)          # touching, not overlapping
        pairs = find_overlaps([a, b, c, d])
        self.assertEqual([(x.id, y.id) for x, y in pairs], [(a.id, b.id)])

    def test_deleted_entries_ignored(self):
        self.assertEqual(find_overlaps([entry(start=0, end=100), entry(start=50, end=150, deleted=True)]), [])

    def test_partial_overlap_trims_earlier(self):
        a, b = entry(start=0, end=100), entry(start=50, end=150)
        changes = resolve(a, b)
        self.assertEqual(len(changes), 1)
        self.assertEqual((changes[0].id, changes[0].end), (a.id, 50))

    def test_contained_entry_splits_earlier_deterministically(self):
        a, b = entry(start=0, end=100), entry(start=40, end=60)
        first, second = resolve(a, b), resolve(b, a)
        self.assertEqual([(c.start, c.end) for c in first], [(0, 40), (60, 100)])
        self.assertEqual([c.id for c in first], [c.id for c in second])
        self.assertEqual(find_overlaps(first + [b]), [])

    def test_two_running_entries_close_the_earlier(self):
        a = entry(start=0, end=500, running=True, device="desk")
        b = entry(start=300, end=500, running=True, device="laptop")
        (closed,) = resolve(a, b)
        self.assertEqual((closed.id, closed.end, closed.running), (a.id, 300, False))

    def test_split_of_running_entry_keeps_tail_running(self):
        a = entry(start=0, end=500, running=True)
        b = entry(start=100, end=200)
        head, tail = resolve(a, b)
        self.assertFalse(head.running)
        self.assertTrue(tail.running)
        self.assertEqual((tail.start, tail.end), (200, 500))

    def test_same_start_removes_one(self):
        a, b = entry(start=0, end=100, id="a"), entry(start=0, end=100, id="b")
        (loser,) = resolve(a, b)
        self.assertTrue(loser.deleted)

    def test_inputs_not_mutated(self):
        a, b = entry(start=0, end=100), entry(start=50, end=150)
        resolve(a, b)
        self.assertEqual(a.end, 100)


if __name__ == "__main__":
    unittest.main()
