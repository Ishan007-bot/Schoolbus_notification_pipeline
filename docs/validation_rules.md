# Profiling and validation

## Principle: flag, don't fix

Nothing is deleted or corrected. Every incident gets:

- `is_valid` — false if any **critical** rule fails (the row can't be trusted for metrics)
- `failure_reasons` / `warning_reasons` — the rule IDs that fired, e.g. `V02` or `V03;V12`

Each month writes `data/output/<month>/quality_report_<month>.csv` with the count per rule, even when the run
halts. If more than **25%** of a month's rows fail a critical rule the pipeline stops: at that point something upstream
is broken and metrics would mislead.

## What profiling found first

Rules were written after profiling one month (October 2025) in
[`notebooks/01_profiling.ipynb`](../notebooks/01_profiling.ipynb). The main findings:

- `informed_on` always equals `created_on` → there is no real notification timestamp.
- The delay field has been a 5-option dropdown since 2018-19 (2016-17 had 1,205 free-text spellings); the top option
  "61-90 Min" is open-ended.
- Breakdowns never have a delay value; the median number of students on board is 0.
- Pre-K routes aren't in the Routes table; most incidents serve several schools; `boro` includes places outside NYC.
- A handful of incidents are logged before they happened, or days later, or with impossible student counts.

## Rules

Counts are over the full backfill: **22 months, 162,766 incidents, 8 invalid (0.005%)**.

| ID | Level | Business rule | Why it matters | Flagged |
|---|---|---|---|---|
| V01 | critical | `busbreakdown_id` is unique (later copies flagged) | duplicates would double-count incidents | 0 |
| V02 | critical | an incident is logged at or after it occurred | "logged before it happened" means one of the timestamps is wrong | 8 |
| V03 | warning | logged within 24 hours of occurring | a report typed days later can't have warned anyone | 53 |
| V04 | critical | `school_year` matches the date (school year runs July–June) | catches entry errors such as `1899-1900` | 0 |
| V05 | critical | occurred inside the requested month and not in the future | guards against filter bugs and future-dated records | 0 |
| V06 | warning | a Running Late incident has a readable delay | unreadable delays are excluded from M3, never guessed | 0 |
| V07 | warning | parsed delay is 1-180 minutes | catches values like `"9568"` in legacy text | 0 |
| V08 | warning | vendor attributable from Routes contract data | measures how much attribution relies on name fallback (mostly Pre-K, all summer) | 18,037 |
| V09 | warning | reported company matches the contracted vendor for the route | possible subcontracting or mid-year route transfer | 12 |
| V10 | warning | students on board is a number from 0 to 72 | e.g. 9,056 students on one bus | 70 |
| V11 | warning | notification flags are Yes/No | unusable flags are excluded from the M1 denominator | 0 |
| V12 | warning | a "Weather Conditions" reason is supported by rain (≥ 1 mm), snow that day or in the previous 3 days, or freezing temperatures | tests the vendor's excuse against independent data | 11 |
| V13 | warning | every `schools_serviced` code exists in Transportation Sites | unknown schools can't be located | 327 |

Only the warnings that describe the **vendor's own reporting** (V03, V06, V07, V09, V10, V12) count against the M5
trust score. V08 and V13 are gaps in OPT's reference data, not vendor behaviour. A rule whose supporting source is
unavailable (e.g. weather) is reported as **skipped**, never as failed.

## Parsing the delay field

[`src/parse_delay.py`](../src/parse_delay.py) turns each value into `low`, `high`, `est` (midpoint), a `method`, and a
`censored` flag (the real delay may be longer).

| Input | Result | Method |
|---|---|---|
| `16-30 Min` | 16–30, est 23 | bucket |
| `61-90 Min` | 61–90, est 75.5, censored | bucket |
| `20mims`, `5MINUTOS.` | 20, 5 | exact |
| `1 hour 15`, `1hr/20min` | 75, 80 (hours then minutes = one duration) | exact |
| `40-1HR` | 40–60 (40 > 1, so it must be minutes) | range_midpoint |
| `20` | 20 | exact_assumed_minutes |
| `2`, `1-2` | not read — minutes or hours? | unparseable |
| `8:07 am`, `heavy flow` | not read | unparseable |
| blank | not read, never treated as 0 | missing |

Tested against all 1,922 real legacy spellings (about 240,000 rows from 2015-18): 99.6% of non-blank values are read.

## Rules that changed after seeing the data

| Change | What the data showed |
|---|---|
| V08 became a warning (planned as critical) | Pre-K routes are absent from Routes by design; critical would have discarded every Pre-K incident |
| V09 redefined | "one route, two vendors" never happens; "reported company ≠ contracted vendor" does |
| V04: school year starts in **July**, not September | July/August 2025 failed V04 on 100% of rows: summer incidents carry the upcoming school year |
| Summer months attribute vendors by name | V09 fired on 57-65% of summer incidents: summer routes are run by other companies |
| V12 accepts recent snow and freezing days | 1,798 winter flags fell right after storms (23 cm of snow) or at -15 °C; flags dropped from 1,919 to 11 |
| V12 treats "no weather data" as no evidence | a missing weather row must not count against the vendor |

The last three changes removed false accusations: before them the trust score appeared to fall to 35% in some months
and several vendors were on the audit list for "untrustworthy data".
