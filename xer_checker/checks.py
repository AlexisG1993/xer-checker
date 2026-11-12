"""The three schedule checks.

All checks look at open activities only: not complete, and not
level-of-effort or WBS-summary activities (those are not driven by logic).
Float is P6's own stored total float (total_float_hr_cnt), converted from
hours to working days with each activity's calendar. The checker does not
re-run the schedule calculation, so its numbers match what P6 reported at
the last schedule run.
"""
from __future__ import annotations

from dataclasses import dataclass

from .parser import DEFAULT_HOURS_PER_DAY, XerFile

HIGH_FLOAT_DAYS = 44  # DCMA 14-point assessment threshold, in working days
SKIP_TYPES = {"TT_LOE", "TT_WBS"}
FLOAT_BUCKETS = ("Negative", "Zero", "Up to 10 days", "10 to 44 days", "Over 44 days")


@dataclass
class Finding:
    check: str
    task_code: str
    task_name: str
    detail: str
    float_days: float | None = None


def open_activities(xer: XerFile) -> list[dict[str, str]]:
    return [t for t in xer.table("TASK")
            if t.get("status_code") != "TK_Complete" and t.get("task_type") not in SKIP_TYPES]


def float_days(xer: XerFile, task: dict[str, str]) -> float | None:
    raw = task.get("total_float_hr_cnt", "")
    try:
        hours = float(raw)
    except ValueError:
        return None  # already reported as a broken row by the parser
    return hours / xer.hours_per_day.get(task.get("clndr_id", ""), DEFAULT_HOURS_PER_DAY)


def missing_links(xer: XerFile) -> list[Finding]:
    """Open activities with no predecessor or no successor.

    Start milestones are allowed to have no predecessor and finish milestones
    no successor: those are the schedule's legitimate open ends.
    """
    links = xer.table("TASKPRED")
    has_pred = {r["task_id"] for r in links}
    has_succ = {r["pred_task_id"] for r in links}
    found = []
    for t in open_activities(xer):
        missing = []
        if t["task_id"] not in has_pred and t.get("task_type") != "TT_Mile":
            missing.append("predecessor")
        if t["task_id"] not in has_succ and t.get("task_type") != "TT_FinMile":
            missing.append("successor")
        if missing:
            found.append(Finding("Missing links", t.get("task_code", ""), t.get("task_name", ""),
                                 "No " + " and no ".join(missing)))
    return found


def negative_float(xer: XerFile) -> list[Finding]:
    found = []
    for t in open_activities(xer):
        days = float_days(xer, t)
        if days is not None and days < 0:
            found.append(Finding("Negative float", t.get("task_code", ""), t.get("task_name", ""),
                                 f"{days:.1f} working days behind", days))
    return sorted(found, key=lambda f: f.float_days)


def high_float(xer: XerFile) -> list[Finding]:
    found = []
    for t in open_activities(xer):
        days = float_days(xer, t)
        if days is not None and days > HIGH_FLOAT_DAYS:
            found.append(Finding("High float", t.get("task_code", ""), t.get("task_name", ""),
                                 f"{days:.1f} working days of float", days))
    return sorted(found, key=lambda f: -f.float_days)


def float_buckets(xer: XerFile) -> dict[str, int]:
    counts = dict.fromkeys(FLOAT_BUCKETS, 0)
    for t in open_activities(xer):
        days = float_days(xer, t)
        if days is None:
            continue
        if days < 0:
            counts["Negative"] += 1
        elif days == 0:
            counts["Zero"] += 1
        elif days <= 10:
            counts["Up to 10 days"] += 1
        elif days <= HIGH_FLOAT_DAYS:
            counts["10 to 44 days"] += 1
        else:
            counts["Over 44 days"] += 1
    return counts
