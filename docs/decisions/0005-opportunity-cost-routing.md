# 0005 Dentist time carries an hourly value

Status: Accepted

## Context
- The owner has no cash cost per procedure, so a cash-margin ranking sends every task to the owner
- Real offices give routine work to the cheapest qualified person and keep the owner on big cases

## Decision
- Each dentist can carry `hourly_value`, their production per hour on other work
- Ranking uses economic margin (cash margin minus that value); reported margin stays cash only

## Consequences
- Juniors get fillings and exams, specialists get their procedures, the owner keeps implants, veneers and aligners
- Values are set per office at onboarding from real production per hour
