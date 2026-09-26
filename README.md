# SilentDelay — NYC school bus notification & vendor reliability pipeline

When a New York City school bus breaks down or runs late, are parents told? And can the city trust the answer,
given that the bus companies report it themselves?

This project turns NYC's public school-bus incident data into a monthly vendor scorecard that answers those
questions, through a repeatable pipeline: **ingest → validate → model → metrics**.

Track C (own topic) · FDE Data Foundations, Classes 4–8

## The problem

NYC's Office of Pupil Transportation (OPT) pays about 50 private bus companies to carry roughly 150,000 students on
9,000+ routes. When a bus is late or breaks down, the company's own staff file an incident report, including whether
they notified the school, the parents and OPT.

That leaves OPT with three open questions:

1. Are families actually being told about delays?
2. Which vendors perform worst **once you account for how many routes they run**?
3. Is the vendor-reported data reliable enough to base contract decisions on?

## Who it's for

| Stakeholder | What they get |
|---|---|
| OPT contract and operations managers | A monthly, explainable list of vendors to audit, and a list of repeat offenders across months |
| Parents, especially of special-education students | Evidence of how often families are left uninformed |
| School principals | Which run types and vendors leave schools in the dark |
| DOE leadership / City Council | The case for replacing self-reporting with automated notification |

## KPI

**Parent notification rate (M1)**: the share of incidents where the vendor reports that parents were notified.
It is labelled **vendor-claimed** everywhere, because no independent record or timestamp exists. Four supporting
metrics sit alongside it:

| | Metric | What it answers |
|---|---|---|
| M1 | Parent notification rate *(KPI)* | Are families told? |
| M2 | Median logging lag | How quickly is an incident reported? |
| M3 | Student-minutes delayed (lower bound) | How many children waited, and for how long? |
| M4 | Incidents per 100 contracted routes | Is a vendor bad, or just big? |
| M5 | Data-trust score | Can we trust this vendor's reports? |

Definitions are in [docs/data_model.md](docs/data_model.md#the-metrics).

## What the data shows

22 months (September 2024 – June 2026), 162,766 incidents. Full tables:
[summary](data/output/summary/summary_2024-09_2026-06.md).

| | 2024-25 | 2025-26 |
|---|---|---|
| Incidents | 72,918 | 89,848 |
| Parents notified (vendor-claimed) | **56.8%** | **48.4%** |

- **Afternoon General Ed runs:** parents are told in only **1.5%–12.3%** of incidents in every September–June month.
  For Pre-K it is 79%–99%.
- **Seven vendors have a median notification rate of 0%** across the months they were ranked. Pioneer Transportation
  (code PW) was on the audit list in **20 of 20** months.
- Pride Transportation and L & M Bus Corp were flagged in 20 of 22 months for **vendor-controllable** breakdowns
  (mechanical problems, won't start, flat tyres) per route, not for traffic.
- 27% of late incidents are recorded in the top delay option, "61-90 Min", so the real delays are longer than the
  data can show.

## The decision it supports

**Which vendors OPT should audit this term**, based on a transparent rule (bottom-quartile notification,
top-quartile controllable incidents per route, or low data trust), and preferring vendors that are flagged
**month after month** over one bad month.

The wider recommendation: the data is internally consistent (trust about 99.9%), but nothing can verify that a
"Yes, parents notified" really happened. That is the case for **automated, GPS-based notification** instead of
self-reporting.

## Sources

| Source | Owner | How it's retrieved | One row = |
|---|---|---|---|
| [Bus Breakdown and Delays](https://data.cityofnewyork.us/Transportation/Bus-Breakdown-and-Delays/ez4e-fazm) | OPT (typed in by vendors) | Socrata API, paginated, with a count check | one incident |
| [Routes](https://data.cityofnewyork.us/d/8yac-vygm) | OPT | CSV download | one route per school year |
| [Transportation Sites](https://data.cityofnewyork.us/d/hg3c-2jsy) | OPT | CSV download | one school per school year |
| [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api) | Open-Meteo | REST API | one hour |
| [Reason categories](reference/reason_categories.csv) | This project | CSV (hand-built) | one delay reason |

Ownership, gaps and join decisions: [docs/source_map.md](docs/source_map.md).

## How it works

| Stage | What happens | Details |
|---|---|---|
| **Ingest** | Pulls the four sources, saves them untouched, and proves each pull is complete (row counts, checksums, hour counts) | [docs/pipeline.md](docs/pipeline.md) |
| **Validate** | 13 business rules flag problems without deleting or fixing anything; writes a quality report per month | [docs/validation_rules.md](docs/validation_rules.md) |
| **Model** | Loads a star schema in DuckDB inside one transaction, with integrity checks before commit | [docs/data_model.md](docs/data_model.md) |
| **Metrics** | Computes M1–M5 in SQL, applies the audit rule, writes the scorecard | [docs/data_model.md](docs/data_model.md#the-metrics) |

**Dependability:**
- It halts when a critical source fails, and degrades when a supplementary one does.
- A rerun gives byte-identical outputs.
- Every run writes a manifest that records the git commit and config it used.
- Failed reruns never overwrite good outputs.

## Setup and run

Needs Python 3.12 (3.10+ should work) and internet access for the NYC Open Data and Open-Meteo APIs.
No API keys are required.

```bash
python -m venv .venv
.venv\Scripts\activate                     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python pipeline.py --month 2025-10         # one month
python pipeline.py --range 2024-09 2026-06 # the full backfill, plus the cross-month summary
python pipeline.py --month 2025-10 --force # download everything again instead of reusing
pytest                                     # 145 tests, no network needed
```

The first full backfill downloads everything and takes a few minutes; later runs reuse the saved downloads.
Exit code 0 means every month succeeded or ran degraded; 1 means at least one month halted.

## Where to find the evidence

| What | Where |
|---|---|
| Cross-month summary (trend, school years, repeat audits) | [data/output/summary/](data/output/summary/) |
| One month's vendor scorecard | e.g. [data/output/2025-10/vendor_scorecard_2025-10.md](data/output/2025-10/vendor_scorecard_2025-10.md) |
| Every metric with its numerator and denominator | `data/output/<month>/metrics_<month>.csv` |
| Data quality per rule | `data/output/<month>/quality_report_<month>.csv` |
| What each run did | `data/output/<month>/run_manifest.json` |
| Profiling that shaped the rules | [notebooks/01_profiling.ipynb](notebooks/01_profiling.ipynb) |

## Known / Unknown / Assumption / Limitation

The most important points; the full list is in [docs/known_unknowns.md](docs/known_unknowns.md).

- **Known:** which vendor holds each route (for 89% of incidents), and what vendors claim about notification.
- **Unknown:** whether a "Yes, parents notified" really happened. `informed_on` looks like a notification time but is
  a copy of `created_on` in every row.
- **Assumption:** a delay bucket is represented by its midpoint; one weather point covers the whole city; "Problem
  Run" delays are OPT's route design, not the vendor's fault.
- **Limitation:** everything is self-reported by the vendors being evaluated. Delays above 90 minutes can't be
  recorded, so student-minutes is a lower bound.

## The judgement call

Running the full 22-month backfill, vendors' data trust appeared to **collapse**: down to 35% in summer 2025 and
89% in February 2026. Several vendors landed on the audit list for "untrustworthy data".

Before acting on it, I checked what was driving the numbers, and **the problem was in my own rules**:

- **Winter:** the "weather excuse on a dry day" flags fell right after storms (23 cm of snow) or on −15 °C days.
  Snow and ice on the roads are a real cause of delay, even when no new snow falls.
- **Summer:** the "wrong company on the report" flags came from summer routes being run by different companies than
  the school-year contracts describe. The pipeline was charging those incidents to the wrong vendors.

After fixing both, the weather flags dropped from 1,919 to 11, trust is about 99.9% everywhere, and every "untrustworthy
data" audit trigger disappeared. The same backfill also showed that OPT's school year starts in July, not September.

The lesson: when self-reported data looks damning, check the rule before accusing the vendor. Details are in
[docs/validation_rules.md](docs/validation_rules.md#rules-that-changed-after-seeing-the-data).

## Repo layout

| Path | Purpose |
|---|---|
| `pipeline.py` | Single entrypoint (`--month`, `--range`, `--force`) |
| `config.yaml` | Source URLs, thresholds, paths |
| `src/ingest/` | One module per source, completeness checks, write-once raw store |
| `src/validate/` | Typing, vendor attribution, the 13 rules, quality report |
| `src/parse_delay.py` | Delay text → minutes (dropdown and legacy free text) |
| `src/model.py`, `src/sql/` | Star schema and scorecard SQL |
| `src/metrics.py`, `src/summary.py` | Metrics, audit rule, scorecards, cross-month summary |
| `src/utils/` | HTTP retries, logging, manifests, atomic writes |
| `reference/` | Hand-built reason categories |
| `data/output/` | Published results (committed); `data/raw/`, `data/processed/` and the warehouse are rebuilt and not committed |
| `docs/` | Source map, validation rules, data model and metrics, pipeline, known unknowns |
| `notebooks/` | Profiling |
| `tests/` | 145 tests |
