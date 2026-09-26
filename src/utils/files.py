"""Atomic writes: write to a temp file, then rename. A crash never leaves a half-written output."""
import os
from pathlib import Path


def atomic_write(path, write_fn):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write_fn(tmp)
    os.replace(tmp, path)
    return path
