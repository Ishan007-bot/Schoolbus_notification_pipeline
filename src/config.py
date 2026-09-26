"""Loads config.yaml and small helpers for turning a --month argument into dates."""
import calendar
import re
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_month(month):
    """'2025-09' -> (2025, 9). Raises ValueError on anything else."""
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise ValueError(f"month must look like YYYY-MM, got {month!r}")
    year, mon = int(month[:4]), int(month[5:])
    if not 1 <= mon <= 12:
        raise ValueError(f"month number must be 01-12, got {mon:02d}")
    return year, mon


def month_bounds(month):
    """First and last calendar day of the month, as dates."""
    year, mon = parse_month(month)
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def school_year_for_month(month):
    """NYC school years run September to August.

    '2025-09' -> '2025-2026', '2026-03' -> '2025-2026'.
    """
    year, mon = parse_month(month)
    start = year if mon >= 9 else year - 1
    return f"{start}-{start + 1}"
