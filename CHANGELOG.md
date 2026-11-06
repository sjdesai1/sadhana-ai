# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [SemVer](https://semver.org/);
each minor version is a finished milestone.

## [Unreleased]

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
