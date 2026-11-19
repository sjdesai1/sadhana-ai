# Milestone 7: Outreach agent, messaging engine, replies and refill

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

Not yet: real sending (texting and email providers, behind the same interface), writing bookings back to the
PMS, language-model replies to questions (inside templates and the office's budget), the 90-day plan, and
overbooking.

## Part 2: replies, refill and the front desk queue

Code:
- `src/sadhana/messaging/intents.py`: what a reply means (confirm, reschedule, accept, choose, decline, question)
- `src/sadhana/agents/replies.py`: the reply agent, inside the approved templates
- `src/sadhana/agents/scheduling.py`: openings, booking and cancelling against the live calendar
- `src/sadhana/agents/refill.py`: a freed slot offered to the best backups at once
- `src/sadhana/agents/desk.py`: the front desk queue and escalation
- Tables: `desk_tasks`, `conversations`, `pending_bookings`, `refill_offers`; `messages.kind` (outreach, refill,
  reply) and `inbound_messages.intent`
- API: `POST /offices/{id}/cancellations` (the doorbell from the PMS), `GET /offices/{id}/desk`,
  `POST /offices/{id}/desk/{task_id}/resolve`, `POST /offices/{id}/desk/{task_id}/approve`; `/inbound` now runs
  the reply agent

Replies:
- "Yes" to a slot offer or refill books it if it is still free (re-checked first); otherwise "just taken" and two
  other times
- "Yes" to recall or treatment follow-up gets the next two openings at the patient's preferred time; "1" or "2"
  books
- "C" confirms. "R" or "can I do another day?" cancels the visit, offers the freed slot to backups right away,
  and gives the patient two new times
- "No" gets a polite close. Questions and anything else get a holding reply and a "needs reply" item for the desk
- Opt-out words win, so "CANCEL" stops texts; confirmations ask for R to reschedule. Nobody who opted out of a
  channel gets a reply on it
- Bookings and cancellations wait in `pending_bookings` to be written to the PMS (with the Open Dental adapter)

Refill: a cancelled visit (from the PMS, or a reschedule by text) is offered at once to the three best backups the
rules allow, ranked like the slot plan (chance to book x chance to show x margin with that provider). The first
yes gets it; later yeses hear it was taken and get other times. Outside send days or quiet hours, the desk gets
a "call the waitlist" item instead.

Front desk queue (decision 0007): needs reply, call, approval, and FYI (done by the agent, for the record).
Anything that needs a person moves up when its time runs out: front desk (1 day), office manager (1 day),
dentist (2 days). Cosmetic follow-up waits for the dentist's approval, and the rules are checked again before it
is sent.

Results (seed 42, 20 send days):

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Booked by text | about 90 | about 200 |
| Cancellations refilled | about 1 of 6 | about 5 of 20 |
| Questions to the desk | about 30 | about 40 |
| Cosmetic follow-ups approved by the dentist | about 45 | about 2 |
| Escalated items | 3 | 5 |

The simulated desk answers about 70% of open items a day; the rest escalate. Refills fill a minority of freed
slots because only a few backups get a same-day offer and most do not answer in time, as in real offices.

Tests: `tests/test_replies.py` (reply meanings; yes, pick and book; reschedule, refill and one winner; confirm,
question and escalation; no reply after STOP; cosmetic approval; endpoints) and the pilot checks in
`tests/test_outreach.py` (no double booking against the PMS or each other, one winner per refill, nobody booked
twice, no replies after STOP).
