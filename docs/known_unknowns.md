# Known / Unknown / Assumption / Limitation

## Known

- Every incident was reported by vendor staff into OPT's Bus Breakdown and Delay system (162,766 incidents,
  September 2024 – June 2026).
- Which vendor holds each route in each school year (OPT's Routes contract data), for 88.9% of incidents.
- The vendors' own claims: parents notified in **56.8%** of incidents in 2024-25 and **48.4%** in 2025-26; for
  afternoon General Ed runs, 1.5%–12.3% in every September–June month.
- Actual hourly rain, snow and temperature at a central NYC point.

## Unknown

- **Whether a "Yes, parents notified" actually happened.** There is no independent record and no notification
  timestamp (`informed_on` is a copy of `created_on`).
- The actual arrival time of any bus.
- How many runs happen each day, so incidents can only be normalised per contracted route, not per run.
- Incidents that were never reported at all — under-reporting is invisible in this data.
- Who operates summer routes (the Routes table describes school-year contracts).
- Why July and August 2026 have no incidents published yet.

## Assumptions

- A delay bucket is represented by its midpoint (`16-30 Min` → 23); the low and high bounds are kept.
- A bare number in legacy delay text means minutes, unless it is 5 or less (then it is left unread).
- A "Weather Conditions" reason is plausible after rain (≥ 1 mm), snow that day or in the previous 3 days, or on a
  freezing day.
- One weather point represents all five boroughs.
- Reason categories (vendor-controllable / route design / external / unknown) follow
  [`reference/reason_categories.csv`](../reference/reason_categories.csv); "Problem Run" is OPT's route design, not
  the vendor's fault.
- A vendor needs at least 30 incidents in a month to be ranked; audit triggers are quartiles of ranked vendors.
- Borough is derived from the site's ZIP code.
- The school year runs July to June (observed in the data).

## Limitations

- **All incident data is self-reported by the vendors being evaluated.** M1 is labelled "vendor-claimed" everywhere.
- The top delay option is "61-90 Min", so longer delays are invisible: **27.3%** of late incidents are in that bucket,
  which makes student-minutes (M3) a lower bound.
- The trust score is about 99.9% for almost every vendor. The data is internally consistent; what cannot be checked
  is whether the claims are true — this is the case for automated, GPS-based notification.
- Vendors without contract route data (mostly Pre-K, 8.4% of incidents) have no M4; summer months have no M4.
- Route paths are withheld for student privacy, so no geographic route analysis is possible.
- "Other" and unmapped reasons are 17.5% of incidents, which limits root-cause conclusions.
- Quartile-based triggers always name somebody each month; the cross-month repeat-audit view is the stronger signal.
- Batch pipeline, one month at a time; not real-time.
