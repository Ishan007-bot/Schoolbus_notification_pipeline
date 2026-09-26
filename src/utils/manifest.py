"""Run manifest: a JSON record of what one pipeline run did, for one month.

Written for EVERY run to logs/manifests/ (success, degraded or failed).
Also written to data/output/<month>/run_manifest.json - but only when the run produced
outputs, so the manifest in an output folder always describes the files next to it.
"""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.config import CONFIG_PATH, REPO_ROOT, school_year_for_month
from src.utils.files import atomic_write


def new_manifest(run_id, month, force):
    return {
        "run_id": run_id,
        "period": month,
        "school_year": school_year_for_month(month),
        "force": force,
        "started_at": _now(),
        "finished_at": None,
        "status": "running",          # -> success | degraded | failed
        "halted_stage": None,
        "error": None,
        "notes": [],                  # degraded-run reasons
        "scope_notes": [],            # how to read the month (e.g. summer service)
        "code_version": code_version(),
        "config_sha256": hashlib.sha256(Path(CONFIG_PATH).read_bytes()).hexdigest()[:16],
        "sources": {},
        "validation": {},
        "model": {},
        "metrics": {},
    }


def write_manifest(manifest, logs_dir, output_dir, root=REPO_ROOT):
    manifest["finished_at"] = _now()
    # Paths are stored relative to the project root: portable, and no local user folder in a public repo.
    text = json.dumps(_relative_paths(manifest, Path(root)), indent=2, default=str)
    written = [atomic_write(Path(logs_dir) / "manifests" / f"run_{manifest['run_id']}_{manifest['period']}.json",
                            lambda p: Path(p).write_text(text, encoding="utf-8"))]
    if manifest["status"] != "failed":
        written.append(atomic_write(Path(output_dir) / manifest["period"] / "run_manifest.json",
                                    lambda p: Path(p).write_text(text, encoding="utf-8")))
    return [str(p) for p in written]


def code_version():
    """Git commit the run used, and whether there were uncommitted changes."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        return {"commit": git("rev-parse", "--short", "HEAD") or None, "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def _relative_paths(obj, root):
    if isinstance(obj, dict):
        return {k: _relative_paths(v, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_relative_paths(v, root) for v in obj]
    if isinstance(obj, str):
        out = obj
        for prefix in {str(root), str(root.resolve())}:
            for form in {prefix, prefix.replace("\\", "/")}:
                out = out.replace(form + "\\", "").replace(form + "/", "").replace(form, ".")
        return out.replace("\\", "/") if out != obj else obj
    return obj


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
