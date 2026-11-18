# Milestone 6, part 1: Slot optimizer

Goal: turn the nightly scores into a schedule. For the next two weeks, find every open stretch of chair time,
decide who is most worth offering it to, and flag booked visits likely to be missed so the slot is not lost.

Run (after `make models`): `make optimize`. In Dagster, `slot_plan` runs after `model_scores` in the nightly job.

Code: `src/sadhana/office_calendar.py` (the calendar, now shared by the product and the simulator, with
`gaps()` for open stretches), `src/sadhana/optimizer/plan.py` (candidates, values, assignment, at-risk list),
`run.py` (reads the office, saves the plan). Settings: `configs/optimizer.json`. Results land in each office's
`slot_plan` and `at_risk_visits` tables; the board is `GET /offices/{id}/opportunities?days=14`.

How it works:
1. Open time: the office calendar (hours, lunch, holidays, who works which day, turnover) with every booked
   visit placed on it. Every open stretch of 20 minutes or more, from two days out to two weeks out, per provider
2. Candidates, only people with nothing booked:
   - overdue patients for a cleaning (adult, child or gum maintenance, from their chart)
   - diagnosed, open treatment items. Cosmetic items only with marketing consent. A crown or post waits for the
     root canal or implant on the same tooth
3. Value of offering a stretch to a candidate:
   `chance they book if offered x chance they show x margin on that work with that provider`
   - chance they book: the model score plus an assumed outreach lift (30% of the rest for recall, 15% for
     treatment). These lifts are assumptions until randomized holdouts measure them in the agents milestone
   - chance they show: one minus the patient's smoothed miss rate
   - margin: the economics engine's net margin for that code, payer and provider, so doctor time goes to
     doctor-worthy work and a slower provider's longer chair time is priced in
   - rules: the provider has to be allowed to do it (scope of practice, certs, specialist scope, equipment), the
     visit has to fit the stretch at that provider's speed, and an offer outside the patient's preferred time is
     discounted 15%. Urgent work counts 1.5x for ranking, elective 0.8x
4. Fill plan: rounds of an assignment solver (Hungarian method, `scipy`). Each round gives every open stretch its
   best remaining patient with nobody used twice; leftover minutes stay open for the next round. Specialist and
   doctor time is planned before hygiene
5. At-risk visits: booked visits with a missed-visit score of 15% or more, ranked by expected margin lost, each
   with a different backup patient who fits the same slot. Action for the desk: confirm by phone, keep the
   backup ready

Results (seed 42, as of 2026-11-01, next two weeks):

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Open chair time | 44 stretches, 56 hours | 134 stretches, 190 hours |
| Offers in the plan | 40 (33 doctor, 7 hygiene) | 135 (104 doctor, 24 hygiene, 7 specialist) |
| Expected margin from the plan | $15.6k | $25.5k |
| At-risk booked visits | 5 | 30 |

Against a desk that calls the same candidates in list order and gives each slot to the first one who fits,
the plan's expected margin is about 3.9x higher at the group office ($25.5k vs $6.6k). Hygiene offers are few
because hygiene chairs are 84% booked and most gaps are shorter than a cleaning; most open time is doctor time.

Tests (`tests/test_optimizer.py`): no provider or patient is double-booked against the plan or the real
schedule; everything stays inside hours, lunch, the two-week horizon and the two-day lead time; every offer is
allowed for that provider, diagnosed and still open, the patient's own, and cosmetic only with consent; the plan
beats the first-come desk by at least 1.5x; at-risk visits are real bookings with distinct backups; the
endpoint returns the plan and filters by days.

Not yet (part 2, with the agents): the 90-day plan, real-time refill when someone cancels, overbooking on
high-risk slots, and measuring the outreach lifts with holdout groups instead of assuming them.
