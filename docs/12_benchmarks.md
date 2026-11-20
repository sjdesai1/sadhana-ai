# Simulator benchmarks

Everything the product claims rests on how simulated patients behave, so the simulator is tuned to published
figures where they exist. Where none exist, the setting is a stated assumption, and the pilot office settles it.

| Behavior | Published figure | Simulator before | Simulator now |
| --- | --- | --- | --- |
| No-shows | 3% DSO average; under 1% top 10% (Henry Schein One, 2026 Catalyst Index) | 6-7% | about 5% |
| Cancellations | 5% DSO average (same source) | late cancels 4% | late cancels 4% |
| Case acceptance | 45% average, 75% top 10% (Catalyst Index 2026); 61% including fillings (Levin Group survey, 2016) | 55-61% | 51-59% |
| Patients returning within 18 months | 58% at groups, 70% solo average (Catalyst Index 2026) | 59-63% | 60-65% |
| Text reminders on attendance | 67.8% to 78.6% attendance, about +11 points (Cochrane review CD007458); odds ratio 1.48 (Guy et al., meta-analysis of trials) | confirmations cut misses about 45% | about 30% |
| Outreach to overdue patients | 35-40% of dormant patients book when reached (vendor whitepaper, PracticeAnalytics 2020; weak source) | about 32% booked in 2 weeks, about +17 points over the holdout | about 30%, about +12 points |
| Treatment follow-up | no published figure found | +9% of non-bookers moved | +7% |
| Text opt-outs | about 0.5% more unsubscribes per extra send (Postscript, retail marketing) | 1-2% of messages | about 0.5-0.9% |

What changed: `configs/simulator/clock.json` (reminder no-show factor 0.62 to 0.45; needed-work acceptance lowered)
and `configs/simulator/responses.json` (outreach lift: recall 0.2 to 0.15, treatment 0.09 to 0.07, confirmation
effect 0.45 to 0.3, opt-outs per text 1.2% to 0.6%).

Caveats:
- The Catalyst Index figures are for multi-location groups; solo offices run a little higher on no-shows and
  higher on retention. The simulator sits between them
- Published reminder trials compare reminders to none. The product's outreach comes on top of the office's own
  reminders, so its true effect should be smaller; the settings above lean that way
- The outreach and opt-out figures come from vendors and marketing data, not trials. They are the weakest inputs,
  and the first a pilot office should replace

What the retuning showed (scorecard, seed 42):
- With realistic, lower no-show rates, missed visits are harder to predict: AUC at the demo offices fell from about
  0.61 to 0.59, and two hidden test offices fell below the 0.58 target (Park Slope 0.56, the boutique 0.52, also
  0.10 from the best possible). Recorded, not tuned (decision 0008); better missed-visit features are next model
  work
- The treatment model's recalibration could flip its ranking when a small recent window gave a negative slope
  (AUC 0.38 at the heavy no-show office). Fixed: recalibration now only shifts the level when the slope is flat
  or negative
- The 13-week forecast now averages the last 13 weeks, not 8, after the solo office's doctor quarter came in 30%
  low on one backtest

Sources:
- [Henry Schein One: no-show and cancellation rates](https://www.henryscheinone.com/insights/blogs/dso-dental-practice-no-show-cancellation-rate/)
- [Henry Schein One: case acceptance rates](https://www.henryscheinone.com/insights/blogs/dso-dental-practice-case-acceptance-rate/)
- [Henry Schein One: patient retention rates](https://www.henryscheinone.com/insights/blogs/dso-dental-practice-patient-retention-rate/)
- [DentistryIQ: national average case acceptance (Levin Group, 2016)](https://dentistryiq.com/practice-management/industry/article/16366701/july-data-bite-heres-the-national-average-for-case-acceptance)
- [Cochrane review: mobile phone messaging reminders for attendance](https://www.cochrane.org/evidence/CD007458)
- [Guy et al.: SMS reminders and clinic attendance, meta-analysis](https://pmc.ncbi.nlm.nih.gov/articles/PMC3419880/)
- [Clerri: dental patient reactivation statistics](https://clerri.com/blog/dental-patient-reactivation-statistics/)
- [Postscript: SMS unsubscribe facts](https://postscript.io/blog/surprising-sms-unsubscribe-facts)
