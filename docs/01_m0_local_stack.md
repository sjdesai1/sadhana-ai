# Milestone 0: Local stack

Goal: every service the project needs runs on the laptop with one command, and CI runs the same checks on every PR.

## What runs, and why

| Service | What it is | Why this project needs it | Open it |
|---|---|---|---|
| App | FastAPI, one app with every layer as a module | API today; agents, booking and reports later | http://localhost:8000/docs |
| Postgres | Relational database | Practice data, one schema per office; job queue for same-day events | `localhost:5432` |
| Dagster | Data pipeline orchestrator | Nightly assets: economics now; sync, scores, 90-day plan, reports later | http://localhost:3000 |
| Mailpit | Fake email server with a web inbox | Catches every email the agents send, so they can be read | http://localhost:8025 |
| Open Dental test DB | MySQL with Open Dental demo data | PMS sync against a real PMS schema (profile `opendental`, from the sync milestone) | `localhost:3306` |

## One-time setup

1. Docker Desktop running, uv installed, VS Code with the Python and Ruff extensions
2. `make setup` (creates `.env`, `.venv`, git hooks)
3. VS Code: Ctrl+Shift+P, "Python: Select Interpreter", pick `.venv`

## Daily

| Command | What it does |
|---|---|
| `make up` / `make down` | Start / stop the stack |
| `make check` | Lint, format check, tests: run before every push |
| `make format` | Auto-format |
| `make cov` | Tests with coverage |
| `make typecheck` | pyright, report only |
| `make economics` | Rebuild economics tables into `outputs/` |
| `make dagster` | Dagster UI without Docker |

## Git flow

1. `git checkout -b feature/short-name`
2. Small commits; pre-commit runs ruff and gitleaks on each
3. Push, open a PR, CI must be green
4. Merge, then `git checkout main && git pull`

## Jupyter

Only for model exploration, in `notebooks/`, never imported by app code. Anything that ships moves into `src/`.
