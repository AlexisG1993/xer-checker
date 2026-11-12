"""Check Primavera P6 .xer files for broken rows, missing links and float problems.

Usage:
    python check_xer.py data/*.xer
    python check_xer.py my_schedule.xer --out reports --encoding cp1256
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from xer_checker.checks import float_buckets, high_float, missing_links, negative_float, open_activities
from xer_checker.parser import read_xer
from xer_checker.report import FileResult, findings_csv, float_chart, markdown_report


def check_file(path: Path, encoding: str | None) -> FileResult:
    started = time.perf_counter()
    xer = read_xer(path, encoding)
    result = FileResult(
        xer=xer,
        open_count=len(open_activities(xer)),
        missing_links=missing_links(xer),
        negative_float=negative_float(xer),
        high_float=high_float(xer),
        buckets=float_buckets(xer),
        seconds=0.0,
    )
    result.seconds = time.perf_counter() - started
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path, help=".xer files to check")
    parser.add_argument("--out", type=Path, default=Path("reports"), help="output folder (default: reports)")
    parser.add_argument("--encoding", help="force a text encoding, e.g. cp1252 or cp1256 (default: detect)")
    args = parser.parse_args(argv)

    missing = [f for f in args.files if not f.is_file()]
    if missing:
        print("Not found: " + ", ".join(map(str, missing)), file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    results = [check_file(f, args.encoding) for f in args.files]

    for r in results:
        findings_csv(r, args.out)
        print(f"{r.name}: {len(r.xer.table('TASK')):,} activities ({r.open_count:,} open), "
              f"{len(r.xer.table('TASKPRED')):,} links, encoding {r.xer.encoding} | "
              f"broken rows {len(r.xer.issues)}, missing links {len(r.missing_links)}, "
              f"negative float {len(r.negative_float)}, high float {len(r.high_float)} | "
              f"{r.seconds:.2f}s")

    chart = float_chart(results, args.out)
    report = markdown_report(results, chart, args.out)
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
