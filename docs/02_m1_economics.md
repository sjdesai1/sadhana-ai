# Milestone 1: Economics engine

Works out, for one office, who can do each procedure, what each payer pays, and the margin.

## Inputs (configs/)

- `rules/scope_NY.json`: NY scope of practice. RDA duties, unlicensed assistant limits, hygienist anesthesia and nitrous certs, no sealants by assistants, no cancellation fees for Medicaid.
- `catalog/service_catalog.csv`: 39 CDT procedures. Chair and doctor minutes, visits, allowed roles, support role, required cert, minimum dentist tier, equipment, office fee, lab and material cost, frequency limit, lab turnaround.
- `payers/payers_NY.csv`: allowed amount as a share of office fee by benefit class, coverage, deductible, annual max, waiting periods.
- `payers/medicaid_fees_NY.csv`: NY Medicaid fees, eMedNY 2026 where published, estimates flagged.
- `offices/*.json`: one file per office. Fee level, payer mix, hours, operatories, equipment, policies, rules, full staff roster.

## How it decides

1. Eligibility: role allowed, specialist scope, required cert, senior tier, office equipment.
2. Allowed amount: cash at office fee less discount; PPO at its share of office fee; Medicaid from its schedule; elective cosmetic at the office fee for everyone.
3. Cash margin = collected - lab - materials - provider labor - support labor.
4. Provider labor: hourly staff by the hour, associates and specialists by share of collections, owner at zero cash cost.
5. Who does it: every dentist can carry an hourly value. Ranking subtracts it, so routine work goes to the cheapest qualified person.

## Outputs (outputs/<office>/)

- `procedure_summary.csv`: best provider and expected margin per chair hour at the payer mix
- `economics_detail.csv`: every procedure x payer x provider
- `eligibility_matrix.csv`: every staff member x procedure, and why not
- `payer_summary.csv`: write-offs and expected late-cancel fee value
- `not_covered.csv`: what a payer won't pay

## Data quality

Rows marked estimate are modeled from public NYC price lists. Each office's real fee schedule and claims history replace them at onboarding.
