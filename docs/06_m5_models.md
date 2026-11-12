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
| treatment_accept | every open treatment item | gets booked within 90 days of diagnosis under the office's usual follow-up | which treatment conversations to have, and the next-best item per patient |

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

## Part 2: Treatment acceptance and next-best item

- `treatment_accept` features, all as of the diagnosis date: the item's fee, what the patient would pay after
  insurance (from the benefits engine), benefit class, priority, how many items and dollars were diagnosed that
  day, month, visit history, and how often the patient booked earlier work (counting only bookings made before)
- Acceptance drifts over time, so the model is tested on six months and recalibrated (Platt scaling) on the three
  months before them, which keeps its probabilities honest
- `next_best.py` picks one open item per patient: chance it gets booked x office fee x priority weight (urgent
  1.5, needed 1.0, elective 0.8). Only diagnosed items qualify; cosmetic items need marketing consent. Each pick
  carries a reason for the desk: urgent, already said yes but never booked, or insurance that expires Dec 31
- Saved nightly in each office's `next_best` table; `GET /offices/{id}/next-best?limit=50` lists them by value

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Treatment acceptance AUC | 0.59 | 0.71 |
| Calibration error | 0.12 (small test set) | 0.04 |
| Patients with a next-best item | 765 | 2,924 |
| Expected value of those picks | about $306k | about $1.0M |

The solo office has a tenth of the group's data, so its treatment model is weaker. Learning across offices
(pooled priors) is the fix and comes later.

What the models cannot see yet is how each patient responds to outreach: the history here only reflects the
office's own follow-up. True uplift (who books because they were contacted, not anyway) needs randomized
holdouts, which arrive with the agents.
