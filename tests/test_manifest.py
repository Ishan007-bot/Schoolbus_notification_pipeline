import shutil
import subprocess

import pytest

from src.utils.manifest import code_version

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "data" / "output").mkdir(parents=True)
    (tmp_path / "src" / "code.py").write_text("x = 1\n")
    (tmp_path / "data" / "output" / "run_manifest.json").write_text("{}\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_clean_repo_is_not_dirty(repo):
    version = code_version(repo)
    assert version["commit"] and version["dirty"] is False


def test_pipeline_outputs_do_not_make_it_dirty(repo):
    (repo / "data" / "output" / "run_manifest.json").write_text('{"run_id": "new"}\n')
    (repo / "data" / "output" / "metrics_2025-10.csv").write_text("a,b\n")
    assert code_version(repo)["dirty"] is False


def test_code_change_makes_it_dirty(repo):
    (repo / "src" / "code.py").write_text("x = 2\n")
    assert code_version(repo)["dirty"] is True
