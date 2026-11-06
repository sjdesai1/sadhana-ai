# 0006 Hidden truth vs observed data

Status: Accepted

## Context
- Real PMS data never says how likely a patient is to come back, only what they did
- Models trained and judged on clean synthetic data look better than they will on a real office

## Decision
- Every simulated patient has two records: `patients.csv` (what a PMS would hold) and `truth.csv` (hidden traits)
- Observed data carries real-world noise: missing and wrong health-form answers, missing phones, partial consent
- Models only ever read observed data; truth is used to score them

## Consequences
- Model quality can be measured against the truth, which no real office can offer
- Every new simulator field must be placed on one side or the other on purpose
