# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [SemVer](https://semver.org/);
each minor version is a finished milestone.

## [Unreleased]

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
