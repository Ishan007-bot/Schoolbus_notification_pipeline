# SilentDelay: NYC School Bus Notification & Vendor Reliability Pipeline

> Work in progress. This README will be completed at the end of the project.

NYC's Office of Pupil Transportation pays private bus companies to transport ~150,000 students.
Delays and breakdowns are self-reported by those same companies. This pipeline pulls the public
incident data plus supporting sources, validates it without silently fixing it, and produces a
monthly vendor scorecard to decide which vendors should be audited.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Run

```bash
python pipeline.py --month 2025-09
pytest
```

## Repo layout

| Path | Purpose |
|---|---|
| `pipeline.py` | Single entrypoint |
| `config.yaml` | Source URLs, thresholds, paths |
| `src/ingest/` | One module per source |
| `src/validate/` | Validation rules and quality report |
| `src/utils/` | Logging, run manifest, HTTP retries |
| `data/raw/` | Untouched downloads (not committed) |
| `data/processed/` | Validated / flagged tables (not committed) |
| `data/output/` | Final metrics and scorecards |
| `docs/` | Source map, data model, validation rules, known unknowns |
| `notebooks/` | Profiling and dashboard |
| `tests/` | Unit tests |
