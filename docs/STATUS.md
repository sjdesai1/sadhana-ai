# Status

The single place to pick up the build. Updated in every PR.

## Roadmap

Testing stages, all inside product v1:
- Stage 1: the whole product, built and demoed on the two demo offices
- Stage 2: the same product tested on more and different simulations (hidden test offices, scenarios, stress)
- Stage 3: real data, starting with Open Dental's demo database, then a pilot office

Product versions (v1 launch, then v2 to v5), pricing and verticals: `docs/strategy/roadmap.md`

## Where things stand

- Milestone 0 (local stack, CI/CD) and 1 (economics engine): done, released as 0.1.0
- Milestone 2 (simulator): done, parts 1 to 5, released as 0.2.0
- Milestone 3 (data layer and sync): done, released as 0.3.0
- Milestone 4 (benefits engine and payer scorecard): done, released as 0.4.0
- Milestone 5 (nightly models): done, released as 0.5.0 (missed visit, recall self-book, treatment acceptance,
  next-best item)
- Milestone 6 (optimizer): done (slot plan for the next two weeks, at-risk visits, opportunities board, 90-day
  plan backtested on past quarters, overbooking where it pays)
- Milestone 7 (agents): part 1 done (messaging engine, outreach agent, holdout groups, measured lift); part 2
  done (reply agent, booking by text, real-time refill, front desk queue with approvals and escalation)
- Release 0.6.0 (optimizer and agents): tagged
- Milestone 8 (evals): done (the scorecard across 9 offices and scenarios, graded against hidden truth)
- Payer rates learned from paid claims, with payers paying under their schedule flagged
- Milestone 9 (reports): done (the monthly office report and the return on the fee)
- Simulator tuned to published benchmarks (docs/12_benchmarks.md)
- Last updated with: simulator benchmarks, and the full plan to launch

## Next

Stage 1, before demo mode (product v1). Each item adds its own scorecard checks:
1. Simulator tuned to published benchmarks: done
2. Outreach lift by segment: new, established and lapsed patients, each against the holdout, in the report
3. Churn and lifetime value: active patients likely to leave, ranked by what they are worth
4. Uplift model: contact the patients outreach actually moves, from the holdout data
5. Market report: census moves and income, daytime workers, CDC dental visit rates, dental shortage areas, new
   housing, nearby dentists and new openings, transit, insurance mix; free public sources (paid ones are v4)
6. Year-end insurance push (Oct to Dec): the covered checkup for patients with benefits left, a "benefits left"
   note to the dentist on the day's schedule, opt-in cosmetic only with marketing consent, paid out of pocket and
   approved by the dentist
7. January restart: patients who maxed out or deferred work, when new benefits start
8. Family scheduling: offer back-to-back times to the rest of the household
9. Earlier-appointment waitlist: patients reply WAIT to hear about earlier openings; refill draws on it
10. Provider scorecards and the fee schedule check
11. Daily huddle sheet and front desk alerts: at-risk visits, balances to collect at check-in, insurance to
    re-check, patients who left without their next visit booked
12. Weekly owner digest and metrics: production pace, new patient retention, aging claims, pre-booking rate,
    perio upkeep, recall lists for postcards
13. Cosmetic interest model: only patients with marketing consent; results to the dentist
14. Same-day treatment: a hygiene patient with diagnosed work and a doctor gap now
15. Insurance-aware booking: split work across benefit years when it saves the patient money
16. Emergency and new-patient slot holds, released the day before
17. Reminders by risk: more for likely no-shows, fewer for reliable patients
18. Doctor exam routing during hygiene visits
19. Reason codes: every suggestion shows its top reasons in plain words
20. Closure rebooking: a storm closure rebooks everyone affected in one go

Then demo mode, then pilot readiness, which closes stage 1. Model work found by the scorecard: better
missed-visit features (two hidden offices below target at realistic no-show rates).

Before launch, after the build (one at a time):
- Conversations with dentists and office managers in the territory
- Practice software access: Open Dental first; Dentrix and Eaglesoft partner programs, or an integration hub
- Texting registration (10DLC), started weeks before the pilot
- Cyber and errors-and-omissions insurance, a breach plan; SOC 2 later for dental groups
- Trademark and domain for Sadhana AI
- Support setup and monitoring (sync failures, model drift)
- A sales partner
- Cost meter and caps per office (target about $150 a month to run)

Stages 2 and 3 as in the roadmap; code walkthrough of every milestone at the end.

## Latest benchmark run (`make sim`, seed 42; sources in docs/12_benchmarks.md)

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| No-shows, last 12 months | about 5% | about 5% | 3% DSO average (Catalyst Index 2026); solo a little higher |
| Late cancels | 3.6% | 3.8% | 5% cancellations (Catalyst Index 2026) |
| Case acceptance | about 59% | about 51% | 45% (Catalyst 2026) to 61% (Levin 2016) |
| Returning within 18 months | about 65% | about 60% | 58% groups, 70% solo (Catalyst 2026) |
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
- Vertical order and pricing: `docs/strategy/roadmap.md`. Reservation-based restaurants and ticketing stay parked
  (platform lock-in); POS-based restaurants, bars and retail come after the appointment verticals
- Messaging after a text STOP: email only for visit confirmations (decision 0011); review with counsel before
  launch. Postcards as another channel, later
- Reviewed translations beyond English and Spanish before the first office with many speakers of another language
- Treatment decisions ignore the annual max (the money step applies it afterward); feed remaining benefits into
  decisions when the benefits engine is built

## Running locally

- `make sim` builds the demo offices; `make up` starts Postgres and the stack (run it again after code changes
  so the app image rebuilds); `make sync` loads them; `make db` opens a SQL prompt
- `make models` trains, tests and scores the models for the demo offices; `make optimize` plans the next two
  weeks and the next 13; `make evals` runs the scorecard (about 9 minutes); `make report` writes the monthly report; `make outreach` runs a simulated outreach pilot and measures the lift
- API docs at http://localhost:8000/docs: office summary, overdue, unscheduled treatment, unused benefits,
  year-end list, patient benefits and estimates, payer scorecard, next-best item per patient, slot board,
  outreach summary, inbound replies, cancellations, front desk queue, 90-day plan, payer check, monthly report
- Database tests skip when Postgres is not running; CI always runs them

## How work happens

- One PR per change, one step at a time: unzip, `make format`, `make check`, `make sim`, commit, push, PR, merge
- Files are the record: configs, decision records, this file, the changelog and the spec. Chat is only where work
  gets asked for
- A new session starts by reading this file, `CHANGELOG.md` and `docs/decisions/`
