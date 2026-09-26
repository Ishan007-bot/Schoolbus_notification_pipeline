import pandas as pd
import pytest

from src.metrics import metric_value, run_metrics
from tests.test_model import load_month
from tests.test_validate import incident


def scenario():
    """AA: 1 contracted route, BB: 2 routes, PreK Co: not in contract data."""
    aa = [incident(1), incident(2, reason="Mechanical Problem"),
          incident(3, has_contractor_notified_parents="No"), incident(4, has_contractor_notified_parents="No"),
          incident(5, school_year="2024-2025", has_contractor_notified_parents="No")]        # invalid (V04)
    bb = [incident(i, route_number="K200", bus_company_name="BETA BUS") for i in (10, 11, 12)]
    bb.append(incident(13, route_number="K200", bus_company_name="BETA BUS",
                       created_on="2025-10-10T07:05:00.000"))                              # V03: untrusted
    prek = [incident(i, route_number="PK1", bus_company_name="PreK Co",
                     has_contractor_notified_parents="No") for i in (20, 21)]               # V08 only
    return aa + bb + prek


@pytest.fixture
def outputs(tmp_path, cfg):
    cfg["metrics"]["min_incidents_per_vendor"] = 1
    load_month(tmp_path, cfg, "2025-10", scenario())
    result = run_metrics("2025-10", tmp_path / "warehouse.duckdb", cfg, tmp_path / "output")
    scorecard = pd.read_csv(result["paths"]["scorecard_csv"]).set_index("vendor_key")
    system = pd.read_csv(result["paths"]["metrics"])
    return result, scorecard, system


def test_m1_excludes_invalid_rows(outputs):
    _, sc, system = outputs
    assert sc.loc["AA", "m1_parent_notified_rate"] == 0.5          # 2 of 4 valid; the invalid "No" is ignored
    assert sc.loc["BB", "m1_parent_notified_rate"] == 1.0
    assert metric_value(system, "M1", "all") == 0.6                # 6 of 10 valid incidents


def test_m3_student_minutes_uses_bucket_midpoint(outputs):
    _, sc, _ = outputs
    assert sc.loc["AA", "m3_student_minutes"] == 4 * 23 * 3        # 4 valid late incidents x 23 min x 3 students
    assert sc.loc["AA", "m3_student_minutes_low"] == 4 * 16 * 3
    assert sc.loc["AA", "m3_student_minutes_high"] == 4 * 30 * 3


def test_m4_uses_all_contracted_routes(outputs):
    _, sc, _ = outputs
    assert sc.loc["AA", "contracted_routes"] == 1 and sc.loc["BB", "contracted_routes"] == 2
    assert sc.loc["AA", "m4_incidents_per_100_routes"] == 400      # 4 valid / 1 route
    assert sc.loc["BB", "m4_incidents_per_100_routes"] == 200      # 4 valid / 2 routes (Q300 had no incidents)
    assert sc.loc["AA", "m4_controllable_per_100_routes"] == 100   # 1 mechanical problem
    assert pd.isna(sc.loc["NAME:PREK CO", "m4_incidents_per_100_routes"])


def test_m5_counts_invalid_rows_and_only_vendor_behaviour_warnings(outputs):
    _, sc, _ = outputs
    assert sc.loc["AA", "m5_trust_score"] == 0.8                   # invalid row lowers trust
    assert sc.loc["BB", "m5_trust_score"] == 0.75                  # V03 late logging
    assert sc.loc["NAME:PREK CO", "m5_trust_score"] == 1.0         # V08 is OPT's data gap, not distrust


def test_audit_triggers(outputs):
    result, sc, _ = outputs
    assert sc.loc["AA", "audit_triggers"] == "M4;M5"               # most controllable incidents per route; trust 80%
    assert sc.loc["BB", "audit_triggers"] == "M5"
    assert sc.loc["NAME:PREK CO", "audit_triggers"] == "M1"        # bottom quartile notification
    assert len(result["audit_list"]) == 3


def test_small_vendors_are_not_ranked(tmp_path, cfg):
    load_month(tmp_path, cfg, "2025-10", scenario())               # default minimum: 30 incidents
    result = run_metrics("2025-10", tmp_path / "warehouse.duckdb", cfg, tmp_path / "output")
    sc = pd.read_csv(result["paths"]["scorecard_csv"])
    assert not sc["ranked"].any() and not sc["audit"].any()


def test_outputs_identical_on_rerun(tmp_path, cfg):
    load_month(tmp_path, cfg, "2025-10", scenario())
    first = run_metrics("2025-10", tmp_path / "warehouse.duckdb", cfg, tmp_path / "output")
    before = {k: open(p, "rb").read() for k, p in first["paths"].items()}
    load_month(tmp_path, cfg, "2025-10", scenario())
    second = run_metrics("2025-10", tmp_path / "warehouse.duckdb", cfg, tmp_path / "output")
    assert {k: open(p, "rb").read() for k, p in second["paths"].items()} == before


def test_scorecard_markdown_shows_run_status_and_notes(tmp_path, cfg):
    load_month(tmp_path, cfg, "2025-10", scenario())
    result = run_metrics("2025-10", tmp_path / "warehouse.duckdb", cfg, tmp_path / "output",
                         run_status="degraded", run_notes=["weather: failed (timeout)"])
    md = open(result["paths"]["scorecard_md"], encoding="utf-8").read()
    assert "**Run status:** DEGRADED" in md and "- weather: failed (timeout)" in md
