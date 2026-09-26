"""One log file per run (logs/run_<run_id>.log) plus the same lines on the console."""
import logging
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def setup_logging(run_id, logs_dir):
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run_{run_id}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    for handler in (logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(logging.Formatter(FORMAT, datefmt="%H:%M:%S"))
        root.addHandler(handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return log_path
