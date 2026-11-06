# Milestone 2: Simulator

Goal: a synthetic practice realistic enough to build, test and demo every module before a real office connects.

Built in small PRs:

| Part | What it adds | Status |
| --- | --- | --- |
| 1 Population | Patients, households, ZIP and income tier, language, insurance, consent, hidden traits | Done |
| 2 Dental charts | Teeth by age, existing work, active problems, gum disease, treatment plans | Done |
| 3 Clock and history | Day-by-day clock: appointments, recall, no-shows, cancellations, seasonality, new patients, churn | Next |
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
