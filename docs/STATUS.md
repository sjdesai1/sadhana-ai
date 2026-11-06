# Status

The single place to pick up the build. Updated in every PR.

## Where things stand

- Milestone 0 (local stack, CI/CD) and 1 (economics engine): done, released as 0.1.0
- Milestone 2 (simulator): done, parts 1 to 5, released as 0.2.0
- Milestone 3 (data layer and sync): next
- Last updated with: release 0.2.0

## Next

1. The spec's build order, starting with milestone 3 (data layer and sync), then the benefits engine, nightly
   models, optimizer, agents, evals, reports, demo mode and pilot readiness
2. Once the product runs end to end: point the sync at Open Dental's demo database and fix what breaks
3. Code walkthrough of every milestone at the end

## Latest benchmark run (`make sim`, seed 42)

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| No-shows, last 12 months | 5.4% | 5.2% | about 5% with reminders |
| Late cancels | 4.3% | 4.4% | 4-6% |
| Hygiene chairs full | 84% | 84% | 80-90% |
| Doctor chairs full | 59% | 66% | 65-85%; the gap is what the product fills |
| New patients a month | 22 | 62 | 20-30 per dentist |
| Active patients overdue, nothing booked | 24% | 32% | a fifth to a third |
| Unscheduled treatment | about $1.0M | about $3.9M | $500k-$1M+ per dentist |
| Emergencies seen | about 7 a month | about 25 a month | several a week per dentist |
| Gross production, last 12 months | about $1.29M | about $3.27M | $0.8-1.5M per general dentist |
| Write-offs | 32% | 46% | PPO 25-40%; more with Medicaid |
| Collected of net production | 99% | 100% | 95-98%; collections lag production |
| Claims denied the first time | 8.6% | 8.6% | 5-10% |
| Unused benefits per active insured patient | about $1,110 | about $1,170 | most never reach their max |

Hidden test offices: see `docs/03_m2_simulator.md`, part 5.

## Open decisions

- Vertical-neutral naming (customer, resource, optional payer) before adding a second vertical
- Office configs' `active_patients` now means patients on file at the start; rename when convenient
- Treatment decisions ignore the annual max (the money step applies it afterward); feed remaining benefits into
  decisions when the benefits engine is built

## How work happens

- One PR per change, one step at a time: unzip, `make format`, `make check`, `make sim`, commit, push, PR, merge
- Files are the record: configs, decision records, this file, the changelog and the spec. Chat is only where work
  gets asked for
- A new session starts by reading this file, `CHANGELOG.md` and `docs/decisions/`
