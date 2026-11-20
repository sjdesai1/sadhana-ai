# Milestone 9: The monthly report

Goal: one page an office manager reads each month: what Sadhana recovered, what it cost, how the schedule did,
what the front desk was spared, insurance to act on, and the next 90 days.

Run (after `make outreach`): `make report`. Output: `outputs/reports/<office>/report.html` and `report.json`.
API: `GET /offices/{id}/report` (JSON) and `GET /offices/{id}/report.html` (the page).

Code: `src/sadhana/reports/monthly.py` (the numbers), `html.py` (the page). Settings: `configs/reports.json`.

Recovered is counted only where it can be shown:
- Recall, treatment follow-up and confirmations: the measured difference between the outreach group and the
  holdout group, times the people reached, times the value of a visit. People who would have booked anyway are
  not counted, and with no holdout measurement nothing is claimed
- Only kept visits count: extra bookings are multiplied by the office's show rate over the last year
- Refills: freed slots the agent filled, less a share the desk might have filled anyway (25%, an assumption until
  measured, and marked as not measured on the page)
- Value is what the office collects: a hygiene visit at its average collected over the last year, treatment at the
  expected collected for that code across the office's payers (using what payers really allow). Margin after
  labor, lab and materials is shown next to it

The page: the headline (recovered, the return on the $500 fee, front desk hours saved), where it came from, the
schedule last month against the month before (chair time used, visits, missed visits), outreach and the desk
(messages, replies, bookings by text, opt-outs, items for a person, escalations, the holdout), insurance (payers
paying under their schedule, insurance resetting Dec 31), the next 90 days, and notes on anything not yet
measured well.

Results (seed 42, the 20-day simulated pilot):

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Recovered (collected) | about $55k | about $72k |
| Margin | about $41k | about $35k |
| Return on the $500 fee | about 110x | about 144x |
| Front desk hours saved | about 27 | about 48 |

These are high because the first month works through the whole backlog of overdue patients and open treatment;
later months will be smaller, and the page says so. The figures also lean on simulated behavior: a pilot office
is what settles them.

Tests: `tests/test_reports.py`.
