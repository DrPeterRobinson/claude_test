"""Summaries, CSV export and invoices."""

from __future__ import annotations

import csv
import html
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

from .models import Entry, Project
from .timeutil import fmt_date, fmt_duration, fmt_hours, fmt_time

GROUPINGS = ("project", "client", "user", "day")


def clipped_seconds(entry: Entry, start: float | None = None, end: float | None = None) -> float:
    s = entry.start if start is None else max(entry.start, start)
    e = entry.end if end is None else min(entry.end, end)
    return max(0.0, e - s)


def round_up(seconds: float, increment_minutes: int) -> float:
    """Round up to the billing increment (e.g. 6 minutes = 0.1 hour)."""
    if increment_minutes <= 0:
        return seconds
    step = increment_minutes * 60
    return math.ceil(seconds / step - 1e-9) * step


@dataclass
class Row:
    key: str
    label: str
    seconds: float = 0.0
    amount: float = 0.0


def summarise(entries: list[Entry], projects: dict[str, Project], start: float | None, end: float | None,
              group_by: str = "project") -> list[Row]:
    if group_by not in GROUPINGS:
        raise ValueError(f"unknown grouping: {group_by}")
    rows: dict[str, Row] = {}
    for e in entries:
        if e.deleted:
            continue
        seconds = clipped_seconds(e, start, end)
        if seconds <= 0:
            continue
        project = projects.get(e.project_id)
        if group_by == "project":
            key, label = e.project_id, project.name if project else "(deleted project)"
        elif group_by == "client":
            key = label = (project.client if project and project.client else "(no client)")
        elif group_by == "user":
            key = label = e.user
        else:
            key = label = fmt_date(e.start if start is None else max(e.start, start))
        row = rows.setdefault(key, Row(key, label))
        row.seconds += seconds
        row.amount += seconds / 3600 * (project.rate if project else 0.0)
    return sorted(rows.values(), key=lambda r: r.label.lower())


def write_entries_csv(path: str, entries: list[Entry], projects: dict[str, Project], device_label=str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "start", "end", "hours", "project", "client", "user", "device", "source", "note"])
        for e in entries:
            if e.deleted:
                continue
            p = projects.get(e.project_id)
            writer.writerow([fmt_date(e.start), fmt_time(e.start), fmt_time(e.end), fmt_hours(e.duration),
                             p.name if p else "", p.client if p else "", e.user, device_label(e.device),
                             e.source, e.note])


@dataclass
class InvoiceLine:
    project: str
    seconds: float
    billed_seconds: float
    rate: float
    amount: float


@dataclass
class Invoice:
    number: str
    client: str
    period_start: float
    period_end: float
    currency: str = "£"
    from_name: str = ""
    issued: float = field(default_factory=time.time)
    lines: list[InvoiceLine] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(line.amount for line in self.lines), 2)


def build_invoice(entries: list[Entry], projects: dict[str, Project], client: str, start: float, end: float,
                  number: str, rounding_minutes: int = 6, currency: str = "£", from_name: str = "") -> Invoice:
    seconds: dict[str, float] = defaultdict(float)
    for e in entries:
        p = projects.get(e.project_id)
        if e.deleted or p is None or p.client != client:
            continue
        seconds[p.id] += clipped_seconds(e, start, end)
    invoice = Invoice(number, client, start, end, currency, from_name)
    for pid in sorted(seconds, key=lambda i: projects[i].name.lower()):
        if seconds[pid] <= 0:
            continue
        p = projects[pid]
        billed = round_up(seconds[pid], rounding_minutes)
        invoice.lines.append(InvoiceLine(p.name, seconds[pid], billed, p.rate, round(billed / 3600 * p.rate, 2)))
    return invoice


def invoice_html(inv: Invoice) -> str:
    esc = html.escape
    cur = esc(inv.currency)
    rows = "\n".join(
        f"<tr><td>{esc(l.project)}</td><td class=n>{fmt_hours(l.billed_seconds)}</td>"
        f"<td class=n>{cur}{l.rate:,.2f}</td><td class=n>{cur}{l.amount:,.2f}</td></tr>"
        for l in inv.lines
    ) or "<tr><td colspan=4>No billable time in this period.</td></tr>"
    last_day = fmt_date(inv.period_end - 1)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Invoice {esc(inv.number)}</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; color: #222; max-width: 760px; margin: 40px auto; padding: 0 16px; }}
 h1 {{ margin-bottom: 0; }} .muted {{ color: #666; }}
 table {{ width: 100%; border-collapse: collapse; margin-top: 24px; }}
 th, td {{ padding: 8px; border-bottom: 1px solid #ddd; text-align: left; }}
 .n {{ text-align: right; font-variant-numeric: tabular-nums; }}
 tfoot td {{ font-weight: bold; border-top: 2px solid #222; }}
 @media print {{ body {{ margin: 0; }} }}
</style></head><body>
<h1>Invoice</h1>
<p class=muted>{esc(inv.from_name)}</p>
<p><strong>Invoice no:</strong> {esc(inv.number)}<br>
<strong>Date:</strong> {fmt_date(inv.issued)}<br>
<strong>Bill to:</strong> {esc(inv.client)}<br>
<strong>Period:</strong> {fmt_date(inv.period_start)} to {last_day}</p>
<table>
<thead><tr><th>Project</th><th class=n>Hours</th><th class=n>Rate</th><th class=n>Amount</th></tr></thead>
<tbody>
{rows}
</tbody>
<tfoot><tr><td colspan=3>Total</td><td class=n>{cur}{inv.total:,.2f}</td></tr></tfoot>
</table>
<p class=muted>Hours are rounded up to the billing increment. Recorded time: {
    fmt_duration(sum(l.seconds for l in inv.lines))} (h:mm).</p>
</body></html>
"""
