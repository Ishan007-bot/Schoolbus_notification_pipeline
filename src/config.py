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


def month_range(first, last):
    """'2025-11', '2026-02' -> ['2025-11', '2025-12', '2026-01', '2026-02']."""
    y, m = parse_month(first)
    end = parse_month(last)
    if (y, m) > end:
        raise ValueError(f"range start {first} is after end {last}")
    months = []
    while (y, m) <= end:
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


# OPT's school year runs July to June (like NYC's fiscal year): summer incidents in July and
# August are labelled with the UPCOMING school year. Found in the data - July 2025 incidents are
# all "2025-2026", July 2024 all "2024-2025" - after first assuming September (rule V04 caught it).
SCHOOL_YEAR_START_MONTH = 7


# In July and August OPT runs summer service with different operators: the Routes table describes
# school-year contracts (same routes are reported by other companies in summer, never in October).
SUMMER_MONTHS = (7, 8)


def is_summer(month):
    return parse_month(month)[1] in SUMMER_MONTHS


def school_year_for_month(month):
    """'2025-07' -> '2025-2026', '2026-06' -> '2025-2026'."""
    year, mon = parse_month(month)
    start = year if mon >= SCHOOL_YEAR_START_MONTH else year - 1
    return f"{start}-{start + 1}"
