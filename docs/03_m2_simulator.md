# Milestone 2: Simulator

Goal: a synthetic practice realistic enough to build, test and demo every module before a real office connects.

Built in small PRs:

| Part | What it adds | Status |
| --- | --- | --- |
| 1 Population | Patients, households, ZIP and income tier, language, insurance, consent, hidden traits | Done |
| 2 Dental charts | Teeth by age, existing work, active problems, gum disease, treatment plans | Done |
| 3a Clock and hygiene history | Day-by-day clock: hygiene schedule, recall, no-shows, cancellations, seasonality, new and former patients | Done |
| 3b Treatment history | Doctor and specialist schedule, plan items scheduled / completed / declined, new findings, emergencies | Done |
| 4 Money | Claims, benefits used, patient portions, balances, year-end resets | Done |
| 5 Life events and calibration | Layoffs, retirement, coverage changes, market scenarios; hidden test offices and stress tests | Done |

## Part 1: Population

Run: `make sim` (or `make sim o=solo_manhattan seed=7`). Output: `outputs/<office>/sim/patients.csv` and `truth.csv`.

How it works:
1. Households: size, members and ages drawn from the office's area (`configs/simulator/areas_NY.json`)
2. Each household gets one ZIP, income tier and language
3. Insurance per household: income tier and age tilt the odds (low income toward Medicaid, 65+ toward cash),
   then weights are raked so the overall mix matches the office's payer mix
4. Each patient gets observed fields (what a PMS records) and hidden truth traits (what drives behavior)

| Observed (patients.csv) | Hidden (truth.csv) |
| --- | --- |
| Age, sex, ZIP, language, role in household | Income tier |
| Insurance, subscriber, secondary insurance | Recall compliance, no-show and late-cancel rates |
| Phone type, email, SMS and marketing consent | Price sensitivity, cosmetic interest, financing |
| Preferred time | Anxiety, tech comfort, text responsiveness, reply speed |
| Health form: smoker, diabetes, blood thinner, pregnant (10% missing, 3% wrong) | Caries and perio risk, grinding, sleep apnea risk |

Rules that keep it realistic:
- Family members share ZIP, insurance and part of their recall habits
- Children have a guardian, no phone, and are booked through the parent
- Texting only goes to patients with a cell phone and consent
- Language picks the message language only; it never feeds a model score

Tests: `tests/test_population.py` checks reproducibility, counts, payer mix, Medicaid rules, households, consent,
and that behavior follows real patterns (income and price sensitivity, age and texting, age and perio risk).

## Part 2: Dental charts

Run: `make sim` now also writes `teeth.csv`, `conditions.csv`, `perio.csv` and `treatment_plan.csv`.

| File | One row per | Truth or observed |
| --- | --- | --- |
| teeth.csv | tooth: present, why missing, filling / crown / implant, age of the work, root canal | Observed |
| conditions.csv | problem on a tooth: decay (early, moderate, deep), failing restoration, crack, impacted, missing | Truth, with a `diagnosed` flag for what the exam found |
| perio.csv | patient: true gum stage, charted stage, bleeding, grinding wear, on maintenance | Both |
| treatment_plan.csv | proposed procedure: tooth or quadrant, CDT code, reason, priority | Observed |

How it works:
- Teeth by age: baby teeth under 6, mixed 6-11, permanent from 12; wisdom teeth unerupted, removed, or impacted
- Missing teeth rise with age, lower income and poor recall; molars are lost first; some gaps already have implants
- Fillings and crowns build up with years of exposure and caries risk; old work fails at realistic rates
- Gum stage follows each patient's hidden perio risk; some perio patients were already treated and are on maintenance
- Exams find about 85% of decay and 60% of cracks; the chart is off by one gum stage 20% of the time
- Each diagnosed problem becomes procedures: deep decay = root canal + buildup + crown; moderate decay under a big
  filling = crown; perio stage 2+ = scaling and root planing per quadrant; grinding = night guard
- Elective items only when they make sense: implants for one gap, whitening only with marketing consent and real
  interest, aligners for crowding with cosmetic interest

Checks against real-world benchmarks: adults with untreated decay about 20% (CDC: about one in four), periodontitis in
adults 30+ about 40-50% (CDC: about 42%), missing teeth rising each decade.

The proposed plan is everything diagnosed and not yet done. Part 3 plays out time on it: some items get scheduled and
completed, some are declined, and the rest is the unscheduled treatment the product chases.

## Part 3a: Clock and hygiene history

Run: `make sim` now also writes `appointments.csv`, `patient_history.csv` (observed) and `appointment_truth.csv`,
`flow_truth.csv` (hidden). Tunable assumptions live in `configs/simulator/clock.json`.

The clock runs one day at a time for three years, ending the day before SIM_TODAY (2026-11-01). Bookings made before
today that fall after it stay on the books as the future schedule.

| File | One row per | Truth or observed |
| --- | --- | --- |
| appointments.csv | appointment: provider, date, start, end, kind, codes, booked on, status, rebook of | Observed |
| patient_history.csv | patient: first and last visit, next appointment, visit and no-show counts, active / inactive / archived | Observed |
| appointment_truth.csv | appointment: why it did not happen (patient, weather, holiday week, summer Friday, office closed, left) | Truth |
| flow_truth.csv | patient: joined, left, why they left, whether the office was told, when they quietly stopped booking | Truth |

How it works:
- Patient flow: about a third of today's patients joined during the window, with a January and September bump and a
  December dip; former patients (about a third more) left for a move, another dentist, an insurance change, a bad
  experience or death. The office is told only some of the time, so many still look active or simply no-show
- Real slots: each hygienist's working days, office hours, lunch and turnover; closed on holidays; patients try for
  their preferred time first (early, midday, evening, Saturday, after school) and kids come any time in summer
- What the office already does: reminder texts, booking the next cleaning at checkout for about half of patients,
  calling no-shows to rebook. Sadhana is measured against this baseline
- Recall: 6 months, or 3 months for treated gum disease. Patients who don't pre-book come back late or never, based
  on hidden recall compliance. When the first opening is weeks away, some say "call me" and drift off
- Visits end as completed, no-show, late cancel (inside 24 hours), or early cancel that frees the slot for others
- Seasonality: holiday weeks and summer Fridays bring more no-shows; bad-weather days more no-shows and late cancels;
  one storm closure each winter; adults push cleanings out of summer and December; kids pile into August;
  insured patients pull January cleanings into December to use benefits; new patients wait until after the holidays

Checks against real-world benchmarks (seed 42):

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| No-shows, last 12 months | 5.7% | 5.5% | about 5% with reminder texts |
| Late cancels | 4.0% | 3.9% | 4-6% |
| Hygiene chairs full | 83% | 85% | 80-90% |
| New patients a month | 23 | 63 (3 dentists) | 20-30 per dentist |
| Active patients overdue with nothing booked | 21% | 26% | a fifth to a third |
| Patients on file still active (seen in 18 months) | about 70% | about 57% | 55-75% |

Inactive patients (seen once, then nothing for 18+ months) are the reactivation pool; overdue active patients are
the recall gap. Both are worked by the product, and both are graded against the hidden truth.

## Part 3b: Treatment history

Same clock, now with the doctor and specialist columns. `make sim` also writes `procedures.csv` (every completed
procedure, the production ledger) and rewrites `treatment_plan.csv` and `conditions.csv` with what happened.
Code: `schedule.py` (calendar), `treatment.py` (treatment), `history.py` (the clock), `build.py` (one call
that builds a whole office).

| File | What changed |
| --- | --- |
| treatment_plan.csv | each item has a plan id, date diagnosed, status (proposed, accepted, scheduled, completed, declined, referred), status date, appointment, times presented |
| conditions.csv | date found and date treated; problems found at later exams are added |
| procedures.csv | one row per completed procedure: date, provider, code, tooth, plan item |
| appointments.csv | doctor, specialist, deep cleaning, follow-up and emergency visits alongside hygiene |

How it works:
- Exams: the chart's diagnosed problems are presented at the first exam in the window. Later exams find problems
  missed before, new decay, old fillings breaking down and gum disease starting
- Decisions per problem group (one tooth, all gum quadrants, a night guard, whitening): accepted, declined, or left
  for later. Patients weigh priority, what they pay after insurance (PPO coverage, Medicaid covered codes, cash
  discount), trust, financing, cosmetic interest and the season: insured patients say yes more in the fall to use
  benefits, less in January when deductibles reset. Each time an item is presented again, the odds drop
- Routing: deep cleanings with a hygienist who holds the anesthesia cert, root canals and gum surgery with the
  visiting specialist when there is one, everything else with a dentist allowed to do it (junior associates do not
  do molar root canals, implants or impactions). Work the office cannot do is referred out (the solo office has no
  cone beam CT, so implants are referred)
- Sequencing: build-up and crown after the root canal, implant crown at least 3 months after the implant, seat
  visits after the lab, aligner check-ins every 6 weeks. Waits survive rebooking
- Missed treatment visits drop the work back to accepted, not scheduled. When the first opening is a month out,
  some patients say "I'll call" and never do
- Emergencies: untreated deep decay, infections and cracks start hurting, and some patients break a tooth or lose a
  filling. They get a doctor slot within 3 days or go elsewhere. The tooth's treatment is presented with urgency,
  and some lapsed patients book a cleaning again
- Deep cleanings move patients to 3-month perio maintenance

Checks against real-world benchmarks (seed 42):

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| Doctor chairs full, counting exam time | 67% | 63% | 65-85%; open doctor time is part of what the product fills |
| Urgent work done vs needed vs elective | highest, middle, lowest | same | case acceptance falls with priority and price |
| Unscheduled treatment (accepted or proposed, not done) | about $1.0M, 26% of diagnosed | about $3.9M, 39% | most offices hold $500k-$1M+ per dentist |
| Emergencies seen | about 7 a month | about 25 a month | several a week per dentist |

## Part 4: Money

Every completed procedure, missed visit and payment, as an office's billing system would hold them. Code:
`money.py`; tunables in the `billing` block of `configs/simulator/clock.json`.

| File | One row per |
| --- | --- |
| ledger.csv | procedure or charge: fee, allowed, primary and secondary paid, patient portion, write-off, why (frequency limit, waiting period, not covered, annual max reached, denied); plus financing fees, collections and cancellation fees |
| claims.csv | claim per payer per visit: submitted, paid, expected vs paid, denial reason, paid after resubmit or written off, pending on SIM_TODAY |
| payments.csv | payment: insurance, patient, financing company, payment plan installment, cancellation fee |
| benefits.csv | insured patient per year and payer: annual max, used, remaining, deductible met |
| balances.csv | guarantor on SIM_TODAY: balance aged 0-30, 31-60, 61-90, 90+ days, insurance still owed, on a payment plan |

How it works:
- Allowed amounts from the economics engine: PPO fee schedules, the Medicaid fee schedule, cash fee less the
  discount, elective cosmetic work paid by the patient
- Benefits per calendar year: deductible on basic and major work, coverage by class, annual max, frequency limits
  from the catalog (two cleanings a year, one crown per tooth in five years), 12-month waiting periods for major
  work on some plans; secondary insurance pays up to the patient's share
- Claims: commercial payers pay in about 10-35 days, Medicaid in 20-60; about 6% of commercial and 10% of Medicaid
  claims are denied the first time; most are paid after a resubmit, the rest written off
- Parents and policyholders pay for their family. Patient portions are paid at the desk, later from a statement,
  through a financing company (office pays a fee), on a 6-month plan, or sent to collections, driven by hidden
  price sensitivity, income and financing propensity
- Cancellation fees per office policy, never for Medicaid, waived for weather, storm closures and patients who left;
  only some are collected

Checks against real-world benchmarks (last 12 months, seed 42):

| Metric | Solo Manhattan | Group Queens | Benchmark |
| --- | --- | --- | --- |
| Gross production | about $1.35M | about $3.2M | $0.8-1.5M per general dentist with hygiene |
| Write-offs | 32% | 45% | PPO 25-40%; more with Medicaid |
| Collected of net production | 98% | 98% | 95-98% |
| Claims denied the first time | 7% | 7% | 5-10% |
| Days for a claim to pay (median) | 23 | 27 | 2-5 weeks |
| Unused benefits this year per active insured patient | about $1,100 | about $1,190 | most patients never reach their max |

## Part 5: Life events, scenarios, stress settings and hidden test offices

Code: `events.py`; settings in the `life_events` and `scenarios` blocks of `configs/simulator/clock.json`. New
hidden file: `life_events.csv` (household, date, event, insurance before and after).

Life events run backward from today, so every office's payer mix still matches:
- Cash today, working age: some had employer PPO until a job loss; they book later and drift away more often
- Cash today, 65 to 72: some had PPO until retirement
- Medicaid today: some had PPO before a job loss
- PPO today: some switched carriers in January, some were uninsured before a new job
- Each plan gets its own annual max and deductible, so a mid-year switch starts fresh
- The first claim after a change often goes to the old card and comes back "coverage terminated"

Scenarios, off unless an office config lists them under `simulation.scenarios`:

| Scenario | Effect |
| --- | --- |
| recession | 12 months of fewer yeses on needed and elective work, twice the job losses, fewer new patients |
| payer_cut | one payer cuts its fee schedule by 10% from a date |
| competitor | a practice opens nearby: more patients leave after it, fewer new ones come |

Stress settings: any `clock.json` value can be overridden per office under `simulation.overrides`, or per run with
`simulate(..., overrides=...)`; for example `no_show_multiplier`, `late_cancel_multiplier`, `accept_shift`,
`delay_multiplier`, `lapse_multiplier`.

Hidden test offices live in `configs/offices_holdout/` (decision record 0008). `make sim` and demos never include
them, and nothing is tuned on them. Tests check they still look like real offices:

| Office | No-shows | Hygiene full | Doctor full | Overdue | Write-offs | Collected |
| --- | --- | --- | --- | --- | --- | --- |
| Bronx, half Medicaid | 8.2% | 81% | 40% | 31% | 51% | 97% |
| Park Slope family | 5.6% | 85% | 55% | 25% | 36% | 99% |
| Upper East Side boutique | 5.6% | 81% | 49% | 21% | 20% | 98% |
