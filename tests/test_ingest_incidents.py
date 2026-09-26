import pytest

from src.ingest.common import IngestError
from src.ingest.incidents import ingest_incidents, load_incidents
from tests.conftest import FakeResponse


def row(i, day="2025-10-15"):
    return {"busbreakdown_id": str(i), "occurred_on": f"{day}T07:00:00.000"}


def fake_api(rows, reported_count=None):
    """Mimics Socrata: count(*) query, then $limit/$offset pages."""
    calls = []

    def get(url, params=None, http_cfg=None):
        calls.append(params)
        if params.get("$select") == "count(*)":
            return FakeResponse([{"count": str(len(rows) if reported_count is None else reported_count)}])
        offset, limit = params["$offset"], params["$limit"]
        return FakeResponse(rows[offset:offset + limit])

    get.calls = calls
    return get


def test_complete_pull_paginates_and_marks_success(cfg, tmp_path):
    rows = [row(i) for i in range(5)]
    get = fake_api(rows)
    result = ingest_incidents("2025-10", cfg, tmp_path, "run1", get=get)

    assert result.status == "ok" and result.rows == 5
    assert len(get.calls) == 1 + 3                      # count + pages of 2, 2, 1
    assert len(load_incidents(result.raw_dir)) == 5
    assert all(c["passed"] for c in result.checks.values())


def test_count_mismatch_halts(cfg, tmp_path):
    with pytest.raises(IngestError, match="row_count_matches"):
        ingest_incidents("2025-10", cfg, tmp_path, "run1", get=fake_api([row(1), row(2)], reported_count=3))


def test_duplicate_ids_halt(cfg, tmp_path):
    with pytest.raises(IngestError, match="no_duplicate_ids"):
        ingest_incidents("2025-10", cfg, tmp_path, "run1", get=fake_api([row(1), row(1)]))


def test_row_outside_month_halts(cfg, tmp_path):
    with pytest.raises(IngestError, match="all_inside_month"):
        ingest_incidents("2025-10", cfg, tmp_path, "run1", get=fake_api([row(1), row(2, day="2025-11-01")]))


def test_empty_month_halts(cfg, tmp_path):
    with pytest.raises(IngestError, match="not_empty"):
        ingest_incidents("2025-10", cfg, tmp_path, "run1", get=fake_api([]))


def test_failed_pull_is_never_reused(cfg, tmp_path):
    with pytest.raises(IngestError):
        ingest_incidents("2025-10", cfg, tmp_path, "run1", get=fake_api([row(1)], reported_count=2))
    result = ingest_incidents("2025-10", cfg, tmp_path, "run2", get=fake_api([row(1)]))
    assert result.status == "ok"                        # re-pulled, not reused from the broken run


def test_rerun_reuses_unless_forced(cfg, tmp_path):
    ingest_incidents("2025-10", cfg, tmp_path, "run1", get=fake_api([row(1)]))

    def must_not_call(*a, **k):
        raise AssertionError("network should not be touched when reusing")

    assert ingest_incidents("2025-10", cfg, tmp_path, "run2", get=must_not_call).status == "reused"
    assert ingest_incidents("2025-10", cfg, tmp_path, "run3", force=True, get=fake_api([row(1)])).status == "ok"
