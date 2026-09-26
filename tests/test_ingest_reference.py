import pandas as pd

from src.ingest.reference import ingest_reference, load_reference
from src.utils.http import HTTPFailure
from tests.conftest import FakeResponse

HEADER = "School_Year,OPT_Code,Name,Site_Type,City,Latitude,Longitude\n"


def sites_csv(year_rows, year="2025-2026"):
    lines = [f"{year},{i:05d},School {i},School,Brooklyn,40.7,-74.0" for i in range(year_rows)]
    return (HEADER + "\n".join(lines) + "\n").encode()


def serve(content):
    return lambda url, params=None, http_cfg=None: FakeResponse(content=content)


def fail(url, params=None, http_cfg=None):
    raise HTTPFailure("server down")


def test_ok_and_leading_zeros_preserved(cfg, tmp_path):
    r = ingest_reference("sites", "2025-2026", cfg, tmp_path, "run1", get=serve(sites_csv(3)))
    assert r.status == "ok" and r.rows == 3
    assert load_reference(r.raw_dir, "sites")["OPT_Code"].tolist() == ["00000", "00001", "00002"]


def test_year_not_in_file_is_missing_year(cfg, tmp_path):
    r = ingest_reference("sites", "2026-2027", cfg, tmp_path, "run1", get=serve(sites_csv(3)))
    assert r.status == "missing_year" and r.rows == 0


def test_download_failure_without_copy_fails(cfg, tmp_path):
    assert ingest_reference("sites", "2025-2026", cfg, tmp_path, "run1", get=fail).status == "failed"


def test_download_failure_falls_back_to_previous_copy(cfg, tmp_path):
    ingest_reference("sites", "2025-2026", cfg, tmp_path, "run1", get=serve(sites_csv(3)))
    r = ingest_reference("sites", "2025-2026", cfg, tmp_path, "run2", force=True, get=fail)
    assert r.status == "fallback" and r.rows == 3 and r.raw_dir.endswith("run1")


def test_missing_columns_fall_back(cfg, tmp_path):
    ingest_reference("sites", "2025-2026", cfg, tmp_path, "run1", get=serve(sites_csv(3)))
    broken = b"School_Year,Name\n2025-2026,x\n"
    r = ingest_reference("sites", "2025-2026", cfg, tmp_path, "run2", force=True, get=serve(broken))
    assert r.status == "fallback"


def test_sudden_rowcount_drop_falls_back(cfg, tmp_path):
    ingest_reference("sites", "2025-2026", cfg, tmp_path, "run1", get=serve(sites_csv(100)))
    r = ingest_reference("sites", "2025-2026", cfg, tmp_path, "run2", force=True, get=serve(sites_csv(50)))
    assert r.status == "fallback" and r.rows == 100
