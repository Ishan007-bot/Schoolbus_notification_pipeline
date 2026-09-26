import itertools
import json
from datetime import datetime

import duckdb
import pandas as pd
import pytest

from src import model
from src.ingest.common import SourceResult
from src.model import ModelError, run_model
from src.validate.stage import run_validate
from tests.test_validate import incident

SCHOOL_YEAR = "2025-2026"
ROUTES = pd.DataFrame({
    "School_Year": [SCHOOL_YEAR] * 3, "Route_Number": ["K100", "K200", "Q300"],
    "Service_Type": ["D2D", "S2S AM", "S2S AM"], "Vendor_Code": ["AA", "BB", "BB"],
    "Vendor_Name": ["ALPHA BUS", "BETA BUS", "BETA BUS"],
})
SITES = pd.DataFrame({
    "School_Year": [SCHOOL_YEAR] * 7,
    "OPT_Code": ["01001", "02002", "03003", "04004", "05005", "06006", "07007"],
    "Name": list("ABCDEFG"), "Site_Type": ["School"] * 7, "City": [None] * 7,
    "Zip": ["10002", "10451", "11201", "11004", "11501", "10301", "07030"],
    "Latitude": ["40.7"] * 7, "Longitude": ["-74.0"] * 7,
})
_run = itertools.count()


def load_month(tmp_path, cfg, month, rows, weather_ok=True):
    """Fake raw files -> validate -> model, always into tmp_path/warehouse.duckdb."""
    raw = tmp_path / f"raw{next(_run)}"
    (raw / "inc").mkdir(parents=True)
    (raw / "inc" / "page_000.json").write_text(json.dumps(rows))
    ROUTES.to_csv(raw / "routes.csv", index=False)
    SITES.to_csv(raw / "sites.csv", index=False)
    hours = [f"{month}-01T{h:02d}:00" for h in range(24)]
    (raw / "weather.json").write_text(json.dumps({"hourly": {
        "time": hours, "precipitation": [0.5] * 24, "snowfall": [0.0] * 24, "temperature_2m": [10.0] * 24}}))

    results = {
        "incidents": SourceResult("incidents", "ok", len(rows), str(raw / "inc")),
        "routes": SourceResult("routes", "ok", 3, str(raw)),
        "sites": SourceResult("sites", "ok", 7, str(raw)),
        "weather": SourceResult("weather", "ok" if weather_ok else "failed", 24, str(raw)),
    }
    validated = run_validate(month, results, cfg, tmp_path / "processed", tmp_path / "output",
                             now=datetime(2026, 9, 27))
    return run_model(month, results, validated["validated_path"], tmp_path / "warehouse.duckdb")


def query(tmp_path, sql):
    con = duckdb.connect(str(tmp_path / "warehouse.duckdb"), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def nov(i, **kw):
    return incident(i, occurred_on="2025-11-05T07:00:00.000", created_on="2025-11-05T07:05:00.000", **kw)


def test_loads_star_schema(tmp_path, cfg):
    summary = load_month(tmp_path, cfg, "2025-10", [
        incident(1, schools_serviced="01001,02002"),
        incident(2, route_number="PK1", bus_company_name="PreK Co", reason="Mechanical Problem"),
    ])
    assert summary["period_rows"] == 2 and all(c["passed"] for c in summary["checks"].values())
    assert query(tmp_path, "SELECT count(*) FROM bridge_incident_site")[0][0] == 3   # 2 schools + 1
    assert query(tmp_path, "SELECT count(*) FROM dim_route")[0][0] == 3              # all routes, not only used ones
    assert query(tmp_path, "SELECT vendor_key FROM fact_incident ORDER BY busbreakdown_id") == [
        ("AA",), ("NAME:PREK CO",)]
    assert query(tmp_path, "SELECT vendor_key, in_contract_data FROM dim_vendor ORDER BY 1") == [
        ("AA", True), ("BB", True), ("NAME:PREK CO", False)]


def test_reason_categories_include_route_design(tmp_path, cfg):
    load_month(tmp_path, cfg, "2025-10", [incident(1)])
    cats = dict(query(tmp_path, "SELECT reason, category FROM dim_reason"))
    assert cats["Problem Run"] == "route_design"
    assert cats["Mechanical Problem"] == "vendor_controllable"
    assert cats["Heavy Traffic"] == "external"


def test_unmapped_reason_is_reported(tmp_path, cfg):
    summary = load_month(tmp_path, cfg, "2025-10", [incident(1, reason="Alien Invasion")])
    assert summary["unmapped_reasons"] == ["Alien Invasion"]


def test_borough_derived_from_zip(tmp_path, cfg):
    load_month(tmp_path, cfg, "2025-10", [incident(1)])
    assert dict(query(tmp_path, "SELECT zip, borough FROM dim_site")) == {
        "10002": "Manhattan", "10451": "Bronx", "11201": "Brooklyn", "11004": "Queens",
        "11501": "Outside NYC", "10301": "Staten Island", "07030": "Outside NYC"}


def test_rerun_same_month_does_not_duplicate(tmp_path, cfg):
    rows = [incident(i) for i in range(5)]
    first = load_month(tmp_path, cfg, "2025-10", rows)
    second = load_month(tmp_path, cfg, "2025-10", rows)
    assert first["table_counts"] == second["table_counts"]
    assert first["table_counts"]["fact_incident"] == 5


def test_months_coexist_and_reload_touches_only_its_month(tmp_path, cfg):
    load_month(tmp_path, cfg, "2025-10", [incident(i) for i in range(3)])
    load_month(tmp_path, cfg, "2025-11", [nov(i) for i in range(100, 104)])
    load_month(tmp_path, cfg, "2025-10", [incident(i) for i in range(2)])      # October shrinks on reload

    assert dict(query(tmp_path, "SELECT period, count(*) FROM fact_incident GROUP BY 1")) == {
        "2025-10": 2, "2025-11": 4}
    assert query(tmp_path, "SELECT count(*) FROM dim_hour")[0][0] == 48


def test_duplicate_ids_are_left_out_of_fact(tmp_path, cfg):
    rows = [incident(1), incident(1)] + [incident(i) for i in range(2, 7)]   # 1 of 7 critical, under the halt threshold
    summary = load_month(tmp_path, cfg, "2025-10", rows)
    assert summary["period_rows"] == 6 and summary["duplicates_skipped"] == 1


def test_missing_weather_still_loads(tmp_path, cfg):
    summary = load_month(tmp_path, cfg, "2025-10", [incident(1)], weather_ok=False)
    assert summary["period_rows"] == 1
    assert query(tmp_path, "SELECT count(*) FROM dim_hour")[0][0] == 0


def test_failed_integrity_check_rolls_back(tmp_path, cfg, monkeypatch):
    load_month(tmp_path, cfg, "2025-10", [incident(i) for i in range(3)])

    monkeypatch.setattr(model, "integrity_checks", lambda *a: {"forced": {"passed": False, "detail": "test"}})
    with pytest.raises(ModelError, match="forced"):
        load_month(tmp_path, cfg, "2025-10", [incident(9)])

    assert query(tmp_path, "SELECT busbreakdown_id FROM fact_incident ORDER BY 1") == [("0",), ("1",), ("2",)]
