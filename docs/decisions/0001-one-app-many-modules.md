# 0001 One app, many modules

Status: Accepted

## Context
- Built and run solo; the first 15-20 offices fit easily on one server
- Separate services per layer mean more to deploy, monitor and debug

## Decision
- One FastAPI app; inputs, data, intelligence, agents and outputs are modules inside it
- Dagster runs the nightly work against the same package

## Consequences
- One image, one deploy, one place to look when something breaks
- A layer can split into its own service later if load demands it; module boundaries make that a move, not a rewrite
