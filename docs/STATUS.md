# Status

The single place to pick up the build. Updated in every PR.

## Roadmap

- v1: the whole product, built and demoed on the two demo offices
- v2: the same product tested on more and different simulations (hidden test offices, scenarios, stress settings)
- v3: real data, starting with Open Dental's demo database, then a pilot office

## Where things stand

- Milestone 0 (local stack, CI/CD) and 1 (economics engine): done, released as 0.1.0
- Milestone 2 (simulator): done, parts 1 to 5, released as 0.2.0
- Milestone 3 (data layer and sync): done, released as 0.3.0
- Milestone 4 (benefits engine and payer scorecard): done, released as 0.4.0
- Milestone 5 (nightly models): done (missed visit, recall self-book, treatment acceptance, next-best item)
- Last updated with: milestone 5 part 2, treatment acceptance and next-best item

## Next

1. Release 0.5.0 (models milestone)
2. Then the spec's build order: optimizer, agents, evals, reports, demo mode, pilot readiness (v1)
3. v2 and v3 as in the roadmap; code walkthrough of every milestone at the end

## Latest benchmark run (`make sim`, seed 42)

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| No-shows, last 12 months | 5.5% | 6.3% | about 5% with reminders |
| Late cancels | 3.8% | 4.1% | 4-6% |
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
- Small offices: pool model training across offices (pooled priors) so a solo office borrows strength
- Vertical order: dental, specialists, med spas, salons and barbers, vets; restaurants and ticketing parked
  (platform lock-in, low value per slot)
- After v1: AI receptionist (real-time voice) as a paid add-on; opt-out and channel policy (text, email, calls,
  postcards) added to decision 0007 with the agents
- Treatment decisions ignore the annual max (the money step applies it afterward); feed remaining benefits into
  decisions when the benefits engine is built

## Running locally

- `make sim` builds the demo offices; `make up` starts Postgres and the stack (run it again after code changes
  so the app image rebuilds); `make sync` loads them; `make db` opens a SQL prompt
- `make models` trains, tests and scores the models for the demo offices
- API docs at http://localhost:8000/docs: office summary, overdue, unscheduled treatment, unused benefits,
  year-end list, patient benefits and estimates, payer scorecard, next-best item per patient
- Database tests skip when Postgres is not running; CI always runs them

## How work happens

- One PR per change, one step at a time: unzip, `make format`, `make check`, `make sim`, commit, push, PR, merge
- Files are the record: configs, decision records, this file, the changelog and the spec. Chat is only where work
  gets asked for
- A new session starts by reading this file, `CHANGELOG.md` and `docs/decisions/`
