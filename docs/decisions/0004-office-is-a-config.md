# 0004 Every office is a config file

Status: Accepted

## Context
- Offices differ in staff, certs, equipment, fees, payer mix, hours and rules
- Scope of practice differs by state

## Decision
- One JSON file per office in `configs/offices/`
- State scope rules in `configs/rules/scope_<STATE>.json`; payers and Medicaid fees per state
- The engine reads configs; no office or state logic in code

## Consequences
- A new office is onboarding data, not a code change
- A new state is a rules file plus payer files
