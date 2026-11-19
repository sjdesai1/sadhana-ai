# Milestone 7, part 1: Outreach agent and messaging engine

Goal: act on the plan. Each send day, decide who hears from the office, about what and on which channel, follow
every consent and opt-out rule, and measure what outreach changes with a holdout group.

Run (after `make optimize`): `make outreach` runs a 20-send-day simulated pilot for each demo office, then
measures the lift. Run `make optimize` again afterwards and it uses the measured lift. In Dagster, `outreach`
runs after `slot_plan` (one day per night).

Code:
- `src/sadhana/messaging/`: the shared engine. `policy.py` (consent, opt-outs, channels, caps, guardians,
  opt-out words), `templates.py` (wording), `inbound.py` (replies, STOP, START, HELP)
- `src/sadhana/agents/outreach.py`: who to contact each day, holdout assignment, sending
- `src/sadhana/agents/lift.py`: outreach group vs holdout, with a 95% interval
- `src/sadhana/simulator/responses.py`: simulated patients answering from hidden traits
- Settings: `configs/messaging.json` (product), `configs/simulator/responses.json` (hidden)
- Tables: `outreach_targets`, `messages`, `inbound_messages`, `opt_outs`, `outreach_results`, and
  `sadhana_meta.lift_estimates`
- API: `GET /offices/{id}/outreach`, `POST /offices/{id}/inbound` (the texting provider's webhook)

Each send day, in order:
1. Confirmations for booked visits at risk of being missed (first day)
2. The slot plan's offers (first day, while the slots are open)
3. Treatment follow-up, by expected value, up to 40 a day
4. Overdue cleanings, least likely to come back on their own first, up to 60 a day

For each pick: holdout or outreach (fixed per patient), then the rules in decision 0011: consent, opt-outs,
cosmetic consent, guardian, caps, send days and quiet hours. The channel is text, else email, else a front desk
call task.

The simulator plays the patients: chances of booking with and without outreach come from hidden traits (recall
compliance, trust, price sensitivity, responsiveness), texts can draw a STOP (more likely after more messages),
and replies go through the product's own inbound handler.

Results (seed 42, 20 send days from 2026-11-02):

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Messages | about 920 (85% text, 15% email) | about 1,630 (85% text, 15% email) |
| Front desk call tasks | 19 | 79 |
| Opt-outs | 15 | 19 |
| Recall lift (assumed 30%) | about 16-21%, interval about 5-30%; holdout too small to use yet | about 23-26%, interval about 17-32% |
| Treatment lift (assumed 15%) | about 12-14%, interval about 5-20% | about 4-8%, interval about -2 to 13% |

The hidden truth is about 20% for recall at both offices and 7-9% for treatment; every measured interval covers
it. The optimizer's guesses were too high, which is why measuring matters.

Tests: `tests/test_messaging.py` (opt-out words, consent, caps, guardians, wording) and `tests/test_outreach.py`
(no message over the caps, after a STOP, without consent, or to the holdout group; children reached through
guardians; measured lift covers the hidden truth; the optimizer switches to the measured lift; endpoints).

Not yet: real sending (texting and email providers, behind the same interface), the front desk queue and
approvals (decision 0007), conversational replies (reschedule, questions), the 90-day plan, real-time refill when
someone cancels, and overbooking.
