import json
from datetime import date, datetime

import pandas as pd
import pytest

from src.ingest.common import SourceResult
from src.validate.prepare import prepare_incidents
from src.validate.rules import apply_rules
from src.validate.stage import ValidationHalt, run_validate

SCHOOL_YEAR = "2025-2026"

ROUTES = pd.DataFrame({
    "School_Year": [SCHOOL_YEAR, SCHOOL_YEAR, SCHOOL_YEAR],
    "Route_Number": ["K100", "K200", "Q300"],
    "Vendor_Code": ["AA", "BB", "BB"],
    "Vendor_Name": ["ALPHA BUS", "BETA BUS", "BETA BUS"],
})
SITE_CODES = {"01001", "02002"}
DAILY_WEATHER = pd.DataFrame({
    "precipitation": [12.0, 0.0, 0.0, 0.0],
    "snowfall":      [0.0, 0.0, 0.0, 0.0],
    "snow_prev":     [0.0, 0.0, 20.0, 0.0],      # 10-20: 20 cm of snow in the previous days
    "temp_min":      [10.0, 10.0, 5.0, -8.0],    # 10-24: deep freeze
}, index=["2025-10-08", "2025-10-14", "2025-10-20", "2025-10-24"])


def incident(i, **overrides):
    row = {
        "busbreakdown_id": str(i), "school_year": SCHOOL_YEAR, "route_number": "K100",
        "run_type": "Special Ed AM Run", "reason": "Heavy Traffic", "schools_serviced": "01001",
        "occurred_on": "2025-10-08T07:00:00.000", "created_on": "2025-10-08T07:05:00.000",
        "informed_on": "2025-10-08T07:05:00.000", "last_updated_on": "2025-10-08T07:05:00.000",
        "bus_company_name": "ALPHA BUS", "how_long_delayed": "16-30 Min",
        "number_of_students_on_the_bus": "3", "breakdown_or_running_late": "Running Late",
        "has_contractor_notified_parents": "Yes", "has_contractor_notified_schools": "Yes",
        "have_you_alerted_opt": "No",
    }
    row.update(overrides)
    return row


def ctx(weather=DAILY_WEATHER, site_codes=SITE_CODES):
    return {
        "start": date(2025, 10, 1), "end": date(2025, 10, 31), "now": datetime(2026, 9, 27),
        "validation": {"max_logging_lag_hours": 24, "delay_minutes_range": [1, 180],
                       "students_on_bus_range": [0, 72], "dry_day_precip_mm": 1.0, "freezing_temp_c": 0.0},
        "routes_year": ROUTES, "site_codes": site_codes, "weather_daily": weather,
    }


def validate(rows, **ctx_overrides):
    raw = pd.DataFrame(rows, dtype="string")
    df = prepare_incidents(raw, ROUTES, SCHOOL_YEAR)
    return apply_rules(df, ctx(**ctx_overrides))


def flagged_ids(df, rule):
    reasons = df["failure_reasons"] + ";" + df["warning_reasons"]
    return df.loc[reasons.str.split(";").apply(lambda ids: rule in ids), "busbreakdown_id"].tolist()


def test_clean_row_passes_everything():
    df, report = validate([incident(1)])
    assert df["is_valid"].all()
    assert df.loc[0, "failure_reasons"] == "" and df.loc[0, "warning_reasons"] == ""
    assert set(report["status"]) == {"ok"}


@pytest.mark.parametrize("rule, bad_row", [
    ("V02", incident(2, created_on="2025-10-08T06:00:00.000")),                    # logged before it happened
    ("V04", incident(2, school_year="2024-2025")),
    ("V05", incident(2, occurred_on="2025-11-02T07:00:00.000", created_on="2025-11-02T07:05:00.000")),
    ("V05", incident(2, occurred_on="not a date")),
])
def test_critical_rules_invalidate_row(rule, bad_row):
    df, _ = validate([incident(1), bad_row])
    assert flagged_ids(df, rule) == ["2"]
    assert df["is_valid"].tolist() == [True, False]


def test_v01_keeps_first_duplicate_valid():
    df, _ = validate([incident(1), incident(1)])
    assert df["is_valid"].tolist() == [True, False]
    assert df["failure_reasons"].tolist() == ["", "V01"]


@pytest.mark.parametrize("rule, bad_row", [
    ("V03", incident(2, created_on="2025-10-10T07:00:00.000")),                    # 48h later
    ("V06", incident(2, how_long_delayed="heavy flow")),
    ("V06", incident(2, how_long_delayed=None)),
    ("V07", incident(2, how_long_delayed="300 min")),
    ("V08", incident(2, route_number="PK999", bus_company_name="PREK CO")),
    ("V09", incident(2, route_number="K200", bus_company_name="SUBCONTRACTOR INC")),
    ("V10", incident(2, number_of_students_on_the_bus="9056")),
    ("V10", incident(2, number_of_students_on_the_bus="abc")),
    ("V11", incident(2, has_contractor_notified_parents="Maybe")),
    ("V12", incident(2, reason="Weather Conditions", occurred_on="2025-10-14T07:00:00.000",
                     created_on="2025-10-14T07:05:00.000")),
    ("V13", incident(2, schools_serviced="01001,99999")),
])
def test_warning_rules_flag_but_keep_row_valid(rule, bad_row):
    df, _ = validate([incident(1), bad_row])
    assert flagged_ids(df, rule) == ["2"]
    assert df["is_valid"].all()


def test_summer_incident_belongs_to_upcoming_school_year():
    summer = {"start": date(2025, 7, 1), "end": date(2025, 7, 31)}
    raw = pd.DataFrame([incident(1, occurred_on="2025-07-15T07:00:00.000", created_on="2025-07-15T07:05:00.000")],
                       dtype="string")                                   # school_year "2025-2026"
    df, _ = apply_rules(prepare_incidents(raw, ROUTES, SCHOOL_YEAR), {**ctx(), **summer})
    assert df.loc[0, "failure_reasons"] == ""


def test_breakdown_without_delay_is_not_flagged():
    df, _ = validate([incident(1, breakdown_or_running_late="Breakdown", how_long_delayed=None)])
    assert df.loc[0, "warning_reasons"] == ""


@pytest.mark.parametrize("day", ["2025-10-08",     # 12 mm of rain
                                 "2025-10-20",     # dry, but snow on the ground from previous days
                                 "2025-10-24",     # dry, but freezing
                                 "2025-10-30"])    # no weather row: no evidence against the vendor
def test_weather_reason_supported(day):
    df, _ = validate([incident(1, reason="Weather Conditions", occurred_on=f"{day}T07:00:00.000",
                               created_on=f"{day}T07:05:00.000")])
    assert df.loc[0, "warning_reasons"] == ""


def test_summer_attribution_ignores_route_contracts():
    raw = pd.DataFrame([incident(1, route_number="K100", bus_company_name="BETA BUS"),    # K100 is AA's in the school year
                        incident(2, route_number="K200", bus_company_name="SUMMER CO")], dtype="string")
    df = prepare_incidents(raw, ROUTES, SCHOOL_YEAR, use_route_contracts=False)
    assert df["vendor_source"].tolist() == ["name_match", "reported_name"]
    assert df["vendor_code"].tolist()[0] == "BB"
    df, _ = apply_rules(df, ctx())
    assert "V09" not in ";".join(df["warning_reasons"])            # no contract to disagree with


def test_rules_skip_when_supporting_source_missing():
    _, report = validate([incident(1)], weather=None, site_codes=None)
    status = report.set_index("rule_id")["status"]
    assert status["V12"] == "skipped" and status["V13"] == "skipped"


def test_vendor_attribution_sources():
    df, _ = validate([
        incident(1),                                                       # route in contract data
        incident(2, route_number="NEW1", bus_company_name="BETA BUS"),     # unknown route, known vendor name
        incident(3, route_number="PK1", bus_company_name="PREK CO"),       # neither
    ])
    assert df["vendor_source"].tolist() == ["routes", "name_match", "reported_name"]
    assert df["vendor_code"].tolist()[:2] == ["AA", "BB"] and pd.isna(df.loc[2, "vendor_code"])


# ---- stage: files, halt, rerun -------------------------------------------------

def write_raw(tmp_path, rows):
    inc = tmp_path / "raw" / "inc"; inc.mkdir(parents=True)
    (inc / "page_000.json").write_text(json.dumps(rows))
    ref = tmp_path / "raw" / "ref"; ref.mkdir()
    ROUTES.to_csv(ref / "routes.csv", index=False)
    pd.DataFrame({"School_Year": [SCHOOL_YEAR] * 2, "OPT_Code": sorted(SITE_CODES)}).to_csv(ref / "sites.csv", index=False)
    return {
        "incidents": SourceResult("incidents", "ok", len(rows), str(inc)),
        "routes": SourceResult("routes", "ok", 3, str(ref)),
        "sites": SourceResult("sites", "ok", 2, str(ref)),
        "weather": SourceResult("weather", "failed"),
    }


def run_stage(tmp_path, results, cfg):
    return run_validate("2025-10", results, cfg, tmp_path / "processed", tmp_path / "output",
                        now=datetime(2026, 9, 27))


def test_stage_writes_outputs_and_rerun_is_identical(tmp_path, cfg):
    results = write_raw(tmp_path, [incident(i) for i in range(10)])
    first = run_stage(tmp_path, results, cfg)
    report_bytes = open(first["report_path"], "rb").read()
    second = run_stage(tmp_path, results, cfg)

    assert open(second["report_path"], "rb").read() == report_bytes
    assert len(pd.read_parquet(second["validated_path"])) == 10
    assert list((tmp_path / "output" / "2025-10").iterdir())[0].name == "quality_report_2025-10.csv"


def test_stage_halts_when_too_many_critical_failures(tmp_path, cfg):
    rows = [incident(i) for i in range(7)] + [incident(i, school_year="1899-1900") for i in range(7, 10)]
    results = write_raw(tmp_path, rows)                                    # 30% critical > 25% threshold
    with pytest.raises(ValidationHalt, match="30.0%"):
        run_stage(tmp_path, results, cfg)
    assert (tmp_path / "output" / "2025-10" / "quality_report_2025-10.csv").exists()   # evidence kept
    assert not (tmp_path / "processed" / "incidents" / "2025-10").exists()             # no metrics input
