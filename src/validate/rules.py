"""Business validation rules. Flag, never fix.

Each rule returns a boolean Series (True = row flagged), or None when the rule
can't run because its supporting source is unavailable (reported as "skipped").

critical  the row can't be trusted for metrics  -> is_valid = False
warning   usable, but suspicious; counted per vendor (feeds the trust score)
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

import pandas as pd

WEATHER_REASON = "Weather Conditions"


@dataclass(frozen=True)
class Rule:
    id: str
    level: str
    description: str
    check: Callable


def v01(df, ctx):
    return df["busbreakdown_id"].duplicated(keep="first")


def v02(df, ctx):
    return df["created_on"].isna() | (df["created_on"] < df["occurred_on"])


def v03(df, ctx):
    return df["logging_lag_min"] > ctx["validation"]["max_logging_lag_hours"] * 60


def v04(df, ctx):
    occurred = df["occurred_on"]
    start = occurred.dt.year.where(occurred.dt.month >= 9, occurred.dt.year - 1).astype("Int64")
    expected = start.astype("string") + "-" + (start + 1).astype("string")
    return occurred.notna() & (df["school_year"] != expected)


def v05(df, ctx):
    occurred = df["occurred_on"]
    after_end = pd.Timestamp(ctx["end"]) + timedelta(days=1)
    return occurred.isna() | (occurred < pd.Timestamp(ctx["start"])) | (occurred >= after_end) | (occurred > ctx["now"])


def v06(df, ctx):
    running_late = df["breakdown_or_running_late"] == "Running Late"
    return running_late & df["delay_parse_method"].isin(["unparseable", "missing"])


def v07(df, ctx):
    lo, hi = ctx["validation"]["delay_minutes_range"]
    est = df["delay_est"]
    return est.notna() & ((est < lo) | (est > hi))


def v08(df, ctx):
    return df["vendor_source"] != "routes"


def v09(df, ctx):
    reported = df["bus_company_name"].str.strip().str.upper()
    contract = df["contract_vendor_name"].str.strip().str.upper()
    flagged = contract.notna() & (reported != contract)
    routes = ctx["routes_year"]
    if routes is not None and len(routes):
        multi = routes.groupby("Route_Number")["Vendor_Code"].nunique()
        flagged = flagged | df["route_number"].isin(multi[multi > 1].index)
    return flagged


def v10(df, ctx):
    lo, hi = ctx["validation"]["students_on_bus_range"]
    students = df["students_on_bus"]
    return students.isna() | (students < lo) | (students > hi)


def v11(df, ctx):
    return df[["notified_parents", "notified_schools", "alerted_opt"]].isna().any(axis=1)


def v12(df, ctx):
    daily = ctx["weather_daily"]
    if daily is None:
        return None
    day = df["occurred_on"].dt.strftime("%Y-%m-%d")
    precip = day.map(daily["precipitation"])
    snow = day.map(daily["snowfall"])
    dry = (precip < ctx["validation"]["dry_day_precip_mm"]) & (snow == 0)
    return (df["reason"] == WEATHER_REASON) & dry


def v13(df, ctx):
    codes = ctx["site_codes"]
    if codes is None:
        return None
    exploded = df["schools_serviced"].str.split(",").explode().str.strip()
    unknown = ~exploded.isin(codes) & exploded.notna() & (exploded != "")
    return unknown.groupby(level=0).any().reindex(df.index, fill_value=False)


RULES = [
    Rule("V01", "critical", "busbreakdown_id is unique (later duplicates flagged)", v01),
    Rule("V02", "critical", "incident logged at or after it occurred (created_on >= occurred_on)", v02),
    Rule("V03", "warning", "logged within max_logging_lag_hours of occurring", v03),
    Rule("V04", "critical", "school_year matches the occurred_on date", v04),
    Rule("V05", "critical", "occurred_on inside the requested month and not in the future", v05),
    Rule("V06", "warning", "Running Late incident has a readable delay", v06),
    Rule("V07", "warning", "parsed delay within delay_minutes_range", v07),
    Rule("V08", "warning", "vendor attributable from Routes contract data (else name fallback)", v08),
    Rule("V09", "warning", "reported company matches the contracted vendor for the route", v09),
    Rule("V10", "warning", "students on bus is a number within students_on_bus_range", v10),
    Rule("V11", "warning", "notification flags are Yes/No", v11),
    Rule("V12", "warning", "'Weather Conditions' reason on a day with measurable rain or snow", v12),
    Rule("V13", "warning", "every schools_serviced code exists in Transportation Sites", v13),
]


def apply_rules(df, ctx):
    """Adds is_valid, failure_reasons, warning_reasons. Returns (df, per-rule report)."""
    flags, report = {}, []
    for rule in RULES:
        result = rule.check(df, ctx)
        if result is None:
            report.append({"rule_id": rule.id, "level": rule.level, "description": rule.description,
                           "status": "skipped", "rows_flagged": 0, "pct_flagged": 0.0})
            continue
        flags[rule.id] = result.fillna(False).astype(bool)
        n = int(flags[rule.id].sum())
        report.append({"rule_id": rule.id, "level": rule.level, "description": rule.description,
                       "status": "flagged" if n else "ok", "rows_flagged": n,
                       "pct_flagged": round(n / len(df) * 100, 2) if len(df) else 0.0})

    flagged = pd.DataFrame(flags, index=df.index)
    critical = [r.id for r in RULES if r.level == "critical" and r.id in flagged]
    warning = [r.id for r in RULES if r.level == "warning" and r.id in flagged]

    df = df.copy()
    df["failure_reasons"] = _join_ids(flagged, critical)
    df["warning_reasons"] = _join_ids(flagged, warning)
    df["is_valid"] = ~flagged[critical].any(axis=1)
    return df, pd.DataFrame(report)


def _join_ids(flagged, ids):
    out = pd.Series("", index=flagged.index, dtype="string")
    for rule_id in ids:
        out = out.where(~flagged[rule_id], out + rule_id + ";")
    return out.str.rstrip(";")
