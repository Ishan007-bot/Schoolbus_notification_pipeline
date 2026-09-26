import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from src.parse_delay import parse_delay, parse_delay_column

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_delay_values.json"


@pytest.mark.parametrize("text, low, high, method, censored", [
    # current dropdown buckets
    ("0-15 Min", 0, 15, "bucket", False),
    ("16-30 Min", 16, 30, "bucket", False),
    ("61-90 Min", 61, 90, "bucket", True),          # top bucket: real delay may be longer
    # legacy free text, exact values with misspelt units
    ("30MINS", 30, 30, "exact", False),
    ("30 MINS.", 30, 30, "exact", False),
    ("30 MINTUES", 30, 30, "exact", False),
    ("20mims", 20, 20, "exact", False),
    ("35 minbs", 35, 35, "exact", False),
    ("25 m ins.", 25, 25, "exact", False),
    ("5MINUTOS.", 5, 5, "exact", False),
    ("30- min", 30, 30, "exact", False),
    ("1 hr", 60, 60, "exact", False),
    ("2 hours", 120, 120, "exact", False),
    ("1HR++", 60, 60, "exact", True),               # "at least an hour"
    # hours followed by minutes = one compound duration
    ("1 hour 15", 75, 75, "exact", False),
    ("1hr/20min", 80, 80, "exact", False),
    ("1hr15min", 75, 75, "exact", False),
    # ranges
    ("20-30 min", 20, 30, "range_midpoint", False),
    ("15/20 mins", 15, 20, "range_midpoint", False),
    ("45 min to 1 hr", 45, 60, "range_midpoint", False),
    ("45mini/1hr", 45, 60, "range_midpoint", False),
    ("40-1HR", 40, 60, "range_midpoint", False),    # 40 > 1, so 40 must be minutes
    ("1-2 hrs", 60, 120, "range_midpoint", False),
    ("30-15 min", 15, 30, "range_midpoint", False), # reversed range is reordered
    ("12-20", 12, 20, "range_midpoint", False),
    # bare numbers
    ("20", 20, 20, "exact_assumed_minutes", False),
])
def test_readable_values(text, low, high, method, censored):
    d = parse_delay(text)
    assert (d.low, d.high, d.method, d.censored) == (low, high, method, censored)
    assert d.est == (low + high) / 2


@pytest.mark.parametrize("text", [
    "2", "2.00", "1-2",          # too small to know if minutes or hours
    "8:07 am", "45 am",          # clock times, not durations
    "heavy flow", "unk", "?????", "MINS",
    "10 20 30 min",              # three numbers
])
def test_unparseable_is_never_guessed(text):
    d = parse_delay(text)
    assert d.method == "unparseable" and d.low is None and d.est is None


@pytest.mark.parametrize("text", [None, "", "   ", float("nan")])
def test_blank_is_missing_not_zero(text):
    d = parse_delay(text)
    assert d.method == "missing" and d.est is None


def test_column_helper_keeps_index_and_types():
    s = pd.Series(["16-30 Min", None, "heavy flow"], index=[10, 11, 12])
    out = parse_delay_column(s)
    assert list(out.index) == [10, 11, 12]
    assert out["delay_parse_method"].tolist() == ["bucket", "missing", "unparseable"]
    assert out.loc[10, "delay_est"] == 23 and pd.isna(out.loc[11, "delay_est"])


def test_coverage_on_real_legacy_values():
    """1,922 distinct free-text values from 2015-18 (~240k rows): almost all must be readable."""
    values = json.loads(FIXTURE.read_text(encoding="utf-8"))["values"]
    rows = Counter()
    for text, n in values:
        rows[parse_delay(text).method] += n
    non_missing = sum(rows.values()) - rows["missing"]
    assert rows["unparseable"] / non_missing < 0.01
