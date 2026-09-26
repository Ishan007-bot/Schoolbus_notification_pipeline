"""End-to-end: the whole pipeline against a fake internet (no network)."""
import json
from datetime import datetime
from itertools import count

import pandas as pd
import pytest
import requests

import pipeline
from pipeline import run_month
from tests.conftest import FakeResponse
from tests.test_validate import incident

NOW = datetime(2026, 9, 27)
OUTPUT_FILES = ["quality_report", "metrics", "vendor_scorecard"]

ROUTES_CSV = pd.DataFrame({
    "School_Year": ["2025-2026"] * 2, "Route_Number": ["K100", "K200"], "Service_Type": ["D2D"] * 2,
    "Vendor_Code": ["AA", "BB"], "Vendor_Name": ["ALPHA BUS", "BETA BUS"],
}).to_csv(index=False).encode()
SITES_CSV = pd.DataFrame({
    "School_Year": ["2025-2026"] * 2, "OPT_Code": ["01001", "02002"], "Name": ["A", "B"],
    "Site_Type": ["School"] * 2, "City": [None, None], "Zip": ["10002", "11201"],
    "Latitude": ["40.7"] * 2, "Longitude": ["-74.0"] * 2,
}).to_csv(index=False).encode()


def month_rows(month, n=5):
    day = f"{month}-08"
    return [incident(f"{month}-{i}", occurred_on=f"{day}T07:00:00.000", created_on=f"{day}T07:05:00.000")
            for i in range(n)]


class FakeInternet:
    """Answers the four source URLs from config; `down` holds sources that return HTTP 503."""

    def __init__(self, cfg, incidents):
        self.src = cfg["sources"]
        self.incidents = incidents
        self.down = set()
        self.calls = {"incidents": 0, "routes": 0, "sites": 0, "weather": 0}

    def get(self, url, params=None, timeout=None):
        name = {self.src["incidents"]["api_url"]: "incidents", self.src["routes"]["csv_url"]: "routes",
                self.src["sites"]["csv_url"]: "sites", self.src["weather"]["api_url"]: "weather"}[url]
        self.calls[name] += 1
        if name in self.down:
            return FakeResponse(status_code=503)
        if name == "routes":
            return FakeResponse(content=ROUTES_CSV)
        if name == "sites":
            return FakeResponse(content=SITES_CSV)
        if name == "weather":
            hours = pd.date_range(params["start_date"], f"{params['end_date']} 23:00", freq="h")
            n = len(hours)
            return FakeResponse({"hourly": {"time": hours.strftime("%Y-%m-%dT%H:%M").tolist(),
                                            "precipitation": [0.0] * n, "snowfall": [0.0] * n,
                                            "temperature_2m": [10.0] * n}})
        rows = self.incidents.get(params["$where"].split("'")[1][:7], [])
        if params.get("$select") == "count(*)":
            return FakeResponse([{"count": str(len(rows))}])
        return FakeResponse(rows[params["$offset"]:params["$offset"] + params["$limit"]])


@pytest.fixture
def internet(cfg, monkeypatch):
    fake = FakeInternet(cfg, {"2025-10": month_rows("2025-10"), "2025-11": month_rows("2025-11", 4)})
    monkeypatch.setattr(requests, "get", fake.get)
    return fake


_ids = count()


def run(tmp_path, cfg, month="2025-10", force=False, run_id=None):
    return run_month(month, cfg, run_id or f"run{next(_ids):03d}", force=force, root=tmp_path, now=NOW)


def outputs(tmp_path, month="2025-10"):
    folder = tmp_path / "data" / "output" / month
    return {name: (folder / f"{name}_{month}.csv").read_bytes() for name in OUTPUT_FILES}


def test_full_run_succeeds_and_writes_manifest(tmp_path, cfg, internet):
    m = run(tmp_path, cfg)
    assert m["status"] == "success" and m["halted_stage"] is None
    assert {s["status"] for s in m["sources"].values()} == {"ok"}
    assert m["validation"]["rows"] == 5 and all(c["passed"] for c in m["model"]["checks"].values())

    out = tmp_path / "data" / "output" / "2025-10"
    assert {p.name for p in out.iterdir()} == {
        "quality_report_2025-10.csv", "metrics_2025-10.csv", "vendor_scorecard_2025-10.csv",
        "vendor_scorecard_2025-10.md", "run_manifest.json"}
    saved = json.loads((out / "run_manifest.json").read_text())
    assert saved["run_id"] == m["run_id"] and saved["finished_at"]
    assert (tmp_path / "logs" / "manifests" / f"run_{m['run_id']}_2025-10.json").exists()


def test_rerun_and_forced_rerun_give_identical_outputs(tmp_path, cfg, internet):
    run(tmp_path, cfg)
    first = outputs(tmp_path)

    second = run(tmp_path, cfg)                                   # reuses raw pulls: no incident download
    assert second["sources"]["incidents"]["status"] == "reused"
    assert outputs(tmp_path) == first

    third = run(tmp_path, cfg, force=True)                        # downloads everything again
    assert third["sources"]["incidents"]["status"] == "ok"
    assert outputs(tmp_path) == first
    assert third["model"]["table_counts"]["fact_incident"] == 5


def test_incident_api_down_halts_without_outputs(tmp_path, cfg, internet):
    internet.down.add("incidents")
    m = run(tmp_path, cfg)
    assert m["status"] == "failed" and m["halted_stage"] == "ingest" and "503" in m["error"]
    assert internet.calls["incidents"] == 4                        # 1 try + 3 retries
    assert not (tmp_path / "data" / "output" / "2025-10").exists()
    assert (tmp_path / "logs" / "manifests" / f"run_{m['run_id']}_2025-10.json").exists()


def test_failed_rerun_keeps_previous_good_outputs(tmp_path, cfg, internet):
    good = run(tmp_path, cfg)
    before = outputs(tmp_path)
    internet.down.add("incidents")
    assert run(tmp_path, cfg, force=True)["status"] == "failed"

    assert outputs(tmp_path) == before                             # old outputs untouched...
    saved = json.loads((tmp_path / "data" / "output" / "2025-10" / "run_manifest.json").read_text())
    assert saved["run_id"] == good["run_id"]                       # ...and still described by their own manifest


def test_weather_down_degrades_but_completes(tmp_path, cfg, internet):
    internet.down.add("weather")
    m = run(tmp_path, cfg)
    assert m["status"] == "degraded" and any(n.startswith("weather: failed") for n in m["notes"])
    assert m["validation"]["rules"]["V12"]["status"] == "skipped"
    md = (tmp_path / "data" / "output" / "2025-10" / "vendor_scorecard_2025-10.md").read_text(encoding="utf-8")
    assert "**Run status:** DEGRADED" in md


def test_range_continues_after_a_failed_month(tmp_path, cfg, internet):
    statuses = [run(tmp_path, cfg, month=m, run_id="backfill")["status"]
                for m in ["2025-10", "2025-12", "2025-11"]]           # December has no incidents -> halt
    assert statuses == ["success", "failed", "success"]


def test_forced_backfill_downloads_reference_once_per_school_year(tmp_path, cfg, internet):
    for m in ["2025-10", "2025-11"]:
        assert run(tmp_path, cfg, month=m, force=True, run_id="backfill")["status"] == "success"
    assert internet.calls["routes"] == 1 and internet.calls["sites"] == 1


def test_unexpected_bug_is_recorded_not_crashed(tmp_path, cfg, internet, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(pipeline, "run_model", broken)
    m = run(tmp_path, cfg)
    assert m["status"] == "failed" and m["halted_stage"] == "model"
    assert m["error"] == "unexpected RuntimeError: boom"


@pytest.mark.parametrize("argv", [["--range", "2025-12", "2025-10"], ["--month", "2025-13"], []])
def test_bad_arguments_rejected_before_any_work(argv):
    with pytest.raises(SystemExit) as e:
        pipeline.main(argv)
    assert e.value.code == 2


def test_manifest_paths_are_relative_to_project(tmp_path, cfg, internet):
    run(tmp_path, cfg)
    text = (tmp_path / "data" / "output" / "2025-10" / "run_manifest.json").read_text()
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text
    saved = json.loads(text)
    assert saved["validation"]["validated_path"] == "data/processed/incidents/2025-10/incidents_validated.parquet"


def test_cross_month_summary_lists_halted_months_and_repeat_audits(tmp_path, cfg, internet):
    from src.summary import build_summary
    cfg["metrics"]["min_incidents_per_vendor"] = 1
    for m in ["2025-10", "2025-11", "2025-12"]:                          # December halts (no incidents)
        run(tmp_path, cfg, month=m, run_id="backfill")
    paths = build_summary(["2025-10", "2025-11", "2025-12"], tmp_path / "data" / "output", min_months_ranked=1)

    trend = pd.read_csv(paths["trend_csv"])
    assert trend["month"].tolist() == ["2025-10", "2025-11", "2025-12"]
    assert trend["status"].tolist() == ["success", "success", "no outputs (halted)"]
    assert trend["incidents"].tolist()[:2] == [5, 4]

    repeat = pd.read_csv(paths["repeat_csv"])
    assert repeat.loc[repeat["vendor_key"] == "AA", "months_ranked"].item() == 2

    md = open(paths["md"], encoding="utf-8").read()
    assert "2 of 3 months produced outputs" in md and "2025-12" in md


def test_summer_month_has_scope_note_and_no_m4(tmp_path, cfg, internet):
    internet.incidents["2025-07"] = month_rows("2025-07")
    m = run(tmp_path, cfg, month="2025-07")
    assert m["status"] == "success" and m["scope_notes"] and not m["notes"]
    sc = pd.read_csv(tmp_path / "data" / "output" / "2025-07" / "vendor_scorecard_2025-07.csv")
    assert sc["m4_incidents_per_100_routes"].isna().all()
    assert (sc["vendor_key"] == "AA").any()                               # still attributed, by name
    md = (tmp_path / "data" / "output" / "2025-07" / "vendor_scorecard_2025-07.md").read_text(encoding="utf-8")
    assert "summer service" in md
