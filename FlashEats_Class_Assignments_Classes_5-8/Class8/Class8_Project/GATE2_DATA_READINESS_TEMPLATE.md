# Gate 2 — Data Readiness Review

**Pipeline:** FlashEats order-journey pipeline · **Run date reviewed:** `2026-09-22` · **Command:** `python run_pipeline.py --run-date 2026-09-22`

**Data:** the FlashEats classroom pack from `github.com/manangupta12/flasheats-classroom-pack` (commit `0105c7a`). All evidence below was re-run against it on 2026-09-27.

## Pipeline run

- [x] Complete flow runs with one command. The command starts the mock Dispatch API, extracts, validates, cleans, transforms, saves and logs, then exits 0 with `PIPELINE SUCCESS`.
- [x] Same run can be safely repeated. A second run for the same date produced byte-identical `order_journey.csv`, `metrics.json` and `validation_report.json` (same MD5 hashes), and the row count stayed at 1,600.
- [x] Raw API responses are preserved. 8 pages are saved under `data/raw/dispatch/run_date=2026-09-22/dispatch_page_001–008.json` before any transformation.
- [x] Processed output is written only after validation. In the `missing_column` and `stale_data` runs, the published `order_journey.csv` kept its original timestamp and hash.

## Validation

| Check | Status | Evidence / note |
|---|---|---|
| Required columns | PASS | 8 required order columns present. The `missing_column` chaos run dropped `promised_eta`, which stopped the pipeline at the gate (exit 2) with `orders: missing required columns: ['promised_eta']` and no output written |
| Critical nulls | WARN | 37 delivered orders have no `actual_delivery_at`. They are excluded from the late rate (1,495 measurable deliveries) and reported, not dropped. Not checked: 3 orders with null `restaurant_id` and 3 with null `driver_id` |
| Order uniqueness | WARN | 6 rows are involved in duplicates (3 extra rows), and the agreed rule de-duplicates to 1,600 orders. **Limitation:** the duplicate copies disagree on `traffic_bucket`, and `keep="first"` settles that silently (found in Class 6). In the `duplicate_order` chaos run, the injected duplicate was removed (1,604 → 1,600 rows), and `order_journey.csv` and `metrics.json` were unchanged. `validation_report.json` in the real partition was overwritten, though (`duplicate rows involved=8`), which is the partition gap noted under *Idempotent rerun* |
| Freshness | PASS | Latest `created_at` is 2026-08-28, 25 days before the run date, within `MAX_DATA_AGE_DAYS=60`. The `stale_data` chaos run (−365 days) stopped the pipeline with `age_days=390, allowed=60`. **Caveat:** 60 days suits a monthly KPI report, not operational use |
| Dispatch retrieval completeness | PASS | 1,600 records retrieved against `total_records=1600`, across 8 pages of 200. The HTTP 500 on page 3 and the 429 on page 5 were each recovered on attempt 2 |

## Reliability

| Capability | Status | Evidence / note |
|---|---|---|
| Bounded retries | PASS | `MAX_RETRIES=3` with backoff. It honours the 429 `retry_after_seconds`. With the API unreachable (`DISPATCH_API_URL` pointed at a dead port), it tried 3 times and stopped with `Dispatch API page 1 failed after 3 attempts`. A schema failure is not retried |
| Useful failure message | PASS / WARN | Validation failures give one clear line naming the stage, the dataset and the reason. The API-unreachable case prints a raw Python traceback before the `PIPELINE FAILED` line. It's correct, but noisier than it needs to be for an on-call engineer |
| Logging | PASS | `logs/pipeline_<run-date>.log` records every stage with counts (rows extracted, pages, retries with page/status/attempt/wait, every validation result, rows de-duplicated, output partition) |
| Idempotent rerun | PASS / WARN | Output is partitioned by logical `run_date` and replaced, not appended. The rerun gave identical hashes. **Gaps:** only `order_journey.csv` uses an atomic temp-file replace, while `metrics.json` and `validation_report.json` are overwritten in place, so a crash mid-save could leave a mixed partition. Also, a *successful* chaos run (`duplicate_order`) wrote to the real `run_date=2026-09-22` partition. Test runs should use a separate partition |
| Configuration outside core logic | PASS | API URL, retries, backoff, freshness window, page size, auto-start and log level all come from environment variables (`config/.env.example`) through `PipelineConfig.from_env` |

## Known limitations

- **`driver_arrived_at_restaurant` is not captured.** Order-to-pickup time (median 26.1 minutes) is where most delay builds up (Class 7), but it can't be split into restaurant preparation versus driver arrival.
- **Late Delivery Rate has no documented owner or definition** (Class 6). The pipeline publishes 56.39% using "any delay > 0"; a "> 10 minutes" definition gives about 23%.
- **Chronology is not validated.** 5 orders are "delivered" before pickup (the Order DB pickup time is wrong, according to driver events) and 4 promised ETAs precede order creation. These rows pass straight into the metrics.
- **Two intervention sources still disagree.** The pipeline now reads the same 430-row `order_interventions.csv` as Class 7, so `orders_with_intervention` (430) matches Class 7. An earlier run against an older 260-row file reported 260. `order_events.csv` still holds 260 intervention events, only 29% of them on orders in `order_interventions.csv` and with different type names (`ETA_MESSAGE`, `REFUND_OFFER`). The pipeline treats `order_interventions.csv` as the system of record, but no owner has confirmed that.
- **Category semantics are only partly handled.** Case and whitespace in `final_status` and `traffic_bucket` are normalised. Restaurant `handoff`, `unknown` and ticket `ETA issue` still need owner confirmation.
- **Dispatch validation is thin.** Only 2 columns are required. The meaning of the ETA (original promise vs the current ETA, which drifts about 9 minutes later) isn't checked.

## Gate decision

**NOT READY** for the next product/AI phase (delay prediction). **READY** as a dependable pipeline for descriptive KPI reporting, with the limitations above published alongside the metric.

**Reason:** the *pipeline* is dependable. It runs from one command, reruns safely, preserves raw data, retries only transient failures, and stops before publishing when the schema breaks or data goes stale. The *data foundation* isn't ready for an AI delay predictor, because the label itself isn't settled: "late" has no owner, the stage where delay builds up has no arrival event, and chronology errors aren't caught. A model trained now would learn a label the business hasn't agreed on. Before moving to Gate 3:

1. Name the KPI owner and record the definition.
2. Add chronology checks (pickup ≤ delivery, promised ETA ≥ created) as WARN-level validations.
3. Have the intervention owner confirm `order_interventions.csv` as the canonical source, and retire or reconcile the intervention rows in `order_events.csv`.
4. Instrument `driver_arrived_at_restaurant` and a timestamped `food_ready` event.
5. Make the whole partition write atomic, and route chaos runs to a test partition.
