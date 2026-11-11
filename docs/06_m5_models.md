# Milestone 5: Nightly models

Goal: score every open appointment and every overdue patient each night, so outreach goes where it changes
the outcome.

Run (after `make sync`): `make models`. In Dagster, `model_scores` runs after `office_sync` in the nightly job.

Code: `src/sadhana/models/features.py` (point-in-time features), `train.py` (XGBoost, testing, scoring),
`run.py` (saves scores and metrics). Scores land in each office's `scores` table; test metrics in
`sadhana_meta.model_runs`.

| Model | Scores | Target | Used for |
| --- | --- | --- | --- |
| missed_visit | every booked future visit | no-show or late cancel | extra confirmation, waitlist and overbooking decisions |
| recall_self_book | every overdue patient with nothing booked | books a cleaning within 60 days on their own | who outreach should go to first: low scores will not come back unaided |

How it is built:
- Features only use what the office knew before the event: visit history, lead time, visit type, day, month,
  hour, contactability, payer type, age, gum maintenance
- Never used for scoring: language (only picks the message language), zip code, neighborhood, sex
  (decision 0010)
- XGBoost, trained on the past, early-stopped on the next two months, tested on the latest months it never saw
- Graded against the simulator's hidden truth in tests: the missed-visit model is compared with the best any
  model could do (ranking by each patient's hidden miss rate), and low recall scores have to pick out patients
  who have quietly stopped coming
- Tested on a hidden office too (Bronx, half Medicaid), never used for tuning

Results (tested on months the models never saw):

| | Solo Manhattan | Group Queens | Hidden: Bronx |
| --- | --- | --- | --- |
| Missed visit AUC | 0.61 | 0.62 | 0.61 |
| Best possible AUC, from hidden truth | about 0.65 | about 0.68 | |
| Missed visit calibration error | 0.04 | 0.02 | 0.02 |
| Recall self-book AUC | 0.73 | 0.74 | 0.72 |
| Recall calibration error | 0.02 | 0.01 | 0.02 |

Most no-shows are not predictable, even with perfect knowledge of each patient, so the missed-visit model is
judged against that ceiling rather than against 1.0. It recovers most of what is there.

Simulator change in this milestone: no-shows now come mostly from a small group of chronic no-showers, and grow
with how far ahead a visit was booked, as in real offices. Office no-show rates stay at benchmark.

Next in this milestone: treatment acceptance and the next-best item to raise with each patient. True uplift
(who books because of outreach, not anyway) needs randomized holdouts, which arrive with the agents.
