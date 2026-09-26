from datetime import date

import pytest

from src.config import load_config, month_bounds, month_range, school_year_for_month


def test_school_year_starts_in_july():
    # summer incidents belong to the UPCOMING school year (observed in OPT's data)
    assert school_year_for_month("2025-07") == "2025-2026"
    assert school_year_for_month("2025-08") == "2025-2026"
    assert school_year_for_month("2025-09") == "2025-2026"
    assert school_year_for_month("2025-12") == "2025-2026"


def test_school_year_spring_belongs_to_previous_start():
    assert school_year_for_month("2026-01") == "2025-2026"
    assert school_year_for_month("2026-06") == "2025-2026"


def test_month_bounds_handles_month_lengths():
    assert month_bounds("2025-09") == (date(2025, 9, 1), date(2025, 9, 30))
    assert month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))


@pytest.mark.parametrize("bad", ["2025-13", "2025-9", "25-09", "", None])
def test_bad_month_rejected(bad):
    with pytest.raises(ValueError):
        month_bounds(bad)


def test_config_has_all_four_sources():
    assert set(load_config()["sources"]) == {"incidents", "routes", "sites", "weather"}


def test_month_range_crosses_year_end():
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert month_range("2025-10", "2025-10") == ["2025-10"]


def test_month_range_rejects_backwards():
    with pytest.raises(ValueError):
        month_range("2026-02", "2025-11")
