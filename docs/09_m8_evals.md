# Milestone 8: The scorecard

Goal: grade the whole product, end to end, against the simulator's hidden truth, on offices it was never tuned
on and under bad conditions, with one pass or fail per check (decision 0012).

Run: `make evals` (about 9 minutes), or `make evals o=group_queens` for one run, `--seed 7` for another seed.
Output: `outputs/evals/scorecard.md` and `.json`, and a row per run in `sadhana_meta.eval_runs`.

Code: `src/sadhana/evals/scorecard.py`; settings and thresholds in `configs/evals.json`.

Runs:
- Demo offices: solo Manhattan, group Queens
- Hidden test offices, never tuned on: Bronx Medicaid, Park Slope family, Upper East Side boutique
- Scenarios: group Queens in a recession, group Queens with a new competitor, solo Manhattan with a payer cut,
  solo Manhattan with reminders that barely work (no-shows about double)

Each run: simulate, sync like a PMS, train and score the models, plan the next two weeks, run a 20-day outreach
pilot with replies, refills and the desk, backtest the 13-week forecast from Aug 2, then grade.

Results (seed 42): 8 of 9 runs pass every check; 18 of 18 measured outreach lifts have a 95% interval that
covers the hidden truth.

| Check | Range across the 9 runs | Target |
| --- | --- | --- |
| Missed-visit AUC | 0.61 to 0.70 | 0.58 or more |
| Gap to the best possible missed-visit model | 0.02 to 0.06 | 0.08 or less |
| Recall AUC | 0.72 to 0.79 | 0.65 or more |
| Recall scores spotting lapsed patients (AUC) | 0.59 to 0.71 | 0.60 or more |
| Treatment AUC | 0.59 to 0.71 | 0.55 or more |
| Calibration error | 0.01 to 0.12 | 0.06 (0.15 for treatment) |
| Plan against a first-come desk | 3.6x to 7.1x | 1.5x or more |
| Measured lift, standard errors from the truth | 0.04 to 1.3 | 3 or less |
| Opt-out rate | 0.5% to 1.9% | 3% or less |
| Hygiene forecast, weekly / quarter error | 4-8% / 0-6% | 12% / 8% |
| Doctor forecast, quarter error | 1% to 24% | 25% |
| Rules: double bookings, messages after STOP or without consent, over the caps, to the holdout, hidden truth in the product's tables, protected inputs | 0 everywhere | 0 |

What failed, and what the scorecard found:
- The Upper East Side boutique (a hidden test office) misses one check: its recall scores spot patients who have
  quietly lapsed at 0.59 against a target of 0.60. It is a small office with few lapsed patients; on seed 7 it
  passes. Not tuned (decision 0008); recorded here
- The slot plan ignored the agents' own bookings, so it could offer a time already booked by text. Fixed: the
  plan reads pending bookings and cancellations
- An at-risk visit with no procedure codes crashed the optimizer (Bronx). Fixed
- Two runs of the same simulation gave different results: model inputs were read in no fixed order (row order
  feeds the trees' sampling), agent ids were random, some ordered reads had ties, and measured lifts from the
  previous run carried over. Fixed; runs now repeat exactly, and a test checks it
- The payer cut scenario scores the same as the plain solo office. The cut lowers what Cigna really pays, but the
  economics engine takes margins from the configured fee schedules, not from what payers paid, so the product
  does not notice. Next: learn each payer's allowed amounts from paid claims
- With heavy no-shows, overbooking turns on (20 slots in two weeks against 0 to 2 elsewhere), as it should

Not in CI: the full scorecard takes about 9 minutes, so CI runs one hidden office end to end
(`tests/test_evals.py`), plus the grading, rendering, repeatability and the plan fix.
