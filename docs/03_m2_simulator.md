# Milestone 2: Simulator

Goal: a synthetic practice realistic enough to build, test and demo every module before a real office connects.

Built in small PRs:

| Part | What it adds | Status |
| --- | --- | --- |
| 1 Population | Patients, households, ZIP and income tier, language, insurance, consent, hidden traits | Done |
| 2 Dental charts | 32 teeth per patient, conditions, restorations, perio stage | Next |
| 3 History | Three years of appointments, recall, treatment plans, no-shows, cancellations | |
| 4 Money | Claims, benefits used, patient portions, balances | |
| 5 Calibration | Checks against real-world targets | |

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
