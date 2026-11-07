# Milestone 4: Benefits engine and payer scorecard

Goal: turn each patient's insurance into a number the front desk can act on, and show the office how each
payer treats it.

Code: `src/sadhana/benefits/rules.py` (frequency limits, shared with the simulator), `src/sadhana/benefits/engine.py`
(benefits, estimates, phasing, year-end list), `payer_scorecard` in `src/sadhana/data/queries.py`.

## What it answers

| Endpoint | Answers |
| --- | --- |
| `GET /offices/{id}/patients/{patient_id}/benefits` | the patient's plan, max left, deductible left, coverage by class, when they are next eligible for an exam, cleaning and x-rays, an estimate for every open treatment item, and the year-end options |
| `GET /offices/{id}/year-end?limit=50` | active insured patients whose open treatment would use benefits that expire Dec 31, ranked by insurance dollars at stake, with what the patient pays and what phasing saves |
| `GET /offices/{id}/payers?months=12` | per payer: patients, visits, gross, allowed as a share of the office fee, write-offs, insurance and patient dollars, denial rate, median days to pay, insurance still owed |

## How it works

- Estimates follow claim order: highest priority first, and on the same tooth the root canal or implant before
  the build-up and crown. The deductible applies once to basic and major work, coverage by class, the annual max
  caps what the plan pays, and secondary insurance pays up to the patient's share
- Cash patients get the cash price, covered Medicaid work costs the patient nothing, elective cosmetic work is
  patient-paid, and anything over the max is flagged "annual max reached"
- Year-end phasing: the plan resets January 1. Non-urgent work that would run past this year's max moves to
  January, where next year's max covers it; urgent work always stays now
- Eligibility uses the catalog's frequency limits against the patient's covered claims, so the desk knows the
  first date a cleaning or x-rays will be paid

## Results on the demo offices (as of 2026-11-01)

| | Solo Manhattan | Group Queens |
| --- | --- | --- |
| Active insured patients with open treatment that benefits would cover | 274 | 877 |
| Insurance dollars that expire Dec 31 unless that work is booked | about $84k | about $337k |
| Patient share of that work | about $92k | about $280k |
| Patients who save by moving part of their work to January | 6, about $2.7k in total | 80, about $35k in total |

The year-end list is the first list the outreach agents will work from.
