from datetime import date

import pytest

from src.ingest import stage
from src.ingest.common import IngestError, SourceResult


@pytest.fixture
def sources(monkeypatch):
    """Replace the four source functions; tests set the status each one returns."""
    statuses = {"incidents": "ok", "routes": "ok", "sites": "ok", "weather": "ok"}
    monkeypatch.setattr(stage, "ingest_incidents", lambda *a, **k: SourceResult("incidents", statuses["incidents"]))
    monkeypatch.setattr(stage, "ingest_reference", lambda name, *a, **k: SourceResult(name, statuses[name]))
    monkeypatch.setattr(stage, "ingest_weather", lambda *a, **k: SourceResult("weather", statuses["weather"]))
    return statuses


def run(cfg, tmp_path, today=date(2026, 9, 27)):
    return stage.run_ingest("2025-10", cfg, tmp_path, "run1", today=today)


def test_all_ok_is_success(cfg, tmp_path, sources):
    assert run(cfg, tmp_path)[0] == "success"


@pytest.mark.parametrize("source,status", [("weather", "failed"), ("sites", "failed"),
                                           ("routes", "fallback"), ("routes", "missing_year")])
def test_supplementary_problems_degrade(cfg, tmp_path, sources, source, status):
    sources[source] = status
    assert run(cfg, tmp_path)[0] == "degraded"


def test_routes_failed_without_fallback_halts(cfg, tmp_path, sources):
    sources["routes"] = "failed"
    with pytest.raises(IngestError, match="vendors"):
        run(cfg, tmp_path)


def test_unfinished_month_degrades(cfg, tmp_path, sources):
    assert run(cfg, tmp_path, today=date(2025, 10, 20))[0] == "degraded"
