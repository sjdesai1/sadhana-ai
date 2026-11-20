# What payers really pay

Found by the scorecard: when a payer cut its rates, the product did not notice. Margins came from the fee
schedules in the configs, not from what payers paid, so the plan and the economics stayed the same.

Now, each time the plan runs (`make optimize`, Dagster `slot_plan`), the product learns from the office's paid
claims:
- Per payer and code, the allowed amount the margins use is
  `w x median allowed on recent claims (6 months) + (1 - w) x the schedule on file`, with `w = n / (n + 5)`.
  A code with many recent claims follows the claims; a rare code leans on the schedule, adjusted by how that
  payer pays across all its codes
- Per payer, a check: the share of its schedule it has paid recently, and the month it changed (a move of more
  than 3% against the three months before). A payer paying more than 3% under its schedule, on 50 or more
  recent claims, is flagged: update the schedule on file, or take it to the payer

Code: `src/sadhana/economics/observed.py`; settings in `configs/economics.json`. Tables: `payer_rates` (only
where the learned amount differs from the schedule) and `payer_schedule_check`. API:
`GET /offices/{id}/payer-check`. `make optimize` prints the flagged payers.

Results:
- Demo offices: every payer pays its schedule, nothing is flagged, and nothing changes
- Payer cut scenario (Cigna allows 10% less from July 2025) at the solo office and the Park Slope hidden office:
  Cigna flagged at 90% of its schedule, change found in July to August 2025, Cigna margins lower, other payers
  untouched, and the slot plan shifts a little away from Cigna work (Cigna is about 10% of patients)
- New scorecard check, `economics.payer_flags_correct`: the payers flagged are exactly the payers the simulation
  cut; it passes on every run

Tests: `tests/test_observed.py`.
