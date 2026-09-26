"""Turn the how_long_delayed text into minutes.

Since 2018-19 the field is a dropdown with 5 buckets; before that it was free text
("30MINS", "20 mims", "40-1HR", "1 hour 15", "8:07 am", "heavy flow" ...).

Every value becomes:
    low, high   bounds in minutes (equal for an exact value)
    est         the number used in metrics (midpoint of a range)
    method      how the value was read - see METHODS
    censored    True when the real delay may be longer than `high`
                ("61-90 Min" is the top bucket; "1HR++" says "at least")

Nothing is guessed: text that can't be read confidently is `unparseable`,
blank is `missing`, and both leave the numbers empty.
"""
import re
from typing import NamedTuple, Optional

import pandas as pd

METHODS = ("bucket", "exact", "exact_assumed_minutes", "range_midpoint", "unparseable", "missing")

BUCKETS = {
    "0-15 min": (0.0, 15.0),
    "16-30 min": (16.0, 30.0),
    "31-45 min": (31.0, 45.0),
    "46-60 min": (46.0, 60.0),
    "61-90 min": (61.0, 90.0),
}
TOP_BUCKET = "61-90 min"

# Unitless numbers this small are ambiguous: "2" could be 2 minutes or 2 hours.
AMBIGUOUS_UNITLESS_MAX = 5

TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z]+|-")


class Delay(NamedTuple):
    low: Optional[float]
    high: Optional[float]
    est: Optional[float]
    method: str
    censored: bool = False


MISSING = Delay(None, None, None, "missing")
UNPARSEABLE = Delay(None, None, None, "unparseable")


def parse_delay(text):
    if text is None or (isinstance(text, float) and pd.isna(text)) or not str(text).strip():
        return MISSING

    s = re.sub(r"\s+", " ", str(text).lower()).strip().rstrip(".,")
    if s in BUCKETS:
        low, high = BUCKETS[s]
        return Delay(low, high, (low + high) / 2, "bucket", censored=(s == TOP_BUCKET))

    if re.search(r"\d{1,2}:\d{2}", s):           # "8:07 am" is a clock time, not a duration
        return UNPARSEABLE

    quantities = _quantities(s)
    if quantities is None or not 1 <= len(quantities) <= 2:
        return UNPARSEABLE

    censored = "+" in s
    if len(quantities) == 1:
        return _single(*quantities[0], censored)
    return _pair(quantities[0], quantities[1], censored)


def _quantities(s):
    """'40-1hr' -> [[40, None], [1, 'hr']].  Returns None for clock times (am/pm)."""
    s = s.replace("/", "-").replace(" to ", "-")
    quantities = []
    for tok in TOKEN.findall(s):
        if tok[0].isdigit():
            quantities.append([float(tok), None])
        elif tok in ("am", "pm"):
            return None
        elif tok[0] in "hm" and quantities and quantities[-1][1] is None:
            # any word starting with h/m after a number is a unit: hr, hrs, hour / min, mins, mintues, mims, minutos
            quantities[-1][1] = "hr" if tok[0] == "h" else "min"
        # dashes and other words ("approx", "total", "ins") carry no number
    return quantities


def _minutes(value, unit):
    return value * 60 if unit == "hr" else value


def _single(value, unit, censored):
    if unit is None:
        if value <= AMBIGUOUS_UNITLESS_MAX:
            return UNPARSEABLE
        return Delay(value, value, value, "exact_assumed_minutes", censored)
    m = _minutes(value, unit)
    return Delay(m, m, m, "exact", censored)


def _pair(first, second, censored):
    (v1, u1), (v2, u2) = first, second

    # "1 hour 15", "1hr/20min": hours followed by minutes is one compound duration
    if u1 == "hr" and u2 in ("min", None):
        m = v1 * 60 + v2
        return Delay(m, m, m, "exact", censored)

    # otherwise it's a range; a missing unit is borrowed from the other side
    if u1 is None and u2 is None and max(v1, v2) <= AMBIGUOUS_UNITLESS_MAX:
        return UNPARSEABLE                        # "1-2": minutes or hours?
    if u1 is None:
        # "40-1hr": 40 is bigger than 1, so it must be minutes, not hours
        u1 = "min" if (u2 == "hr" and v1 > v2) else u2
    if u2 is None:
        u2 = u1
    low, high = sorted((_minutes(v1, u1), _minutes(v2, u2)))
    return Delay(low, high, (low + high) / 2, "range_midpoint", censored)


def parse_delay_column(values):
    """Vectorised: parse each distinct value once, return a DataFrame aligned to `values`."""
    values = pd.Series(values)
    parsed = {v: parse_delay(v) for v in values.dropna().unique()}
    rows = [parsed.get(v, MISSING) if pd.notna(v) else MISSING for v in values]
    out = pd.DataFrame(rows, columns=["delay_low", "delay_high", "delay_est", "delay_parse_method", "delay_censored"],
                       index=values.index)
    return out.astype({"delay_low": "Float64", "delay_high": "Float64", "delay_est": "Float64", "delay_censored": bool})
