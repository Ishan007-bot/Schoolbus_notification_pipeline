"""Shared pieces for every source: the result record and the write-once raw store.

Raw layout:  data/raw/<source>/<partition>/<run_id>/...
             partition = month (incidents, weather) or school year (routes, sites)

A pull only counts once its folder contains _SUCCESS.json. Folders without it are
failed or incomplete pulls; they're kept as evidence but never reused.
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SUCCESS_FILE = "_SUCCESS.json"


class IngestError(Exception):
    """Retrieval failed or is provably incomplete."""


@dataclass
class SourceResult:
    source: str
    status: str            # ok | reused | fallback | missing_year | failed
    rows: int = 0
    raw_dir: str = ""
    checks: dict = field(default_factory=dict)
    message: str = ""

    def to_dict(self):
        return asdict(self)


def check(passed, detail):
    return {"passed": bool(passed), "detail": detail}


def failed_checks(checks):
    return [name for name, c in checks.items() if not c["passed"]]


def new_run_dir(raw_root, source, partition, run_id):
    path = Path(raw_root) / source / partition / run_id
    path.mkdir(parents=True, exist_ok=False)   # write-once: never reuse a run folder
    return path


def mark_success(run_dir, meta):
    (Path(run_dir) / SUCCESS_FILE).write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


def latest_success(raw_root, source, partition):
    """Most recent successful pull for this source/partition as (folder, meta), or None."""
    base = Path(raw_root) / source / partition
    if not base.exists():
        return None
    for run_dir in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        marker = run_dir / SUCCESS_FILE
        if marker.exists():
            return run_dir, json.loads(marker.read_text(encoding="utf-8"))
    return None


def md5_of(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
