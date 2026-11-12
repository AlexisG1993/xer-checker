"""Read Primavera P6 .xer exports with the Python standard library only.

An .xer file is tab-separated text:
    ERMHDR  <version> ...        file header
    %T      <TABLE>              start of a table
    %F      <field> <field> ...  column names for that table
    %R      <value> <value> ...  one data row
    %E                           end of file

The parser never guesses. Anything that breaks those rules, or that later
checks cannot trust (bad numbers, bad dates, links to missing activities),
is recorded as an Issue with its line number instead of being silently fixed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DATE_FORMAT = "%Y-%m-%d %H:%M"
REQUIRED_TABLES = ("PROJECT", "CALENDAR", "TASK", "TASKPRED")
DEFAULT_HOURS_PER_DAY = 8.0


@dataclass
class Issue:
    """One broken row or file-level problem. line == 0 means the whole file."""

    table: str
    line: int
    row_id: str
    problem: str


@dataclass
class XerFile:
    path: Path
    encoding: str
    version: str
    tables: dict[str, list[dict[str, str]]]
    issues: list[Issue] = field(default_factory=list)
    hours_per_day: dict[str, float] = field(default_factory=dict)

    def table(self, name: str) -> list[dict[str, str]]:
        return self.tables.get(name, [])


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

_LETTER_RUN = re.compile(r"[^\W\d_]+")


def detect_encoding(raw: bytes) -> str:
    """Pick utf-8, cp1252 (Western) or cp1256 (Arabic).

    P6 writes .xer files in the Windows code page of the machine that
    exported them, and the file does not say which one. Arabic text read as
    cp1252 turns into words made only of accented Latin letters ("ÈÏÇíÉ"),
    which almost never happens in real Western text, so that is the signal.
    """
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    sample = raw.decode("cp1252", errors="replace")
    non_ascii_words = [w for w in _LETTER_RUN.findall(sample) if not w.isascii()]
    if not non_ascii_words:
        return "cp1252"
    all_accented = sum(
        1 for w in non_ascii_words if len(w) >= 3 and not any(c.isascii() for c in w)
    )
    return "cp1256" if all_accented / len(non_ascii_words) > 0.5 else "cp1252"


# --------------------------------------------------------------------------
# File structure
# --------------------------------------------------------------------------


def read_xer(path: str | Path, encoding: str | None = None) -> XerFile:
    path = Path(path)
    raw = path.read_bytes()
    enc = encoding or detect_encoding(raw)
    text = raw.decode(enc, errors="replace")

    tables: dict[str, list[dict[str, str]]] = {}
    fields: dict[str, list[str]] = {}
    issues: list[Issue] = []
    current: str | None = None
    version = ""
    saw_end = False

    for line_no, line in enumerate(text.split("\n"), start=1):
        line = line.rstrip("\r")
        if not line:
            continue
        parts = line.split("\t")
        tag = parts[0]

        if tag == "ERMHDR":
            version = parts[1] if len(parts) > 1 else "unknown"
        elif tag == "%T":
            current = parts[1] if len(parts) > 1 and parts[1] else None
            if current is None:
                issues.append(Issue("", line_no, "", "Table marker (%T) without a table name"))
            else:
                tables.setdefault(current, [])
        elif tag == "%F":
            if current is None:
                issues.append(Issue("", line_no, "", "Field list (%F) before any table (%T)"))
            else:
                fields[current] = parts[1:]
        elif tag == "%R":
            if current is None or current not in fields:
                issues.append(Issue(current or "", line_no, "", "Data row (%R) before its field list (%F)"))
                continue
            values = parts[1:]
            expected = len(fields[current])
            if len(values) != expected:
                # A row whose columns don't line up can't be trusted at all:
                # flag it and leave it out of every check.
                issues.append(Issue(current, line_no, values[0] if values else "",
                                    f"Row has {len(values)} values but the table header has {expected}"))
                continue
            row = dict(zip(fields[current], values))
            row["_line"] = str(line_no)
            tables[current].append(row)
        elif tag == "%E":
            saw_end = True
        else:
            issues.append(Issue(current or "", line_no, "", f"Unrecognised line type '{tag[:20]}'"))

    if not version:
        issues.append(Issue("", 0, "", "No ERMHDR header: this may not be a P6 export"))
    if not saw_end:
        issues.append(Issue("", 0, "", "No %E end marker: the file may be cut off"))
    for name in REQUIRED_TABLES:
        if name not in tables:
            issues.append(Issue(name, 0, "", f"Required table {name} is missing"))

    xer = XerFile(path=path, encoding=enc, version=version, tables=tables, issues=issues)
    xer.hours_per_day = {c["clndr_id"]: calendar_hours_per_day(c) for c in xer.table("CALENDAR")}
    _validate_rows(xer)
    xer.issues.sort(key=lambda i: (i.line == 0, i.line))  # by line, file-level problems last
    return xer


# --------------------------------------------------------------------------
# Row-level validation
# --------------------------------------------------------------------------

_TASK_DATE_FIELDS = ("act_start_date", "act_end_date", "early_start_date", "early_end_date",
                     "late_start_date", "late_end_date", "target_start_date", "target_end_date")


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _is_date(value: str) -> bool:
    try:
        datetime.strptime(value, DATE_FORMAT)
        return True
    except ValueError:
        return False


def _validate_rows(xer: XerFile) -> None:
    add = xer.issues.append
    tasks = xer.table("TASK")
    task_ids: set[str] = set()

    for t in tasks:
        line, code = int(t["_line"]), t.get("task_code", "")
        tid = t.get("task_id", "")
        if not tid:
            add(Issue("TASK", line, code, "Activity has no task_id"))
        elif tid in task_ids:
            add(Issue("TASK", line, code, f"Duplicate task_id {tid}"))
        task_ids.add(tid)

        tf = t.get("total_float_hr_cnt", "")
        if tf and not _is_number(tf):
            add(Issue("TASK", line, code, f"Total float is not a number: '{tf}'"))
        if not tf and t.get("status_code") != "TK_Complete":
            add(Issue("TASK", line, code, "Open activity has no total float (schedule not calculated?)"))

        for f in _TASK_DATE_FIELDS:
            v = t.get(f, "")
            if v and not _is_date(v):
                add(Issue("TASK", line, code, f"{f} is not a valid date: '{v}'"))

        if t.get("clndr_id") and t["clndr_id"] not in xer.hours_per_day:
            add(Issue("TASK", line, code, f"Calendar {t['clndr_id']} is not in the CALENDAR table"))

    for r in xer.table("TASKPRED"):
        line, rid = int(r["_line"]), r.get("task_pred_id", "")
        for side, key in (("successor", "task_id"), ("predecessor", "pred_task_id")):
            if r.get(key) not in task_ids:
                add(Issue("TASKPRED", line, rid, f"Link points to a {side} ({r.get(key)}) that is not in this file"))
        lag = r.get("lag_hr_cnt", "")
        if lag and not _is_number(lag):
            add(Issue("TASKPRED", line, rid, f"Lag is not a number: '{lag}'"))


# --------------------------------------------------------------------------
# Calendars
# --------------------------------------------------------------------------

_DAY = re.compile(r"\(0\|\|([1-7])\(\)")
_INTERVAL = re.compile(r"s\|(\d{1,2}):(\d{2})\|f\|(\d{1,2}):(\d{2})")


def calendar_hours_per_day(cal: dict[str, str]) -> float:
    """Working hours in a normal day, used to turn float hours into days.

    Newer exports drop the day_hr_cnt column, so fall back to the weekly
    pattern stored in clndr_data, then to 8 hours.
    """
    value = cal.get("day_hr_cnt", "")
    if value and _is_number(value) and float(value) > 0:
        return float(value)

    data = cal.get("clndr_data", "")
    start = data.find("DaysOfWeek")
    if start >= 0:
        ends = [i for i in (data.find("Exceptions", start), data.find("VIEW", start)) if i >= 0]
        week = data[start:min(ends) if ends else len(data)]
        chunks = _DAY.split(week)  # [before, day, body, day, body, ...]
        daily = []
        for body in chunks[2::2]:
            hours = 0.0
            for sh, sm, eh, em in _INTERVAL.findall(body):
                begin = int(sh) + int(sm) / 60
                end = int(eh) + int(em) / 60 or 24.0  # 00:00 as an end time means midnight
                hours += max(end - begin, 0)
            if hours > 0:
                daily.append(hours)
        if daily:
            return max(set(daily), key=daily.count)  # the most common working day
    return DEFAULT_HOURS_PER_DAY
