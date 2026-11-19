# 0012 The scorecard

Status: Accepted

## Context
- Each milestone had its own tests, on the two demo offices
- Nothing graded the whole product end to end, on offices it was never tuned on, or under bad conditions
- A score that changes between runs of the same simulation cannot be trusted or compared

## Decision
- One scorecard (`make evals`, settings in `configs/evals.json`) runs the whole product on every office and
  scenario: the two demo offices, the three hidden test offices, a recession, a competitor, a payer cut, and an
  office whose reminders barely work
- Each run is graded against hidden truth and the rules, with a pass or fail per check: data (no hidden truth in
  the product's tables), models (AUC, calibration, gap to the best possible, lapsed patients, no protected
  inputs), optimizer (against a first-come desk, no double booking), outreach (lift within 3 standard errors of
  the truth, no rule broken, opt-out rate), replies and refill (no double booking, one winner), forecast
  (backtested on a past quarter)
- A 95% interval misses the truth 1 time in 20 by design, so each run's lift is held to 3 standard errors, and
  the share of intervals that cover the truth is reported across the scorecard
- Hidden test offices are graded, never tuned on (decision 0008). A failure there is reported, not fixed by
  changing settings until it passes
- Runs repeat exactly: each run starts from a fresh simulation with no leftover agent activity or measured lift,
  and every read that feeds a model or a decision is ordered
- Every run is saved (`outputs/evals/scorecard.md` and `.json`, and `sadhana_meta.eval_runs`)

## Consequences
- Changes are judged by the whole scorecard, not one office
- The scorecard takes about 9 minutes, so it runs on demand and before a release, not in CI; CI runs one hidden
  office end to end
