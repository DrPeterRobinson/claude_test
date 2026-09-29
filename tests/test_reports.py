import csv
import os
import unittest

from timetracker import reports

from helpers import entry, project, temp_root


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.web = project("Web", client="Acme", rate=60.0)
        self.app = project("App", client="Acme", rate=90.0)
        self.other = project("Other", client="Globex", rate=100.0)
        self.projects = {p.id: p for p in (self.web, self.app, self.other)}

    def test_summary_by_project_clips_to_range(self):
        entries = [entry(self.web.id, 0, 3600), entry(self.web.id, 3600, 7200),
                   entry(self.app.id, -1800, 1800),     # half inside the range
                   entry(self.app.id, 0, 3600, deleted=True)]
        rows = {r.label: r for r in reports.summarise(entries, self.projects, 0, 10_000)}
        self.assertEqual(rows["Web"].seconds, 7200)
        self.assertAlmostEqual(rows["Web"].amount, 120.0)
        self.assertEqual(rows["App"].seconds, 1800)
        self.assertAlmostEqual(rows["App"].amount, 45.0)

    def test_summary_by_client_and_user(self):
        entries = [entry(self.web.id, 0, 3600, user="alice"), entry(self.other.id, 0, 3600, user="bob")]
        by_client = {r.label: r.seconds for r in reports.summarise(entries, self.projects, None, None, "client")}
        by_user = {r.label: r.seconds for r in reports.summarise(entries, self.projects, None, None, "user")}
        self.assertEqual(by_client, {"Acme": 3600, "Globex": 3600})
        self.assertEqual(by_user, {"alice": 3600, "bob": 3600})

    def test_round_up(self):
        self.assertEqual(reports.round_up(0, 6), 0)
        self.assertEqual(reports.round_up(1, 6), 360)
        self.assertEqual(reports.round_up(360, 6), 360)
        self.assertEqual(reports.round_up(361, 6), 720)
        self.assertEqual(reports.round_up(123, 0), 123)

    def test_invoice_only_includes_client_and_totals_match(self):
        entries = [entry(self.web.id, 0, 5400),              # 1.5 h @ 60 = 90
                   entry(self.app.id, 0, 3000),              # 50 min -> 54 min billed = 0.9 h @ 90 = 81
                   entry(self.other.id, 0, 3600)]
        inv = reports.build_invoice(entries, self.projects, "Acme", 0, 10_000, "INV-1", rounding_minutes=6)
        self.assertEqual([l.project for l in inv.lines], ["App", "Web"])
        self.assertEqual(inv.total, 171.0)
        html = reports.invoice_html(inv)
        self.assertIn("INV-1", html)
        self.assertIn("171.00", html)

    def test_invoice_html_escapes_names(self):
        p = project("<script>", client="A&B", rate=10)
        inv = reports.build_invoice([entry(p.id, 0, 3600)], {p.id: p}, "A&B", 0, 3600, "X")
        html = reports.invoice_html(inv)
        self.assertNotIn("<script>", html)
        self.assertIn("A&amp;B", html)

    def test_csv_export(self):
        path = os.path.join(temp_root(self), "out.csv")
        reports.write_entries_csv(path, [entry(self.web.id, 0, 3600), entry(self.web.id, 0, 60, deleted=True)],
                                  self.projects)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3:6], ["1.00", "Web", "Acme"])


if __name__ == "__main__":
    unittest.main()
