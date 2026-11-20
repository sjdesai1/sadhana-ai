# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [SemVer](https://semver.org/);
each minor version is a finished milestone.

## [Unreleased]

### Added
- The scorecard (`make evals`): simulates the demo offices, the three hidden test offices, a recession, a
  competitor, a payer cut and an office with heavy no-shows; runs the whole product on each and grades it
  against hidden truth and the rules; `configs/evals.json`, `outputs/evals/scorecard.md`,
  `sadhana_meta.eval_runs`; decision record 0012

- Allowed amounts learned from paid claims (credibility-weighted against the schedule on file), used by the
  economics, the slot plan, booking and the 90-day plan; payers paying more than 3% under their schedule are
  flagged with the month it changed; `payer_rates`, `payer_schedule_check`, `/offices/{id}/payer-check`,
  `configs/economics.json`; scorecard check `economics.payer_flags_correct`

- The monthly report: recovered revenue counted only from the measured difference against the holdout (and
  only kept visits), refills less an assumed share the desk would have filled, the return on the fee, front desk
  hours saved, the schedule, outreach, insurance and the next 90 days; `make report`, `/offices/{id}/report`,
  `/offices/{id}/report.html`, `configs/reports.json`

- Simulator tuned to published benchmarks (no-shows, cancellations, case acceptance, retention, reminder effect,
  outreach response, opt-outs), with sources in `docs/12_benchmarks.md`
- The full plan to launch in `docs/STATUS.md` (20 items before demo mode, the before-launch checklist) and the
  expanded roadmap (v2 to v5, rules, cost to run)

- Outreach lift by segment (new, established, lapsed), stored per segment, shown in the monthly report ("Who
  responds") and checked against each segment's hidden truth in the scorecard
- The monthly report shows a likely range on recovered revenue, from the intervals on each measured lift
- Missed-visit features: recent misses, days since the last miss, advance cancellations

### Changed
- Missed-visit history is what was known the day before the visit, not at booking; that model uses shallower,
  more regularized trees; its AUC floor in the scorecard and tests moved from 0.58 to 0.55 (see
  docs/13_m10_segments.md)
- New nullable columns are also added to existing shared (sadhana_meta) tables
- The 13-week forecast averages the last 13 weeks instead of 8 (steadier for a single dentist's column)

### Fixed (found by the scorecard)
- Treatment model recalibration no longer flips its ranking on a small recent window (AUC 0.38 at the heavy
  no-show office); a flat or negative slope only shifts the level
- Times offered in Spanish read "a las", not "at"
- The slot plan now counts the agents' own bookings and cancellations, so it never offers a time already booked
  by text
- At-risk visits with no procedure codes no longer crash the optimizer
- Runs repeat exactly: model inputs are read in a fixed order (row order fed the trees' sampling), agent ids
  are no longer random, slot plan rows are numbered from 1 on every run, and every read that feeds a decision
  has a fixed order

## [0.6.0] - Milestones 6 and 7: optimizer and agents

### Added
- 90-day plan: weekly capacity, booked and expected hours per column, what recall outreach and treatment
  follow-up add at the measured lift, hours still open, the emptiest weeks and the insurance resetting Dec 31;
  backtested on three past quarters; `plan_90`, `/offices/{id}/plan-90`, Dagster `plan_90`
- Overbooking where it pays (chance of a miss x the extra patient's margin against the cost of both showing),
  at most one per provider per day, never for specialists; `overbook_suggestions`, listed in `/opportunities`
- Slot optimizer: open chair time for the next two weeks from the real schedule, candidates (overdue cleanings and
  diagnosed open treatment, cosmetic only with consent), valued as chance to book x chance to show x margin per
  provider, filled by rounds of an assignment solver with nobody used twice; `slot_plan` table, `make optimize`,
  Dagster `slot_plan`
- At-risk booked visits with a distinct backup patient for each slot; `at_risk_visits` table
- `/offices/{id}/opportunities`: the slot board
- `configs/optimizer.json`, with the outreach lifts marked as assumptions until holdouts measure them
- Messaging engine shared by every vertical: consent, opt-outs (STOP and its variants, START, HELP), channels
  (text, email, front desk call task), caps, quiet hours, guardians for children, wording in English and Spanish
- Outreach agent: confirmations for at-risk visits, slot offers, treatment follow-up and recall each send day, with
  a fixed holdout group; `outreach_targets`, `messages`, `inbound_messages`, `opt_outs`, `outreach_results`
- Lift measurement, outreach group vs holdout with a 95% interval; `sadhana_meta.lift_estimates`; the optimizer
  uses the measured lift once the holdout is big enough
- Simulated patients answer outreach from hidden traits: bookings, replies and opt-outs
- `make outreach`, Dagster `outreach`, `/offices/{id}/outreach`, `/offices/{id}/inbound`
- Decision record 0011: consent, opt-outs, channels and holdout groups
- `docs/strategy/roadmap.md`: product versions, pricing, verticals
- Reply agent: books from "Yes" and "1"/"2", confirms "C", reschedules "R" (freeing the slot), closes "No", and
  sends questions to the desk; reply meanings in `messaging/intents.py`; `conversations` and `pending_bookings`
- Real-time refill: a cancelled visit is offered to the three best backups at once, first yes wins;
  `refill_offers`; `/offices/{id}/cancellations`
- Front desk queue with needs reply, call, approval and FYI items, escalation from front desk to office manager
  to dentist, and dentist approval for cosmetic follow-up; `desk_tasks`; `/offices/{id}/desk` with resolve and
  approve
- Simulated patients reply, pick times, reschedule, ask questions and cancel ahead; a simulated desk works the
  queue

### Changed
- The office calendar moved to `sadhana.office_calendar`, shared by the product and the simulator, with `gaps()`
  for open stretches; the simulator re-exports it
- `economics.inputs_for()` builds economics inputs from an office config already in hand
- The models' patient frame also carries marketing consent and preferred time
- Testing stages renamed Stage 1 to 3, so v1 to v5 name product versions
- The optimizer reports which outreach lifts it used, measured or assumed
- New nullable columns are added to existing office tables on setup (a stopgap until migrations)
- The planner's inputs load through `optimizer.run.gather`, shared with the reply agent

## [0.5.0] - Milestone 5: nightly models

### Added
- Nightly models with XGBoost: missed visit (no-show or late cancel) for every booked visit, and recall self-book
  for every overdue patient; point-in-time features without language, zip, neighborhood or sex; time-split
  testing with AUC and calibration; scores per office and metrics per run; `make models`; Dagster `model_scores`
- Decision record 0010: model features and testing
- Treatment acceptance model for every open item, recalibrated on recent months as acceptance drifts
- Next-best item per patient (chance x fee x priority, diagnosed items only, cosmetic only with consent, with a
  reason for the desk); `next_best` table and `/offices/{id}/next-best`

### Changed
- Model probabilities pass through an optional Platt recalibration fit on the most recent months
- Pandas `day_of_week` instead of the deprecated `weekday`
- Simulator: a small group of chronic no-showers and a lead-time effect on no-shows, as in real offices; office
  no-show rates stay at benchmark

## [0.4.0] - Milestone 4: benefits engine

### Added
- Benefits engine: each patient's max and deductible left, coverage by class, next eligible dates from frequency
  limits, estimates for open treatment in claim order (deductible, coverage, annual max, secondary insurance),
  and year-end phasing that moves non-urgent work past this year's max to January
- Year-end list: active insured patients whose open treatment would use benefits expiring Dec 31
- Payer scorecard: volume, allowed share of fee, write-offs, denial rate, days to pay, insurance owed per payer
- API: `/patients/{patient_id}/benefits`, `/year-end`, `/payers` under each office
- Frequency rules moved to `benefits/rules.py`, shared by the product and the simulator

## [0.3.0] - Milestone 3: data layer and sync

### Added
- Data layer: canonical tables in one Postgres schema per office, with row-level security and an app role that
  cannot bypass it; office-scoped sessions; bookkeeping of offices and sync runs
- Sync: adapter interface, simulator adapter (observed data only, never hidden truth), full-refresh load with
  COPY in one transaction; `make sync` and `make db`
- CI runs a Postgres service; database tests are required there and skip locally without `make up`
- Decision record 0009: full-refresh sync, tables from code, migrations before real data
- Nightly Dagster job syncs every demo office into Postgres
- API on the database: office summary, overdue patients, unscheduled treatment, unused benefits; office-scoped
  queries in `data/queries.py`; 404 for offices with no data, 503 when the database is down

## [0.2.0] - Milestone 2: simulator

### Added
- Simulator, part 1: synthetic patient population per office (`make sim`). Households sharing ZIP, language,
  insurance and behavior; NYC neighborhood income tiers; insurance raked to each office's payer mix; consent,
  phone type and noisy health-form fields; hidden truth traits kept in a separate table
- Simulator, part 2: dental charts. Baby, mixed or permanent teeth by age; missing teeth, fillings, crowns, root
  canals and implants that build up with age; active decay, failing restorations, cracks and impacted wisdom teeth;
  gum disease stages; exams that miss some problems; treatment plans in catalog codes, status proposed
- Simulator, part 3a: three-year day-by-day hygiene history. New and former patients with join and leave dates;
  appointments in real provider slots around hours, lunch and holidays; recall with pre-booking, late returns and
  lapses; no-shows, late and early cancels; holiday, summer, weather and back-to-school seasonality; the office's
  existing reminders as the baseline; future bookings past today
- Simulator, part 3b: treatment history. Exams that present the plan and find new problems; accept, decline or
  wait decisions driven by priority, out-of-pocket cost, trust, financing, cosmetic interest and season; doctor,
  hygienist and visiting specialist routing by license and tier; referrals for work the office cannot do; root
  canal, implant, lab and aligner sequencing; emergencies; procedures ledger; one-call `build.simulate`

- Simulator, part 4: money. Ledger of every procedure with allowed amounts, insurance and patient shares and
  write-offs; claims with denials, resubmits and payment lag; secondary insurance; deductibles, annual max,
  frequency limits and waiting periods per calendar year; patient payments, financing, payment plans and
  collections; cancellation fees; balances with aging; unused benefits per patient
- Simulator, part 5: life events generated backward from today (job loss, retirement, new job, January plan
  switch) with billing by the payer on each date and stale-card denials; recession, payer fee cut and competitor
  scenarios; stress overrides per office or per run; three hidden test offices in `configs/offices_holdout/`
- Decision record 0008: hidden test offices are never tuned on
- `docs/STATUS.md`: where the build stands, what is next, latest benchmarks; updated in every PR
- Decision record 0007: what agents do alone vs with approval, doorbell and watchdog escalation, versioned message
  templates, per-office LLM budget

### Fixed
- Docker image build: copy source before installing, since dependencies are read from requirements.txt
- Dagster test uses resolve_job_def instead of the deprecated job lookup

## [0.1.0] - Milestone 0 and 1

### Added
- Local stack: one FastAPI app, Postgres, Dagster, Mailpit, optional Open Dental test database, all in Docker Compose
- CI on every PR and push to main: ruff, format check, tests with coverage, economics build, compose validation,
  pyright and pip-audit (report only), Docker image build
- CD on main: versioned image pushed to GitHub Container Registry
- Economics engine: NY scope of practice rules, 39-procedure catalog, NY payers and Medicaid fees, two demo
  offices (solo Manhattan, group Queens); eligibility, margins per procedure, payer and provider, best provider
  per procedure, payer write-offs, late-cancel fee value
- API: `/health`, `/offices`, `/offices/{id}/economics`
- Dagster asset `economics_tables` with a nightly schedule
