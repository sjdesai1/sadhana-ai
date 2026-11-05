# 0002 Schema per office

Status: Accepted

## Context
- Each office's patient data must never reach another office
- A database per office is the strongest boundary but heavy to run at 15-20 offices

## Decision
- One Postgres server, one schema per office, row-level security on every table
- No cross-office data in prompts or model features, except pooled priors built from de-identified aggregates

## Consequences
- Isolation that holds up to a HIPAA review, with one server to back up and patch
- A large client can move to its own database later without code changes, since queries already scope to one office
