# Status

The single place to pick up the build. Updated in every PR.

## Where things stand

- Milestone 0 (local stack, CI/CD) and 1 (economics engine): done, released as 0.1.0
- Milestone 2 (simulator): parts 1, 2, 3a and 3b done; part 4 (money) next
- Last updated with: docs PR adding this file and decision record 0007

## Next

1. Simulator part 4, money: claims, allowed amounts, benefits used and remaining, deductibles, patient portions,
   balances, year-end resets, cancellation fees (none for Medicaid)
2. Simulator part 5: life events (job loss, retirement, coverage changes, moves), market scenarios, calibration of
   the full run
3. Then the spec's build order: sync and adapters, benefits engine, nightly models, optimizer, agents, evals,
   reports, demo mode, pilot readiness

## Latest benchmark run (`make sim`, seed 42)

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| No-shows, last 12 months | 5.6% | 5.7% | about 5% with reminders |
| Late cancels | 4.7% | 4.2% | 4-6% |
| Hygiene chairs full | 83% | 85% | 80-90% |
| Doctor chairs full | 67% | 63% | 65-85% |
| New patients a month | 22 | 62 | 20-30 per dentist |
| Active patients overdue, nothing booked | 24% | 31% | a fifth to a third |
| Unscheduled treatment | about $1.0M | about $3.9M | $500k-$1M+ per dentist |
| Emergencies seen | about 7 a month | about 25 a month | several a week per dentist |

## Open decisions

- Vertical-neutral naming (customer, resource, optional payer) before adding a second vertical
- Office configs' `active_patients` now means patients on file at the start; rename when convenient

## How work happens

- One PR per change, one step at a time: unzip, `make format`, `make check`, `make sim`, commit, push, PR, merge
- Files are the record: configs, decision records, this file, the changelog and the spec. Chat is only where work
  gets asked for
- A new session starts by reading this file, `CHANGELOG.md` and `docs/decisions/`
