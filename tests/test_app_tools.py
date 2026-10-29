"""Tests for tools/app_tools.py. Run: python3 -m unittest discover -s tests -v  (standard library only)."""
import contextlib
import csv
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import app_tools as at  # noqa: E402

PAIRS = json.loads((HERE / "labelled_pairs.json").read_text(encoding="utf-8"))


class Workspace(unittest.TestCase):
    """Each test gets an empty temporary workspace; the module paths are pointed at it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seekcrew_test_"))
        self.saved = (at.ROOT, at.APPS, at.TRACKER, at.BACKUPS, at.CONFIG)
        at.ROOT = self.tmp
        at.APPS = self.tmp / "applications"
        at.TRACKER = self.tmp / "tracker.csv"
        at.BACKUPS = self.tmp / ".backups"
        at.CONFIG = self.tmp / "seekcrew.config.json"
        at.CONFIG.write_text(json.dumps(PAIRS["config"]), encoding="utf-8")

    def tearDown(self):
        at.ROOT, at.APPS, at.TRACKER, at.BACKUPS, at.CONFIG = self.saved
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def seed_tracker(self):
        rows = []
        for i, e in enumerate(PAIRS["existing"], 1):
            row = dict((h, "") for h in at.HEADERS)
            row.update({"ID": str(i), "Company": e["company"], "Role": e["role"], "Job link": e["url"],
                        "Status": "Applied", "Notes": ("Job ID/reference: %s." % e["job_id"]) if e["job_id"] else ""})
            rows.append(row)
        at.write_csv(at.TRACKER, rows)

    def run_cli(self, *argv):
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["app_tools.py"] + list(argv)
        try:
            with contextlib.redirect_stdout(out):
                try:
                    code = at.main()
                except SystemExit as e:
                    code = e.code
        finally:
            sys.argv = old
        return code, out.getvalue()


class LabelledPairs(Workspace):
    def test_every_case(self):
        self.seed_tracker()
        results = []
        for c in PAIRS["cases"]:
            verdict, _ = at.check(c["employer"], c["role"], c["url"], c["job_id"])
            results.append((c["name"], c["expect"], verdict))
        wrong = [r for r in results if r[1] != r[2]]
        correct = len(results) - len(wrong)
        print("\nDuplicate check on %d labelled cases: %d correct (%.0f%%)" % (len(results), correct, 100.0 * correct / len(results)))
        for name, expect, got in wrong:
            print("  MISMATCH: %s: expected %s, got %s" % (name, expect, got))
        self.assertEqual(wrong, [], "labelled cases that did not match")

    def test_duplicates_are_never_called_new(self):
        """Missing a real duplicate is the costly mistake: no DUPLICATE case may come back NEW."""
        self.seed_tracker()
        for c in PAIRS["cases"]:
            if c["expect"] == "DUPLICATE":
                verdict, _ = at.check(c["employer"], c["role"], c["url"], c["job_id"])
                self.assertNotEqual(verdict, "NEW", c["name"])


class Folders(Workspace):
    def test_folder_numbering_and_duplicate_refusal(self):
        code, out = self.run_cli("new-folder", "--name", "Acme Applied Scientist", "--employer", "Acme Ltd",
                                 "--role", "Applied Scientist", "--url", "https://acme.example/jobs/90001")
        self.assertEqual(code, 0, out)
        self.assertTrue((at.APPS / "01_Acme_Applied_Scientist").is_dir())
        (at.APPS / "01_Acme_Applied_Scientist" / "Job_Requirements.md").write_text(
            "# Acme Ltd - Applied Scientist\n\nURL: https://acme.example/jobs/90001\nJob ID: 90001\n", encoding="utf-8")
        code, out = self.run_cli("new-folder", "--name", "Acme_again", "--employer", "Acme", "--role", "Applied Scientist",
                                 "--url", "https://acme.example/jobs/90001?ref=board")
        self.assertEqual(code, 2, out)
        self.assertFalse(any(d.name.startswith("02_") for d in at.APPS.iterdir()))
        code, out = self.run_cli("new-folder", "--name", "Other_Employer", "--employer", "Zephyr Corp", "--role", "Data Analyst")
        self.assertEqual(code, 0, out)
        self.assertTrue((at.APPS / "02_Other_Employer").is_dir())

    def test_force_overrides_possible(self):
        self.run_cli("new-folder", "--name", "Fabrikam_SWE", "--employer", "Fabrikam Robotics", "--role", "Software Engineer")
        (at.APPS / "01_Fabrikam_SWE" / "Job_Requirements.md").write_text("# Fabrikam Robotics - Software Engineer\n", encoding="utf-8")
        code, _ = self.run_cli("new-folder", "--name", "Fabrikam_Robot_Vision", "--employer", "Fabrikam Robotics", "--role", "Software Engineer")
        self.assertEqual(code, 1)
        code, _ = self.run_cli("new-folder", "--name", "Fabrikam_Robot_Vision", "--employer", "Fabrikam Robotics",
                               "--role", "Software Engineer", "--force")
        self.assertEqual(code, 0)


class TrackerAdd(Workspace):
    def add(self, **kw):
        base = ["tracker-add", "--company", kw.get("company", "Zephyr Corp"), "--role", kw.get("role", "Data Analyst"),
                "--type", kw.get("type", "Industry")]
        for k in ("status", "fit", "priority", "deadline", "url", "mode"):
            if k in kw:
                base += ["--" + k, kw[k]]
        if kw.get("force"):
            base.append("--force")
        return self.run_cli(*base)

    def test_new_file_gets_id_1_then_2(self):
        code, out = self.add()
        self.assertEqual(code, 0, out)
        code, out = self.add(company="Quark Systems", role="ML Engineer")
        self.assertEqual(code, 0, out)
        rows = at.csv_rows()
        self.assertEqual([r["ID"] for r in rows], ["1", "2"])

    def test_existing_rows_unchanged_and_backup_made(self):
        self.seed_tracker()
        before = at.csv_rows()
        code, out = self.add(company="Zephyr Corp", role="Data Analyst", priority="High", fit="4")
        self.assertEqual(code, 0, out)
        after = at.csv_rows()
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(after[-1]["ID"], str(len(before) + 1))
        self.assertTrue(any(at.BACKUPS.glob("tracker_before_*.csv")))

    def test_duplicate_row_is_refused(self):
        self.seed_tracker()
        code, out = self.add(company="Northwind Analytics", role="Applied Scientist, Computer Vision")
        self.assertEqual(code, 2, out)
        self.assertEqual(len(at.csv_rows()), len(PAIRS["existing"]))

    def test_invalid_values_are_rejected_without_touching_the_file(self):
        self.seed_tracker()
        raw = at.TRACKER.read_bytes()
        code, _ = self.add(status="Hired!!")
        self.assertNotEqual(code, 0)
        code, _ = self.add(deadline="next week")
        self.assertNotEqual(code, 0)
        code, _ = self.add(fit="9")
        self.assertNotEqual(code, 0)
        self.assertEqual(at.TRACKER.read_bytes(), raw)

    def test_failed_verification_restores_the_file(self):
        self.seed_tracker()
        raw = at.TRACKER.read_bytes()
        original = at.write_csv

        def broken(path, rows):  # drops the last row, so verification must fail
            original(path, rows[:-1])
        at.write_csv = broken
        try:
            code, _ = self.add(company="Zephyr Corp", role="Data Analyst")
        finally:
            at.write_csv = original
        self.assertNotEqual(code, 0)
        self.assertEqual(at.TRACKER.read_bytes(), raw)

    def test_ids_are_permanent_after_a_row_is_removed(self):
        self.add()
        self.add(company="Quark Systems", role="ML Engineer")
        rows = at.csv_rows()
        at.write_csv(at.TRACKER, rows[1:])  # user deletes ID 1 by hand
        self.add(company="Initech", role="Platform Engineer")
        self.assertEqual([r["ID"] for r in at.csv_rows()], ["2", "3"])


class Init(Workspace):
    def test_init_copies_examples_once(self):
        for name in ("PROFILE.example.md", "EVIDENCE_BANK.example.md", "tracker.example.csv", "seekcrew.config.example.json"):
            (self.tmp / name).write_text("x", encoding="utf-8")
        at.CONFIG.unlink()
        code, out = self.run_cli("init")
        self.assertEqual(code, 0, out)
        for name in ("PROFILE.md", "EVIDENCE_BANK.md", "tracker.csv", "seekcrew.config.json"):
            self.assertTrue((self.tmp / name).exists(), name)
        (self.tmp / "PROFILE.md").write_text("mine", encoding="utf-8")
        self.run_cli("init")
        self.assertEqual((self.tmp / "PROFILE.md").read_text(encoding="utf-8"), "mine")
        for d in ("applications", "shortlists", "master"):
            self.assertTrue((self.tmp / d).is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
