# Milestone 6: Slot optimizer, 90-day plan and overbooking

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

Real-time refill and measured outreach lifts came with the agents (milestone 7); the 90-day plan and
overbooking are part 2 below.

## Part 2: the 90-day plan and overbooking

Run: `make optimize` now also prints the next 13 weeks and any overbooking. In Dagster, `plan_90` runs after
`outreach`, so it uses that night's measured lift.

Code: `src/sadhana/optimizer/horizon.py` (forecast, outreach fill, backtest) and `Planner.overbook` in
`plan.py`. Tables: `plan_90`, `overbook_suggestions`. API: `GET /offices/{id}/plan-90`; `/opportunities` now
lists overbooking too.

The 90-day plan, week by week and per column:
1. Capacity: chair hours from the office calendar (hours, lunch, who works which day, holidays)
2. Booked: what is on the books now, plus the agents' bookings and cancellations not yet in the PMS
3. Expected with the office's usual booking: its share of capacity filled over the last 13 weeks, never below
   what is booked times its recent show rate
4. Outreach on top: recall (overdue now, spread over 6 weeks; coming due, in the week it is due) and treatment
   follow-up (spread over 8 weeks), each at the measured lift (or the assumption until measured), capped by the
   open hours each week, with the rest spilling to the next week
5. Value: hours x the office's margin per chair hour in that column over the last year; treatment at each item's
   own margin
6. Also: the emptiest weeks, and the insurance that resets Dec 31 for patients with open treatment

Backtest: the baseline made on three past dates (Feb 1, May 3, Aug 2 2026) from the schedule as it stood then,
next to what the offices really did over the following 13 weeks:

| | Hygiene weekly error | Hygiene quarter total | Doctor quarter total |
| --- | --- | --- | --- |
| Solo Manhattan | 4-6% | within 4% | within 18% |
| Group Queens | 4-6% | within 3% | within 6% |

Specialist weeks are too lumpy to forecast week by week (a few long surgeries); the plan shows them, but the
error is large. An earlier try that scaled the booked hours up by past booking curves was much worse (25-75%
weekly error), because how far ahead visits get booked shifts from year to year.

Results (seed 42, next 13 weeks from 2026-11-02):

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Chair hours | about 1,270 | about 3,480 |
| Filled with usual booking | about 54% (about $150k margin) | about 49% (about $176k margin) |
| With outreach | about 72-74% (+$60-75k) | about 62-67% (+$75-135k) |
| Emptiest weeks | early January | January |
| Insurance resetting Dec 31 | about $85k, 278 patients | about $325k, 821 patients |

The outreach range moves with the measured treatment lift, which is still noisy at these holdout sizes.

Overbooking: a second patient goes into a booked slot only where
`p_missed x chance the extra shows x their margin > (1 - p_missed) x chance the extra shows x cost of both
showing` (about $60 for hygiene, $120 for the doctor: overtime and a patient kept waiting). At most one per
provider per day, never in the specialist column, never the same extra patient twice. With these offices' miss
rates (most visits under 25%), it rarely pays: about one slot at the solo office and none at the group office in
the next two weeks. It is there for offices with more no-shows.

Tests (`tests/test_horizon.py`): the backtest bounds above on all three dates for both offices; the plan adds up
(expected under capacity, outreach under the open hours, unfilled what is left); agent bookings count as booked;
overbooking happens when it pays and not when it does not, within the limits; the endpoint.
