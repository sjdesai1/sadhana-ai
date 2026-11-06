# 0008 Hidden test offices

Status: Accepted

## Context
- The simulator's numbers are assumptions; models trained on two demo offices can end up good only at those two
- A real office will differ in neighborhood, payer mix, staffing and market conditions

## Decision
- Keep a set of hidden test offices in `configs/offices_holdout/` with different areas, payer mixes, staffing,
  scenarios and stress overrides
- Never tune simulator settings, model features or thresholds while looking at them; use them only to test
- `make sim` and demos never include them

## Consequences
- Every model and optimizer step reports results on the hidden offices next to the demo offices
- A result that holds only on the demo offices is treated as a failure
- New hidden offices can be added; existing ones are not edited to make results look better
