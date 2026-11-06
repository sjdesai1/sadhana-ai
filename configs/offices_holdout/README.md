# Hidden test offices

Offices with different neighborhoods, payer mixes, staffing and scenarios. Nothing is ever tuned on them: they
exist only to check that the simulator and, later, the models hold up on offices they were not built around.
`make sim` and demos never include them. Tests build them by name through `build.simulate`.

| Office | What is different |
| --- | --- |
| holdout_bronx_medicaid | Half Medicaid, low income, Spanish-speaking majority, no visiting specialists, more no-shows and slower recall |
| holdout_park_slope_family | Families with kids, Saturday hours, part-time junior associate, a payer cutting fees mid-window |
| holdout_ues_boutique | 40% cash, premium fees, older and wealthier, a recession year and a competitor opening |
