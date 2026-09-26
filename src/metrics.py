"""Metrics stage: M1-M5 per vendor and system-wide, the audit rule, and the output files.

Outputs in data/output/<month>/ (overwritten atomically; rows in a fixed order so a rerun is byte-identical):
  metrics_<month>.csv            system-wide metrics, one row per metric x segment
  vendor_scorecard_<month>.csv   one row per vendor, with the audit decision and what triggered it
  vendor_scorecard_<month>.md    the human-readable version for OPT
"""
import logging
import re
from pathlib import Path

import duckdb
import pandas as pd

from src.config import is_summer, school_year_for_month
from src.utils.files import atomic_write

log = logging.getLogger("metrics")

SQL_DIR = Path(__file__).parent / "sql"
RULE_ID = re.compile(r"^V\d{2}$")

M1, M2, M2Q, M3, M4, M4C, M5 = ("m1_parent_notified_rate", "m2_median_logging_lag_min", "m2_logged_quickly_rate",
                                 "m3_student_minutes", "m4_incidents_per_100_routes",
                                 "m4_controllable_per_100_routes", "m5_trust_score")


def run_metrics(month, warehouse_path, cfg, output_root, run_status="success", run_notes=()):
    mcfg = cfg["metrics"]
    con = duckdb.connect(str(warehouse_path))
    try:
        build_period_tables(con, month, mcfg)
        scorecard = con.execute((SQL_DIR / "vendor_scorecard.sql").read_text(encoding="utf-8")).df()
        scorecard, thresholds = apply_audit_rule(scorecard, mcfg)
        system = system_metrics(con, scorecard, mcfg)
    finally:
        con.close()

    scorecard = scorecard.round(4)
    out = Path(output_root) / month
    paths = {
        "metrics": atomic_write(out / f"metrics_{month}.csv", lambda p: system.to_csv(p, index=False)),
        "scorecard_csv": atomic_write(out / f"vendor_scorecard_{month}.csv", lambda p: scorecard.to_csv(p, index=False)),
    }
    markdown = render_scorecard(month, run_status, run_notes, system, scorecard, thresholds, mcfg)
    paths["scorecard_md"] = atomic_write(out / f"vendor_scorecard_{month}.md",
                                         lambda p: Path(p).write_text(markdown, encoding="utf-8"))

    audited = (scorecard.loc[scorecard["audit"], "vendor_name"] + " [" +
               scorecard.loc[scorecard["audit"], "vendor_key"] + "]").tolist()
    log.info("M1 parents notified (claimed): %.1f%% | M5 trust: %.1f%% | vendors ranked: %d | audit list: %d",
             metric_value(system, "M1", "all") * 100, metric_value(system, "M5", "all") * 100,
             int(scorecard["ranked"].sum()), len(audited))
    for name in audited:
        log.info("  audit: %s", name)
    headline = {metric_id.lower(): metric_value(system, metric_id, segment)
                for metric_id, segment in [("M1", "all"), ("M5", "all")]}
    return {"paths": {k: str(v) for k, v in paths.items()}, "audit_list": audited,
            "vendors_ranked": int(scorecard["ranked"].sum()), "headline": headline}


def build_period_tables(con, month, mcfg):
    rules = mcfg["trust_warning_rules"]
    if not all(RULE_ID.match(r) for r in rules):
        raise ValueError(f"bad rule id in trust_warning_rules: {rules}")
    trust_rules = "[" + ", ".join(f"'{r}'" for r in rules) + "]"

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE period_incidents AS
        SELECT *,
               is_valid AND NOT list_has_any(warns, {trust_rules})                    AS trusted,
               is_valid AND incident_type = 'Running Late' AND delay_est IS NOT NULL
                   AND students_on_bus IS NOT NULL AND NOT list_has_any(warns, ['V07', 'V10'])
                                                                                   AS m3_eligible
        FROM (
            SELECT f.*, coalesce(r.category, 'unknown')                  AS reason_category,
                   string_split(coalesce(f.warning_reasons, ''), ';')   AS warns,
                   f.logging_lag_min <= ?                               AS logged_quickly
            FROM fact_incident f LEFT JOIN dim_reason r USING (reason)
            WHERE f.period = ?
        )""", [mcfg["logged_within_minutes"], month])
    # Summer: route contracts describe the school year, so there is no fair route denominator -> M4 is n/a.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE period_routes AS
        SELECT vendor_code, count(*) AS contracted_routes FROM dim_route
        WHERE school_year = ? AND NOT ? GROUP BY 1""", [school_year_for_month(month), is_summer(month)])


def apply_audit_rule(scorecard, mcfg):
    """A ranked vendor (>= min incidents) goes on the audit list if ANY trigger fires:
         M1  parent notification rate in the bottom quartile of ranked vendors
         M4  VENDOR-CONTROLLABLE incidents per 100 routes in the top quartile (contract vendors only).
             All-cause M4 is reported but not used: a vendor shouldn't be audited for traffic.
         M5  trust score below the configured floor
    Quartiles are relative, so some vendors are always named - the scorecard prints the cut-offs."""
    sc = scorecard.copy()
    q = mcfg["audit_quartile"]
    sc["ranked"] = sc["incidents"] >= mcfg["min_incidents_per_vendor"]
    ranked = sc[sc["ranked"]]

    thresholds = {
        "m1_at_or_below": ranked[M1].quantile(q) if len(ranked) else float("nan"),
        "m4c_at_or_above": ranked[M4C].dropna().quantile(1 - q) if ranked[M4C].notna().any() else float("nan"),
        "m5_below": mcfg["audit_trust_score_below_pct"] / 100,
    }
    fired = pd.DataFrame({
        "M1": sc["ranked"] & (sc[M1] <= thresholds["m1_at_or_below"]),
        # "> 0" so a quartile cut of 0 doesn't flag vendors with no controllable incidents at all
        "M4": sc["ranked"] & sc[M4C].notna() & (sc[M4C] >= thresholds["m4c_at_or_above"]) & (sc[M4C] > 0),
        "M5": sc["ranked"] & (sc[M5] < thresholds["m5_below"]),
    }).fillna(False)

    sc["audit"] = fired.any(axis=1)
    sc["audit_triggers"] = fired.apply(lambda r: ";".join(k for k, v in r.items() if v), axis=1)
    return sc, thresholds


def system_metrics(con, scorecard, mcfg):
    t = con.execute("""
        SELECT count(*)                                                          AS incidents,
               count(*) FILTER (WHERE is_valid)                                  AS valid,
               count(*) FILTER (WHERE is_valid AND notified_parents)             AS m1_num,
               count(*) FILTER (WHERE is_valid AND notified_parents IS NOT NULL) AS m1_den,
               median(logging_lag_min) FILTER (WHERE is_valid)                   AS m2_median,
               count(*) FILTER (WHERE is_valid AND logged_quickly)               AS m2_quick,
               coalesce(sum(delay_est  * students_on_bus) FILTER (WHERE m3_eligible), 0) AS m3,
               coalesce(sum(delay_low  * students_on_bus) FILTER (WHERE m3_eligible), 0) AS m3_low,
               coalesce(sum(delay_high * students_on_bus) FILTER (WHERE m3_eligible), 0) AS m3_high,
               count(*) FILTER (WHERE is_valid AND incident_type = 'Running Late')                    AS late,
               count(*) FILTER (WHERE is_valid AND incident_type = 'Running Late' AND delay_censored) AS censored,
               count(*) FILTER (WHERE trusted)                                   AS trusted,
               count(*) FILTER (WHERE vendor_source = 'routes')                  AS via_routes
        FROM period_incidents""").df().iloc[0]

    rows = []

    def add(metric_id, metric, segment, value, numerator=None, denominator=None, note=""):
        rows.append({"metric_id": metric_id, "metric": metric, "segment": segment,
                     "value": None if pd.isna(value) else round(float(value), 4),
                     "numerator": numerator, "denominator": denominator, "note": note})

    def rate(n, d):
        return n / d if d else float("nan")

    add("CTX", "incidents", "all", t.incidents)
    add("CTX", "valid incidents", "all", t.valid, int(t.valid), int(t.incidents), "rows passing all critical rules")
    add("CTX", "attributed to vendor via contract routes", "all", rate(t.via_routes, t.incidents),
        int(t.via_routes), int(t.incidents), "the rest use the reported company name (mostly Pre-K)")

    add("M1", "parent notification rate", "all", rate(t.m1_num, t.m1_den), int(t.m1_num), int(t.m1_den),
        "vendor-claimed Yes/No flag; no timestamp exists for when parents were told")
    for r in con.execute("""
            SELECT run_type, count(*) FILTER (WHERE notified_parents) AS n,
                   count(*) FILTER (WHERE notified_parents IS NOT NULL) AS d
            FROM period_incidents WHERE is_valid GROUP BY 1 ORDER BY 1""").fetchall():
        add("M1", "parent notification rate", r[0], rate(r[1], r[2]), r[1], r[2])

    add("M2", "median logging lag (minutes)", "all", t.m2_median, note="created_on minus occurred_on")
    add("M2", f"logged within {mcfg['logged_within_minutes']} minutes", "all", rate(t.m2_quick, t.valid),
        int(t.m2_quick), int(t.valid))

    censored_share = rate(t.censored, t.late)
    add("M3", "student-minutes delayed", "all", t.m3,
        note=f"lower bound; range {t.m3_low:,.0f}-{t.m3_high:,.0f}; {censored_share:.1%} of late incidents "
             f"are in the open-ended top bucket (61-90 Min)")
    add("M3", "late incidents in top delay bucket", "all", censored_share, int(t.censored), int(t.late),
        "real delay may exceed 90 minutes")

    contract = scorecard[scorecard["contracted_routes"].notna()]
    routes, incidents = contract["contracted_routes"].sum(), contract["valid_incidents"].sum()
    add("M4", "incidents per 100 contracted routes", "contract vendors", rate(incidents * 100, routes),
        int(incidents), int(routes))
    for r in con.execute("""
            SELECT reason_category, count(*) FROM period_incidents WHERE is_valid GROUP BY 1 ORDER BY 1""").fetchall():
        add("M4", "share of incidents by reason category", r[0], rate(r[1], t.valid), r[1], int(t.valid))

    add("M5", "data-trust score", "all", rate(t.trusted, t.incidents), int(t.trusted), int(t.incidents),
        f"valid and none of {','.join(mcfg['trust_warning_rules'])}")
    add("AUDIT", "vendors ranked", "all", scorecard["ranked"].sum(),
        note=f">= {mcfg['min_incidents_per_vendor']} incidents in the period")
    add("AUDIT", "vendors on audit list", "all", scorecard["audit"].sum())
    return pd.DataFrame(rows).astype({"numerator": "Int64", "denominator": "Int64"})


def metric_value(system, metric_id, segment, metric=None):
    rows = system[(system["metric_id"] == metric_id) & (system["segment"] == segment)]
    if metric is not None:
        rows = rows[rows["metric"] == metric]
    return rows["value"].iloc[0]


def render_scorecard(month, run_status, run_notes, system, sc, thresholds, mcfg):
    def v(key):
        return metric_value(system, *key)

    def pct(x):
        return "n/a" if pd.isna(x) else f"{x:.1%}"

    def num(x, digits=1):
        return "n/a" if pd.isna(x) else f"{x:,.{digits}f}"

    m3_note = system.loc[(system["metric_id"] == "M3") & (system["metric"] == "student-minutes delayed"), "note"].iloc[0]
    lines = [
        f"# Vendor scorecard — {month}",
        "",
        f"**Run status:** {run_status.upper()}",
    ]
    lines += [f"- {n}" for n in run_notes]
    lines += [
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Incidents (valid) | {num(v(('CTX', 'all', 'incidents')), 0)} ({num(v(('CTX', 'all', 'valid incidents')), 0)}) |",
        f"| **M1** Parents notified (vendor-claimed) | **{pct(v(('M1', 'all', 'parent notification rate')))}** |",
        f"| **M2** Median logging lag | {num(v(('M2', 'all', 'median logging lag (minutes)')))} min |",
        f"| **M3** Student-minutes delayed (lower bound) | {num(v(('M3', 'all', 'student-minutes delayed')), 0)} |",
        f"| **M4** Incidents per 100 contracted routes | {num(v(('M4', 'contract vendors', 'incidents per 100 contracted routes')))} |",
        f"| **M5** Data-trust score | {pct(v(('M5', 'all', 'data-trust score')))} |",
        "",
        f"M3 note: {m3_note}.",
        "",
        "## Parent notification by run type",
        "",
        "| Run type | Notified | Incidents |",
        "|---|---|---|",
    ]
    by_run = system[(system["metric_id"] == "M1") & (system["segment"] != "all")].sort_values("value")
    lines += [f"| {r.segment} | {pct(r.value)} | {r.denominator} |" for r in by_run.itertuples()]

    ranked = sc[sc["ranked"]].sort_values(["audit", M1], ascending=[False, True])
    lines += [
        "",
        "## Audit list",
        "",
        f"A vendor with at least {mcfg['min_incidents_per_vendor']} incidents is listed if any trigger fires: "
        f"**M1** notification rate at or below {pct(thresholds['m1_at_or_below'])} (bottom quartile), "
        f"**M4** at or above {num(thresholds['m4c_at_or_above'])} vendor-controllable incidents per 100 routes "
        f"(top quartile; mechanical, won't start, flat tire), "
        f"**M5** trust score below {pct(thresholds['m5_below'])}.",
        "",
        "| Vendor | Code | Incidents | M1 notified | M2 lag (min) | M3 student-min | M4 all causes | M4 controllable | M5 trust | Audit | Triggers |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ranked.itertuples():
        code = r.vendor_code if isinstance(r.vendor_code, str) else "–"
        lines.append(f"| {r.vendor_name} | {code} | {r.incidents} | {pct(getattr(r, M1))} | {num(getattr(r, M2))} | "
                     f"{num(getattr(r, M3), 0)} | {num(getattr(r, M4))} | {num(getattr(r, M4C))} | "
                     f"{pct(getattr(r, M5))} | {'**YES**' if r.audit else 'no'} | {r.audit_triggers or '–'} |")
    unranked = sc[~sc["ranked"]]
    lines += [
        "",
        f"{len(unranked)} vendor(s) with fewer than {mcfg['min_incidents_per_vendor']} incidents are not ranked "
        f"({int(unranked['incidents'].sum())} incidents in total); see the CSV.",
        "",
        "## Read with care",
        "",
        "- Notification flags and delays are **self-reported by the vendors being evaluated**.",
        "- Vendors without contract route data (mostly Pre-K) have no M4 value.",
        "- Quartile triggers are relative: they always name the weakest vendors of the month, not an absolute failure.",
        "",
    ]
    return "\n".join(lines)
