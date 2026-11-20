# Product roadmap

## Testing stages (inside product v1)
- Stage 1: the whole product on the two demo offices
- Stage 2: more and different simulations (hidden test offices, scenarios, stress settings)
- Stage 3: real data, Open Dental's demo database, then a pilot office

## Product versions
- v1: schedule recovery. Recall, treatment follow-up, slot optimizer, 90-day plan, outreach with measured lift,
  replies and refill, the front desk queue, the monthly report, payer rates from paid claims, plus the 20 items in
  `docs/STATUS.md` (segments, churn and lifetime value, uplift, market report, year-end push and January restart,
  family scheduling, waitlist, scorecards and fee check, huddle sheet and alerts, owner digest, cosmetic interest,
  same-day treatment, insurance-aware booking, slot holds, reminders by risk, exam routing, reason codes, closure
  rebooking)
- v2: insurance verification, patient collections; fee tuning, missed billing (documented work only, with
  over-coding flags), leak and embezzlement detection, message testing (wording, time, channel), staffing
  suggestions, the ideal weekly template, lab case timing, recall intervals by risk (dentist sets), room and
  assistant scheduling, deposits for repeat no-shows (policy suggestion), post-procedure check-ins, online forms,
  a self-booking link, data export
- v3: AI receptionist, claims and denials, payments and financing, membership plans for uninsured patients,
  chairside treatment presentation with exact out-of-pocket and financing, referrals kept in house
- v4: benchmarks across offices, staffing marketplace, supply ordering, practice valuation for private equity and
  dental groups, accounting connection (overhead against similar offices), marketing attribution, dashboards for
  dental groups, paid market data (search demand, competitor reviews and prices, mobility), de-identified
  benchmarks (only with BAAs, proper de-identification and counsel)
- v5: own PMS, and the full stack for each vertical

Rules that hold in every version: insurance-driven offers only for diagnosed or clinically appropriate care;
cosmetic only with marketing consent and the dentist's approval; suggestions to sell go to the dentist, never
straight to the patient; language, zip, neighborhood and sex are never model inputs; area data describes the
office's market, never a patient.

## Cost to run
- Target about $150 per office per month. Core v1 runs about $25-60 (texts, email, database, nightly models);
  shared fixed costs (HIPAA hosting, backups, the texting provider) push it higher until about 15-20 offices
- Add-ons with per-use costs (receptionist voice minutes, clearinghouse fees) are priced to cover themselves
- A cost meter per office, hard caps on messages and language-model spend, and an alert above target

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
- Later: performance pricing (a base fee plus a share of measured recovered revenue above a floor), per-provider
  pricing, setup fees and annual contracts

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

## Other markets
- POS businesses (restaurants, bars, retail, grocery): models for win-back, slow nights, offers and uplift; bars
  need age-gated consent for alcohol texts
- B2B: distributor reorder and churn first; email outreach only (CAN-SPAM), never cold texts

## International, later
Canada, Australia, UK, Netherlands and other wealthy markets, each with its own privacy and messaging rules.
