"""The monthly report as one self-contained HTML page, the way an office manager would read it: the headline
first, then where the money came from, the schedule, the desk, insurance and the next 90 days."""

from __future__ import annotations

from html import escape
from typing import Any

from sadhana.reports.monthly import Report

Row = dict[str, Any]

CSS = """
:root{--ink:#1d2433;--muted:#5b6475;--line:#e3e6ec;--bg:#ffffff;--card:#f6f7f9;--accent:#0b6e4f;--warn:#a34b00}
@media (prefers-color-scheme: dark){:root{--ink:#e8ebf1;--muted:#a3abba;--line:#2c3240;--bg:#141821;--card:#1c212c;
--accent:#3ccf91;--warn:#ffb067}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",sans-serif}main{max-width:860px;margin:0 auto;padding:32px 16px 64px}h1{font-size:24px;margin:0 0 4px}
h2{font-size:17px;margin:32px 0 8px}.muted{color:var(--muted)}.hero{display:grid;grid-template-columns:repeat(3,1fr);
gap:12px;margin:20px 0}.tile{background:var(--card);border-radius:10px;padding:14px 16px}.tile b{display:block;
font-size:26px;color:var(--accent)}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}td.n,th.n{text-align:right}
.note{font-size:13px;color:var(--muted)}.flag{color:var(--warn)}.bar{height:10px;background:var(--accent);border-radius:3px}
@media (max-width:600px){.hero{grid-template-columns:1fr}}
"""


def _money(v: float) -> str:
    return f"${v:,.0f}"


def _pct(v: float) -> str:
    return f"{v:.0%}"


def render(report: Report) -> str:
    r = report.as_dict()
    start, end = r["period"]
    rows = "".join(
        f"<tr><td>{escape(x['source'])}{'' if x['measured'] else ' *'}</td><td class=n>{x['people_reached']:,}</td>"
        f"<td class=n>{x['extra_bookings']:,.0f}</td><td class=n>{_money(x['collected'])}</td>"
        f"<td class=n>{_money(x['margin'])}</td></tr>"
        for x in r["recovered"]
    )
    s, o, d, ins, nx = r["schedule"], r["outreach"], r["desk"], r["insurance"], r["next_90"]
    lm, mb = s["last_month"], s["month_before"]
    payers = (
        "".join(
            f"<li class=flag>{escape(p['payer'])} pays {_pct(p['paid_vs_schedule'])} of its fee schedule"
            f"{' since ' + p['changed_on'][:7] if p['changed_on'] else ''}: update the schedule or take it to the payer"
            "</li>"
            for p in ins["payers_under_schedule"]
        )
        or "<li>Every payer is paying its fee schedule.</li>"
    )
    weeks = "".join(f"<li>Week of {w['week']}: {w['unfilled_hours']:.0f} chair hours still open</li>"
                    for w in nx["emptiest_weeks"])  # fmt: skip
    names = {"sms": "text", "email": "email"}
    channels = ", ".join(f"{n:,} by {names.get(c, c)}" for c, n in o["by_channel"].items())
    notes = "".join(f"<li>{escape(n)}</li>" for n in r["notes"])
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Monthly report</title><style>{CSS}</style>
</head><body><main>
<h1>{escape(r["office_name"])}</h1>
<div class=muted>Monthly report, {start} to {end}</div>
<div class=hero>
<div class=tile><b>{_money(r["recovered_total"])}</b>recovered this month</div>
<div class=tile><b>{r["return_on_fee"]:.0f}x</b>the {_money(r["fee"])} fee</div>
<div class=tile><b>{d["hours_saved"]:.0f} h</b>of front desk time saved</div>
</div>
<p>Counted only where it can be shown: patients reached against a group left alone, so people who would have
booked anyway are not counted. Margin to the office after labor, lab and materials: {_money(r["margin_total"])}.</p>

<h2>Where it came from</h2>
<table><tr><th>Source</th><th class=n>Reached</th><th class=n>Extra visits</th><th class=n>Collected</th>
<th class=n>Margin</th></tr>{rows}</table>
<p class=note>* Not measured against a holdout yet; see the notes.</p>

<h2>The schedule in {escape(s["month"])}</h2>
<table><tr><th></th><th class=n>{escape(s["month"])}</th><th class=n>Month before</th></tr>
<tr><td>Chair time used</td><td class=n>{_pct(lm["chair_use"])}</td><td class=n>{_pct(mb["chair_use"])}</td></tr>
<tr><td>Visits due</td><td class=n>{lm["visits"]:,}</td><td class=n>{mb["visits"]:,}</td></tr>
<tr><td>Missed (no-show or late cancel)</td><td class=n>{_pct(lm["missed_rate"])}</td>
<td class=n>{_pct(mb["missed_rate"])}</td></tr></table>

<h2>Outreach and the front desk</h2>
<ul><li>{o["messages"]:,} messages ({channels}); {o["replies"]:,} replies; {o["booked_by_text"]:,} visits booked by
text</li><li>{o["opt_outs"]:,} opt-outs ({o["opt_out_rate"]:.1%} of messages), each honored at once</li>
<li>{d["replies_handled"]:,} replies answered by the agent; {d["tasks"]:,} items needed a person, {d["tasks_done"]:,}
done, {d["escalated"]:,} escalated, {d["still_open"]:,} still open</li>
<li>{o["held_out"]:,} patients left alone on purpose, to measure what outreach changes</li></ul>

<h2>Insurance</h2>
<ul>{payers}<li>{_money(ins["benefits_resetting_dec_31"])} of insurance resets on Dec 31 for
{ins["patients_with_benefits_left"]:,} patients with diagnosed work still open</li></ul>

<h2>The next 90 days</h2>
<p>The office's usual booking fills about {_pct(nx["utilization"])} of chair time; with outreach, about
{_pct(nx["utilization_with_outreach"])}, worth about {_money(nx["outreach_value"])} more in margin.
{nx["unfilled_hours"]:,.0f} chair hours would still be open.</p>
<ul>{weeks}</ul>

{f"<h2>Notes</h2><ul class=note>{notes}</ul>" if notes else ""}
</main></body></html>
"""
