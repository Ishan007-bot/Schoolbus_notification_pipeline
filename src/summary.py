"""Cross-month evidence: the 5 metrics month by month, per school year, and the vendors that
keep landing on the audit list (a much stronger audit case than any single month's quartiles).

Built only from the published monthly outputs in data/output/<month>/, so it shows exactly what
was reported. Months without outputs (halted runs) are listed as such, not skipped silently.

Writes data/output/summary/summary_<first>_<last>.{md,csv} and repeat_audits_<first>_<last>.csv
"""
import json
import logging
from pathlib import Path

import pandas as pd

from src.metrics import metric_value
from src.utils.files import atomic_write

log = logging.getLogger("summary")


def build_summary(months, output_root, min_months_ranked=6):
    output_root = Path(output_root)
    trend_rows, vendor_rows, pooled = [], [], []

    for month in months:
        folder = output_root / month
        manifest_path = folder / "run_manifest.json"
        if not manifest_path.exists():
            trend_rows.append({"month": month, "status": "no outputs (halted)"})
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metrics = pd.read_csv(folder / f"metrics_{month}.csv")
        scorecard = pd.read_csv(folder / f"vendor_scorecard_{month}.csv")

        m1_all = metrics[(metrics["metric_id"] == "M1") & (metrics["segment"] == "all")].iloc[0]
        pooled.append({"school_year": manifest["school_year"], "incidents": _value(metrics, "CTX", "all", "incidents"),
                       "m1_num": m1_all["numerator"], "m1_den": m1_all["denominator"]})
        trend_rows.append({
            "month": month,
            "status": manifest["status"],
            "school_year": manifest["school_year"],
            "incidents": _value(metrics, "CTX", "all", "incidents"),
            "m1_parent_notified": m1_all["value"],
            "m1_gen_ed_pm": _value(metrics, "M1", "General Ed PM Run"),
            "m1_pre_k": _value(metrics, "M1", "Pre-K/EI"),
            "m2_median_lag_min": _value(metrics, "M2", "all", "median logging lag (minutes)"),
            "m3_student_minutes": _value(metrics, "M3", "all", "student-minutes delayed"),
            "m3_top_bucket_share": _value(metrics, "M3", "all", "late incidents in top delay bucket"),
            "m4_per_100_routes": _value(metrics, "M4", "contract vendors"),
            "m5_trust": _value(metrics, "M5", "all"),
            "vendors_ranked": int(scorecard["ranked"].sum()),
            "vendors_audited": int(scorecard["audit"].sum()),
        })
        vendor_rows.append(scorecard.assign(month=month)[
            ["month", "vendor_key", "vendor_name", "ranked", "audit", "audit_triggers",
             "incidents", "m1_parent_notified_rate"]])

    trend = pd.DataFrame(trend_rows)
    repeat = _repeat_audits(pd.concat(vendor_rows) if vendor_rows else pd.DataFrame(), min_months_ranked)
    by_year = _school_years(pd.DataFrame(pooled))

    folder = output_root / "summary"
    tag = f"{months[0]}_{months[-1]}"
    paths = {
        "trend_csv": atomic_write(folder / f"summary_{tag}.csv", lambda p: trend.round(4).to_csv(p, index=False)),
        "repeat_csv": atomic_write(folder / f"repeat_audits_{tag}.csv", lambda p: repeat.round(4).to_csv(p, index=False)),
    }
    md = render(months, trend, by_year, repeat, min_months_ranked)
    paths["md"] = atomic_write(folder / f"summary_{tag}.md", lambda p: Path(p).write_text(md, encoding="utf-8"))
    log.info("summary for %s..%s: %s", months[0], months[-1], paths["md"])
    return {k: str(v) for k, v in paths.items()}


def _value(metrics, metric_id, segment, metric=None):
    try:
        return metric_value(metrics, metric_id, segment, metric)
    except IndexError:                  # e.g. no General Ed PM runs in a summer month
        return float("nan")


def _repeat_audits(vendors, min_months_ranked):
    if vendors.empty:
        return pd.DataFrame()
    ranked = vendors[vendors["ranked"]].copy()
    triggers = (ranked.assign(t=ranked["audit_triggers"].fillna("").str.split(";")).explode("t")
                .query("t != ''").groupby("vendor_key")["t"]
                .apply(lambda s: ", ".join(f"{k}×{v}" for k, v in s.value_counts().sort_index().items())))
    out = ranked.groupby("vendor_key").agg(
        vendor_name=("vendor_name", "last"),
        months_ranked=("month", "nunique"),
        months_audited=("audit", "sum"),
        incidents=("incidents", "sum"),
        median_m1=("m1_parent_notified_rate", "median"),
    )
    out["audit_share"] = out["months_audited"] / out["months_ranked"]
    out["triggers"] = triggers.reindex(out.index).fillna("")
    out = out[out["months_ranked"] >= min_months_ranked]
    return out.sort_values(["audit_share", "months_audited", "incidents"], ascending=False).reset_index()


def _school_years(pooled):
    if pooled.empty:
        return pooled
    g = pooled.groupby("school_year").sum(numeric_only=True)
    g["m1_parent_notified"] = g["m1_num"] / g["m1_den"]
    return g.reset_index()


def render(months, trend, by_year, repeat, min_months_ranked):
    def pct(x):
        return "–" if pd.isna(x) else f"{x:.1%}"

    def num(x, d=0):
        return "–" if pd.isna(x) else f"{x:,.{d}f}"

    lines = [f"# SilentDelay summary — {months[0]} to {months[-1]}", ""]
    halted = trend[trend["status"].str.startswith("no outputs")]["month"].tolist()
    ok = len(trend) - len(halted)
    lines += [f"{ok} of {len(trend)} months produced outputs." +
              (f" Halted (no outputs): {', '.join(halted)} - see logs/manifests." if halted else ""), ""]

    lines += ["## By school year", "", "| School year | Incidents | M1 parents notified (pooled) |", "|---|---|---|"]
    lines += [f"| {r.school_year} | {num(r.incidents)} | **{pct(r.m1_parent_notified)}** |" for r in by_year.itertuples()]

    lines += ["", "## Month by month", "",
              "| Month | Status | Incidents | M1 all | M1 Gen Ed PM | M1 Pre-K | M2 lag | M3 student-min | Top bucket | M4 | M5 | Audited |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in trend.itertuples():
        if r.status.startswith("no outputs"):
            lines.append(f"| {r.month} | {r.status} |" + " – |" * 10)
            continue
        lines.append(f"| {r.month} | {r.status} | {num(r.incidents)} | **{pct(r.m1_parent_notified)}** | "
                     f"{pct(r.m1_gen_ed_pm)} | {pct(r.m1_pre_k)} | {num(r.m2_median_lag_min, 1)} | "
                     f"{num(r.m3_student_minutes)} | {pct(r.m3_top_bucket_share)} | {num(r.m4_per_100_routes, 1)} | "
                     f"{pct(r.m5_trust)} | {r.vendors_audited}/{r.vendors_ranked} |")

    lines += ["", "## Repeat audits", "",
              f"Vendors ranked in at least {min_months_ranked} months, ordered by the share of those months they were "
              "on the audit list. A vendor flagged month after month is a stronger audit case than one bad month.", "",
              "| Vendor | Code | Months ranked | Months audited | Share | Median M1 | Triggers |",
              "|---|---|---|---|---|---|---|"]
    for r in repeat.head(15).itertuples():
        code = "–" if r.vendor_key.startswith("NAME:") else r.vendor_key
        lines.append(f"| {r.vendor_name} | {code} | {r.months_ranked} | {r.months_audited} | "
                     f"**{pct(r.audit_share)}** | {pct(r.median_m1)} | {r.triggers or '–'} |")
    lines += ["", "M1 is vendor-claimed. M3 is a lower bound (top delay bucket is open-ended). "
              "Per-month detail: data/output/<month>/vendor_scorecard_<month>.md.", ""]
    return "\n".join(lines)
