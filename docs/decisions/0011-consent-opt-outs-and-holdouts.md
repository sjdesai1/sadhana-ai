# 0011 Consent, opt-outs, channels and holdout groups

Status: Accepted

## Context
- Outreach reaches patients by text, email and front desk calls; texting rules (TCPA, carrier rules) are strict
- People who reply STOP still have visits to confirm; offices want another way to reach them
- Every messaging tool claims results; few can show what outreach changed versus what would have happened anyway
- The optimizer needs to know how much outreach moves bookings; until now it assumed 30% (recall) and 15%
  (treatment)

## Decision
- Messaging is a shared engine with no dental knowledge: consent, opt-outs, channels, caps, templates, replies.
  The vertical pieces (what to follow up on, which codes are cosmetic) come from settings and the agents
- STOP (and STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, REVOKE, OPTOUT, Spanish ALTO, PARA, BAJA, and plain phrases
  like "stop texting me") stops texts at once for every purpose, with one confirmation text. START undoes it
- After a text STOP, email only for visit confirmations, never outreach. Email unsubscribe stops email. Both rules
  are settings, reviewed with counsel before launch
- Texts need text consent and a cell phone; email needs an address on file; no consent for either means a front
  desk call task, never a text
- Cosmetic follow-up needs marketing consent. A child's messages go to the guardian, on the guardian's consent
- Caps: one message per contact per day, two per week; nobody is picked again for the same thing within 30 days;
  sends only on send days and inside quiet hours
- Wording is care follow-up in plain language: no procedure names or codes in a text, no sales words, always the
  opt-out line. Language only picks the wording; English and Spanish are written, others fall back to English
  until reviewed translations exist
- A random holdout group (20% to start, fixed per patient) gets no outreach. Arms are assigned before consent and
  caps are checked, and results are compared by arm (intention to treat), so the lift is what an office would
  really get. lift = (outreach rate - holdout rate) / (1 - holdout rate)
- The optimizer uses the measured lift once a group's holdout has 100 or more people, and its assumption before

## Consequences
- Every message, reply and opt-out is a row: offices get an audit trail, and evals can check that nothing was
  sent after a STOP, without consent, over the caps, or to the holdout group
- The holdout costs some bookings; the share can come down once lift is measured well enough
- A new vertical reuses the engine with its own templates, purposes and settings
- Real sending (a texting provider, an email provider) plugs in behind the same interface; today's sender is
  simulated
