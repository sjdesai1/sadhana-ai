# Milestone 2: Simulator

Goal: a synthetic practice realistic enough to build, test and demo every module before a real office connects.

Built in small PRs:

| Part | What it adds | Status |
| --- | --- | --- |
| 1 Population | Patients, households, ZIP and income tier, language, insurance, consent, hidden traits | Done |
| 2 Dental charts | Teeth by age, existing work, active problems, gum disease, treatment plans | Done |
| 3a Clock and hygiene history | Day-by-day clock: hygiene schedule, recall, no-shows, cancellations, seasonality, new and former patients | Done |
| 3b Treatment history | Doctor and specialist schedule, plan items scheduled / completed / declined, new findings, emergencies | Next |
| 4 Money | Claims, benefits used, patient portions, balances, year-end resets | |
| 5 Life events and calibration | Layoffs, retirement, coverage changes, market scenarios; checks of the full multi-year run | |

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
