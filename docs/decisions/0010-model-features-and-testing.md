# 0010 Model features and testing

Status: Accepted

## Context
- Scores decide who gets outreach and what they hear about
- Some fields can stand in for protected traits: language, zip code and neighborhood track race, national origin
  and income; sex is protected directly
- A model tested on data it trained on, or with future information in its features, looks better than it is

## Decision
- Never use language, zip code, neighborhood or sex as model inputs; language only picks the message language
- Features are point-in-time: only what the office knew before the event
- Train on the past, early-stop on the next stretch, test on the latest months; report AUC and calibration
- On simulated offices, also grade against hidden truth, including the best score any model could reach
- Every model is also tested on a hidden test office (decision 0008)

## Consequences
- Some predictive signal is given up on purpose
- A test enforces the excluded fields
- Model metrics are stored with every run in `sadhana_meta.model_runs`, so drift is visible
