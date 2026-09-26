from src.ingest.weather import ingest_weather, load_weather
from src.utils.http import HTTPFailure
from tests.conftest import FakeResponse


def weather(hours, missing=0):
    precip = [0.0] * hours
    for i in range(missing):
        precip[i] = None
    return {"hourly": {"time": [f"t{i}" for i in range(hours)], "precipitation": precip,
                       "snowfall": [0.0] * hours, "temperature_2m": [10.0] * hours}}


def serve(payload):
    return lambda url, params=None, http_cfg=None: FakeResponse(payload)


OCT = 24 * (31 + 3)          # October plus the 3 look-back days before it


def test_full_month_ok(cfg, tmp_path):
    r = ingest_weather("2025-10", cfg, tmp_path, "run1", get=serve(weather(OCT)))
    assert r.status == "ok" and r.rows == OCT
    assert len(load_weather(r.raw_dir)) == OCT


def test_pull_starts_before_the_month(cfg, tmp_path):
    seen = {}

    def get(url, params=None, http_cfg=None):
        seen.update(params)
        return FakeResponse(weather(OCT))
    ingest_weather("2025-10", cfg, tmp_path, "run1", get=get)
    assert seen["start_date"] == "2025-09-28" and seen["end_date"] == "2025-10-31"


def test_pull_with_other_lookback_is_not_reused(cfg, tmp_path):
    cfg["validation"]["snow_lookback_days"] = 0
    ingest_weather("2025-10", cfg, tmp_path, "run1", get=serve(weather(744)))
    cfg["validation"]["snow_lookback_days"] = 3
    assert ingest_weather("2025-10", cfg, tmp_path, "run2", get=serve(weather(OCT))).status == "ok"


def test_dst_month_allows_one_extra_hour(cfg, tmp_path):
    assert ingest_weather("2025-11", cfg, tmp_path, "run1", get=serve(weather(24 * 33 + 1))).status == "ok"


def test_partial_month_is_failed_not_halted(cfg, tmp_path):
    r = ingest_weather("2025-10", cfg, tmp_path, "run1", get=serve(weather(600)))
    assert r.status == "failed" and "hour_count" in r.message


def test_missing_values_fail(cfg, tmp_path):
    assert ingest_weather("2025-10", cfg, tmp_path, "run1", get=serve(weather(OCT, missing=5))).status == "failed"


def test_network_failure_is_reported(cfg, tmp_path):
    def down(url, params=None, http_cfg=None):
        raise HTTPFailure("timeout")
    assert ingest_weather("2025-10", cfg, tmp_path, "run1", get=down).status == "failed"
