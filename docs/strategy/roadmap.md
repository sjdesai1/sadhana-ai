# Product roadmap

## Testing stages (inside product v1)
- Stage 1: the whole product on the two demo offices
- Stage 2: more and different simulations (hidden test offices, scenarios, stress settings)
- Stage 3: real data, Open Dental's demo database, then a pilot office

## Product versions
- v1: schedule recovery. Recall, treatment follow-up, slot optimizer, at-risk visits, outreach with measured lift
- v2: insurance verification, patient collections
- v3: AI receptionist, claims and denials, payments and financing, membership plans for uninsured patients
- v4: benchmarks across offices, staffing, supply ordering, practice valuation for private equity and dental groups
- v5: own PMS, and the full stack for each vertical

The messaging engine is built vertical-neutral in v1, so new verticals are a settings pack and an adapter.

## Pricing
Founding offices, the first 30:
- $500 a month for the core product, locked while they stay
- 20% off any v2 or v3 add-on
- In return: a case study, reference calls, feedback sessions

Standard pricing after the first 30:

| Tier | Includes | Small (1-2 dentists) | Group (3-5 dentists) | Multi-location |
| --- | --- | --- | --- | --- |
| Core | v1 | $750 | $1,250 | $1,000 per location |
| Growth | Core + v2 | $1,250 | $2,000 | $1,600 per location |
| Pro | Growth + v3 software | $2,000 | $3,000 | $2,400 per location |

- Add-ons on their own: verification $400, collections $300, claims and denials $600, AI receptionist $350,
  membership plans $150
- Payments and financing: free to the office; revenue from processing and lender referral fees
- Annual prepay: 2 months free. Yearly increase 3-5% (not for founding offices). Multi-location: 10% off at 5+,
  15% at 10+
- Guarantee: under 5x the fee recovered in a quarter, the next month is free

## Verticals
Scored 1-5, higher is better (Entry: easier to get in; Competition: more open). Judgment calls, to revisit.

| Rank | Business | Value | Potential | Entry | Competition | Main competitors |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Dental (general) | 5 | 5 | 4 | 3 | Weave, RevenueWell, Dental Intelligence, Lighthouse 360 |
| 2 | Dental specialists | 5 | 3 | 4 | 4 | Same tools, weaker fit |
| 3 | Med spas | 5 | 4 | 4 | 3 | Boulevard, Zenoti, Mangomint, PatientNow |
| 4 | B2B distributor reorder and churn | 5 | 4 | 3 | 4 | Choco, Pepper, BlueCart |
| 5 | Vets | 4 | 4 | 3 | 3 | PetDesk, Vetstoria, Weave |
| 6 | Physical therapy and chiro | 4 | 4 | 3 | 4 | WebPT, Prompt |
| 7 | Optometry and dermatology | 4 | 3 | 3 | 3 | Weave, Solutionreach, ModMed |
| 8 | Restaurants (POS-based) | 3 | 5 | 3 | 2 | Toast, SevenRooms, Thanx, Paytronix |
| 9 | Salons, barbers, nails, lash | 2 | 5 | 4 | 2 | Vagaro, Boulevard, Fresha, Square |
| 10 | Home services | 4 | 4 | 3 | 2 | ServiceTitan, Housecall Pro, Jobber |
| 11 | Auto service | 3 | 4 | 3 | 3 | Tekmetric, Shopmonkey |
| 12 | Bars | 3 | 4 | 2 | 3 | Restaurant tools; alcohol texting rules |
| 13 | Mental health practices | 3 | 3 | 2 | 3 | SimplePractice, Headway |
| 14 | Fitness studios | 2 | 4 | 3 | 2 | Mindbody, ClassPass |
| 15 | B2B SaaS churn and expansion | 4 | 5 | 2 | 1 | Gainsight, ChurnZero, Vitally |
| 16 | Retail and grocery | 2 | 5 | 3 | 1 | Klaviyo, Attentive, Postscript |

Order:
1. Appointment businesses: dental, specialists, med spas, salons and barbers, vets
2. Point-of-sale businesses: restaurants, bars, retail, grocery (POS adapter, uplift models, holdout-measured lift)
3. B2B, starting with distributor reorder and churn

Edge in every vertical: models that pick who to contact, when and with what, and holdout groups that prove the
lift. Sending messages alone is a crowded market.

## International, later
Canada, Australia, UK, Netherlands and other wealthy markets, each with its own privacy and messaging rules.
