"""Tests for the parser and the three checks. Run: python -m unittest"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xer_checker.checks import high_float, missing_links, negative_float
from xer_checker.parser import calendar_hours_per_day, detect_encoding, read_xer

FIXTURE = Path(__file__).parent / "fixtures" / "broken.xer"
REAL_FILES = Path(__file__).parent.parent / "data"

TASK_FIELDS = ["task_id", "proj_id", "clndr_id", "status_code", "task_type", "task_code", "task_name",
               "total_float_hr_cnt", "early_start_date", "driving_path_flag"]


def task(*values: str) -> str:
    return "%R\t" + "\t".join(values)


# A tiny schedule that breaks on purpose. One problem per row, labelled.
BROKEN_XER = "\n".join([
    "ERMHDR\t19.12\t2026-11-10\tProject\tadmin",
    "%T\tPROJECT", "%F\tproj_id\tproj_short_name\tlast_recalc_date", "%R\t1\tDEMO\t2026-11-01 00:00",
    "%T\tCALENDAR", "%F\tclndr_id\tclndr_name\tday_hr_cnt", "%R\t1\tStandard\t8",
    "%T\tTASK", "%F\t" + "\t".join(TASK_FIELDS),
    task("10", "1", "1", "TK_NotStart", "TT_Mile", "A10", "Start", "0", "2026-11-02 08:00", "Y"),
    task("20", "1", "1", "TK_NotStart", "TT_Task", "A20", "Linked, behind", "-40", "2026-11-02 08:00", "Y"),
    task("30", "1", "1", "TK_NotStart", "TT_Task", "A30", "No links at all", "16", "2026-11-03 08:00", "N"),
    task("40", "1", "1", "TK_NotStart", "TT_Task", "A40", "Lots of float", "400", "2026-11-04 08:00", "N"),
    task("50", "1", "1", "TK_NotStart", "TT_Task", "A50", "Bad float", "abc", "2026-11-04 08:00", "N"),
    task("60", "1", "1", "TK_NotStart", "TT_Task", "A60", "Bad date", "8", "next tuesday", "N"),
    task("70", "1", "9", "TK_NotStart", "TT_Task", "A70", "Unknown calendar", "8", "2026-11-04 08:00", "N"),
    task("80", "1", "1", "TK_NotStart", "TT_Task", "A80", "Too few columns"),
    task("90", "1", "1", "TK_NotStart", "TT_FinMile", "A90", "Finish", "0", "2026-12-01 08:00", "Y"),
    "%T\tTASKPRED", "%F\ttask_pred_id\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt",
    "%R\t1\t20\t10\tPR_FS\t0", "%R\t2\t40\t20\tPR_FS\t0", "%R\t3\t50\t40\tPR_FS\t0",
    "%R\t4\t60\t50\tPR_FS\t0", "%R\t5\t70\t60\tPR_FS\t0", "%R\t6\t90\t70\tPR_FS\t0",
    "%R\t7\t90\t999\tPR_FS\t0",
    # no %E end marker: the file looks cut off
]) + "\n"


class BrokenFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.xer = read_xer(FIXTURE)
        cls.problems = [i.problem for i in cls.xer.issues]

    def has(self, text: str) -> bool:
        return any(text in p for p in self.problems)

    def test_fixture_matches_source(self):
        self.assertEqual(FIXTURE.read_text(encoding="utf-8"), BROKEN_XER)

    def test_flags_every_broken_row(self):
        self.assertTrue(self.has("Row has 7 values but the table header has 10"))
        self.assertTrue(self.has("Total float is not a number: 'abc'"))
        self.assertTrue(self.has("early_start_date is not a valid date"))
        self.assertTrue(self.has("Calendar 9 is not in the CALENDAR table"))
        self.assertTrue(self.has("predecessor (999) that is not in this file"))
        self.assertTrue(self.has("No %E end marker"))
        self.assertEqual(len(self.xer.issues), 6)

    def test_broken_row_is_left_out(self):
        self.assertNotIn("A80", {t["task_code"] for t in self.xer.table("TASK")})

    def test_missing_links(self):
        found = {f.task_code: f.detail for f in missing_links(self.xer)}
        self.assertEqual(found, {"A30": "No predecessor and no successor"})

    def test_negative_float(self):
        found = negative_float(self.xer)
        self.assertEqual([(f.task_code, f.float_days) for f in found], [("A20", -5.0)])

    def test_high_float(self):
        found = high_float(self.xer)
        self.assertEqual([(f.task_code, f.float_days) for f in found], [("A40", 50.0)])


class EncodingTests(unittest.TestCase):
    def test_arabic_detected(self):
        raw = "%R\t1\tبداية المشروع\tأعمال الدهانات الخارجية\n".encode("cp1256")
        self.assertEqual(detect_encoding(raw), "cp1256")

    def test_western_accents_stay_western(self):
        raw = "%R\t1\tRéunion de chantier\tFaçade nord\tÉtude préliminaire\n".encode("cp1252")
        self.assertEqual(detect_encoding(raw), "cp1252")

    def test_utf8(self):
        self.assertEqual(detect_encoding("%R\t1\tCafé\n".encode("utf-8")), "utf-8")

    def test_arabic_file_reads_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ar.xer"
            path.write_bytes(BROKEN_XER.replace("\tStart\t", "\tبداية المشروع\t").encode("cp1256"))
            names = {t["task_code"]: t["task_name"] for t in read_xer(path).table("TASK")}
        self.assertEqual(names["A10"], "بداية المشروع")


class CalendarTests(unittest.TestCase):
    def test_hours_from_weekly_pattern(self):
        week = ("(0||CalendarData()((0||DaysOfWeek()((0||1()())"
                + "".join(f"(0||{d}()((0||0(s|08:00|f|12:00)())(0||1(s|13:00|f|17:00)())))" for d in range(2, 7))
                + "(0||7()()))(0||Exceptions()((0||0(d|40000)()))))")
        self.assertEqual(calendar_hours_per_day({"clndr_data": week}), 8.0)

    def test_day_hr_cnt_wins(self):
        self.assertEqual(calendar_hours_per_day({"day_hr_cnt": "10"}), 10.0)

    def test_default(self):
        self.assertEqual(calendar_hours_per_day({}), 8.0)


@unittest.skipUnless((REAL_FILES / "airport.xer").exists(), "sample files not present")
class RealFileTests(unittest.TestCase):
    """Counts cross-checked with a separate throwaway script during development."""

    def test_airport(self):
        x = read_xer(REAL_FILES / "airport.xer")
        self.assertEqual((len(x.table("TASK")), len(x.table("TASKPRED")), len(x.issues)), (66, 84, 0))
        self.assertEqual((len(missing_links(x)), len(negative_float(x)), len(high_float(x))), (0, 0, 10))

    def test_rehab(self):
        x = read_xer(REAL_FILES / "rehab-2.xer")
        self.assertEqual(x.encoding, "cp1256")
        self.assertEqual((len(x.table("TASK")), len(x.table("TASKPRED")), len(x.issues)), (6977, 10730, 0))
        self.assertEqual((len(missing_links(x)), len(negative_float(x)), len(high_float(x))), (10, 0, 3718))


if __name__ == "__main__":
    unittest.main()
