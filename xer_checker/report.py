"""Write the chart, the Markdown report and one findings CSV per file."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .checks import FLOAT_BUCKETS, HIGH_FLOAT_DAYS, Finding, open_activities  # noqa: E402
from .parser import XerFile  # noqa: E402

SERIES_COLORS = ("#2a78d6", "#eb6834")  # validated categorical slots 1 and 2
INK, INK_MUTED, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e4e3df"
ENCODING_NAMES = {"cp1252": "Windows Western (cp1252)", "cp1256": "Windows Arabic (cp1256)", "utf-8": "UTF-8"}


@dataclass
class FileResult:
    xer: XerFile
    open_count: int
    missing_links: list[Finding]
    negative_float: list[Finding]
    high_float: list[Finding]
    buckets: dict[str, int]
    seconds: float

    @property
    def name(self) -> str:
        return self.xer.path.name


# --------------------------------------------------------------------------
# Chart
# --------------------------------------------------------------------------


def float_chart(results: list[FileResult], out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    n = len(results)
    width = 0.8 / n
    x = range(len(FLOAT_BUCKETS))

    # Shade the two buckets the checks flag.
    for i, bucket in enumerate(FLOAT_BUCKETS):
        if bucket in ("Negative", "Over 44 days"):
            ax.axvspan(i - 0.48, i + 0.48, color="#f1eee8", zorder=0)
            ax.text(i, 1.0, "flagged", transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                    fontsize=9, color=INK_MUTED)

    for k, r in enumerate(results):
        total = max(r.open_count, 1)
        shares = [100 * r.buckets[b] / total for b in FLOAT_BUCKETS]
        offsets = [i - 0.4 + width * (k + 0.5) for i in x]
        bars = ax.bar(offsets, shares, width=width * 0.92, color=SERIES_COLORS[k % 2],
                      label=f"{r.name}  ({r.open_count:,} open activities)", zorder=2)
        for bar, bucket in zip(bars, FLOAT_BUCKETS):
            count = r.buckets[bucket]
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1, f"{count:,}",
                    ha="center", va="bottom", fontsize=8.5, color=INK_MUTED)

    ax.set_xticks(list(x), FLOAT_BUCKETS, fontsize=10, color=INK)
    ax.set_ylabel("Share of open activities (%)", fontsize=10, color=INK_MUTED)
    ax.set_ylim(0, 100)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=1)
    ax.tick_params(axis="y", colors=INK_MUTED, labelsize=9)
    ax.tick_params(axis="x", length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#bdbbb4")

    fig.suptitle("Total float of open activities", x=0.06, y=0.97, ha="left",
                 fontsize=14, fontweight="bold", color=INK)
    ax.set_title(f"P6 stored total float in working days. Bar labels are activity counts. "
                 f"High float = over {HIGH_FLOAT_DAYS} days (DCMA).",
                 loc="left", fontsize=9.5, color=INK_MUTED, pad=22)
    ax.legend(loc="upper right", frameon=False, fontsize=9.5, labelcolor=INK)
    fig.tight_layout()
    path = out / "float_profile.png"
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
# CSV + Markdown
# --------------------------------------------------------------------------


def findings_csv(r: FileResult, out: Path) -> Path:
    path = out / f"{r.xer.path.stem}_findings.csv"
    # utf-8-sig so Excel shows Arabic names correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["check", "line", "activity_or_row", "name", "detail", "float_days"])
        for i in r.xer.issues:
            w.writerow(["Broken row", i.line or "", i.row_id, i.table, i.problem, ""])
        for f in r.missing_links + r.negative_float + r.high_float:
            w.writerow([f.check, "", f.task_code, f.task_name, f.detail,
                        "" if f.float_days is None else f"{f.float_days:.1f}"])
    return path


def _longest_path(xer: XerFile) -> str:
    count = sum(t.get("driving_path_flag") == "Y" for t in open_activities(xer))
    return f"{count:,}" if count else "not flagged in this export"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _project(xer: XerFile) -> dict[str, str]:
    projects = xer.table("PROJECT")
    return projects[0] if projects else {}


def markdown_report(results: list[FileResult], chart: Path, out: Path, top: int = 10) -> Path:
    lines = ["# XER schedule check", ""]
    header = "| | " + " | ".join(f"`{r.name}`" for r in results) + " |"
    lines += [header, "|---" * (len(results) + 1) + "|"]

    def row(label, values):
        lines.append(f"| {label} | " + " | ".join(_cell(v) for v in values) + " |")

    row("Project", [f"{_project(r.xer).get('proj_short_name', '?')}"
                    + (f" (+{len(r.xer.table('PROJECT')) - 1} more)" if len(r.xer.table("PROJECT")) > 1 else "")
                    for r in results])
    row("P6 export version", [r.xer.version for r in results])
    row("Text encoding (detected)", [ENCODING_NAMES.get(r.xer.encoding, r.xer.encoding) for r in results])
    row("Data date", [_project(r.xer).get("last_recalc_date", "?")[:10] for r in results])
    row("Activities (open / total)", [f"{r.open_count:,} / {len(r.xer.table('TASK')):,}" for r in results])
    row("Relationships", [f"{len(r.xer.table('TASKPRED')):,}" for r in results])
    row("On P6 longest path (open)", [_longest_path(r.xer) for r in results])
    row("**Broken rows**", [f"**{len(r.xer.issues):,}**" for r in results])
    row("**Missing links**", [f"**{len(r.missing_links):,}**" for r in results])
    row("**Negative float**", [f"**{len(r.negative_float):,}**" for r in results])
    row(f"**High float (> {HIGH_FLOAT_DAYS} days)**", [f"**{len(r.high_float):,}**" for r in results])
    row("Time to read and check", [f"{r.seconds:.2f} s" for r in results])

    lines += ["", f"![Total float of open activities]({chart.name})", ""]

    for r in results:
        csv_name = f"{r.xer.path.stem}_findings.csv"
        lines += [f"## {r.name}", "", f"Every finding is in [`{csv_name}`]({csv_name}). "
                  f"Up to {top} per check are shown here.", ""]
        if r.xer.issues:
            lines += ["**Broken rows**", "", "| Line | Table | Row | Problem |", "|---|---|---|---|"]
            lines += [f"| {i.line or 'file'} | {i.table} | {_cell(i.row_id)} | {_cell(i.problem)} |"
                      for i in r.xer.issues[:top]]
            lines.append("")
        else:
            lines += ["**Broken rows:** none. Every row matches its table header, and every number, "
                      "date, calendar and link checks out.", ""]
        for title, items in (("Missing links", r.missing_links), ("Negative float", r.negative_float),
                             ("High float", r.high_float)):
            if not items:
                lines += [f"**{title}:** none.", ""]
                continue
            lines += [f"**{title}** ({len(items)})", "", "| Activity | Name | Finding |", "|---|---|---|"]
            lines += [f"| {_cell(f.task_code)} | {_cell(f.task_name)} | {_cell(f.detail)} |" for f in items[:top]]
            lines.append("")

    path = out / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
