# Sadhana AI

Schedule and revenue recovery for dental practices. Finds the money already in an office's patient list and schedule, reaches patients in their language, books them into the right slots, and proves the dollars recovered.

## Stack

- One FastAPI app, every layer as a module
- Postgres, one schema per office
- Dagster for the nightly pipeline, Postgres job queue for same-day events
- LangGraph agents, LangSmith tracing, OR-Tools optimizer
- Docker Compose locally; GitHub Actions for CI/CD

## Quick start

    make setup
    make check
    make economics
    make up

- API: http://localhost:8000/docs
- Dagster: http://localhost:3000
- Mailpit: http://localhost:8025

## Layout

    configs/      state scope rules, procedure catalog, payers, office configs
    src/sadhana/  api, economics, pipelines (more layers added per the build order)
    tests/        pytest suite, run on every PR
    docs/         one doc per milestone, decisions in docs/decisions/

## Workflow

- Start with `docs/STATUS.md`: where the build stands, what is next, latest benchmarks
- Branch off main, open a PR for every change
- Run `make check` before every push; CI runs the same plus compose validation, pyright, pip-audit and a Docker build
- Merge to main publishes a versioned image to GitHub Container Registry
