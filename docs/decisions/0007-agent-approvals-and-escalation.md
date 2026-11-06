# 0007 Agent approvals and escalation

Status: Accepted

## Context
- Agents will text patients, fill openings and follow up on treatment for real offices
- Some actions are safe to automate; others carry clinical, legal, billing or reputational risk
- A patient message left unanswered costs the office a booking and trust
- Idle agents do nothing until something wakes them

## Decision
- Agents act on their own for: appointment reminders and confirmations, rebooking cancellations and no-shows,
  filling openings from the waitlist, recall outreach, replies inside approved templates
- A human approves: any message outside an approved template, anything touching billing, claims or balances,
  treatment and cosmetic suggestions (to the dentist, never straight to the patient), editing or deleting PMS
  records, new campaigns or audiences
- Requests vs informs: front desk items are either "needs a reply" (a patient asked something) or "FYI"
  (confirmed, rescheduled), shown separately
- Doorbell: an inbound text, missed call or cancellation writes a job to the Postgres queue and wakes the right
  agent; nightly work runs in Dagster
- Watchdog: anything unanswered escalates on a timer: agent, then front desk task, then office manager, then
  dentist
- Approved message templates are versioned; only an approved version can be sent, and every send records it
- The PMS is the source of truth: an agent re-reads a slot right before booking it
- Each agent run gets one bounded task with a step limit and a stop condition, then hands off
- Each office has a monthly LLM budget; near the limit, agents switch to a cheaper model

## Consequences
- Every agent action is either on the automatic list or waits in an approval queue; nothing in between
- Template approval and send logs give offices a clean audit trail
- Escalation timers and budgets are per-office settings in the office config
- Evals must cover: no sends outside templates, no double booking, escalation fires on time
