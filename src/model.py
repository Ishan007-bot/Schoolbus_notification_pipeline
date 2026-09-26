"""Model stage: load validated incidents + reference data into the DuckDB star schema.

Rerun-safe: the period's rows (and its school year's reference rows) are deleted and
re-inserted inside ONE transaction, and integrity checks run before COMMIT. A failed
load rolls back and leaves the warehouse exactly as it was.
"""
import logging
from datetime import timedelta
from pathlib import Path

import duckdb

from src.config import REPO_ROOT, month_bounds, school_year_for_month
from src.ingest.weather import load_weather

log = logging.getLogger("model")

SCHEMA = Path(__file__).parent / "sql" / "schema.sql"
REASONS = REPO_ROOT / "reference" / "reason_categories.csv"
TABLES = ["fact_incident", "bridge_incident_site", "dim_vendor", "dim_route", "dim_site", "dim_hour", "dim_reason"]
REFERENCE_LOADABLE = {"ok", "reused", "fallback", "missing_year"}
WEATHER_LOADABLE = {"ok", "reused"}

NOT_A_DUPLICATE = "NOT list_contains(string_split(coalesce(failure_reasons, ''), ';'), 'V01')"

# USPS ZIP prefixes for the five boroughs. 11004/11005 are Queens despite the Nassau-style 110 prefix.
BOROUGH_FROM_ZIP = """CASE
    WHEN Zip IS NULL OR NOT regexp_matches(Zip, '^[0-9]{5}') THEN 'Unknown'
    WHEN substr(Zip, 1, 3) IN ('100', '101', '102') THEN 'Manhattan'
    WHEN substr(Zip, 1, 3) = '103' THEN 'Staten Island'
    WHEN substr(Zip, 1, 3) = '104' THEN 'Bronx'
    WHEN substr(Zip, 1, 3) = '112' THEN 'Brooklyn'
    WHEN substr(Zip, 1, 3) IN ('111', '113', '114', '116') OR substr(Zip, 1, 5) IN ('11004', '11005') THEN 'Queens'
    ELSE 'Outside NYC' END"""


class ModelError(Exception):
    """The loaded tables are inconsistent; the load was rolled back."""


def run_model(month, ingest_results, validated_path, warehouse_path, reasons_path=REASONS):
    Path(warehouse_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse_path))
    try:
        con.execute(SCHEMA.read_text(encoding="utf-8"))
        con.execute("BEGIN TRANSACTION")
        try:
            load_reasons(con, reasons_path)
            load_routes(con, ingest_results["routes"], school_year_for_month(month))
            load_sites(con, ingest_results["sites"], school_year_for_month(month))
            load_weather_hours(con, ingest_results["weather"], month)
            loaded = load_incidents(con, month, validated_path)
            rebuild_vendor_dim(con)

            checks = integrity_checks(con, month, loaded)
            failed = [name for name, c in checks.items() if not c["passed"]]
            if failed:
                raise ModelError("; ".join(f"{name}: {checks[name]['detail']}" for name in failed))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        unmapped = [r[0] for r in con.execute("""
            SELECT DISTINCT f.reason FROM fact_incident f LEFT JOIN dim_reason r USING (reason)
            WHERE f.period = ? AND r.reason IS NULL AND f.reason IS NOT NULL""", [month]).fetchall()]
        if unmapped:
            log.warning("reasons not in reference/reason_categories.csv (treated as 'unknown'): %s", unmapped)

        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
        log.info("fact rows for %s: %d (%d duplicate IDs left out) | checks passed: %s",
                 month, loaded["inserted"], loaded["duplicates_skipped"], ", ".join(checks))
        log.info("warehouse totals: %s", ", ".join(f"{t}={n}" for t, n in counts.items()))
        return {"period_rows": loaded["inserted"], "duplicates_skipped": loaded["duplicates_skipped"],
                "table_counts": counts, "unmapped_reasons": unmapped, "checks": checks}
    finally:
        con.close()


def _sql_path(path):
    """A file path as a SQL string literal (DuckDB table functions take literals)."""
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def load_reasons(con, reasons_path):
    con.execute("DELETE FROM dim_reason")
    con.execute(f"""
        INSERT INTO dim_reason
        SELECT reason, category, rationale FROM read_csv({_sql_path(reasons_path)}, header = true, all_varchar = true)""")


def load_routes(con, result, school_year):
    if result.status not in REFERENCE_LOADABLE:
        log.warning("routes not loadable (%s); dim_route for %s left unchanged", result.status, school_year)
        return
    con.execute("DELETE FROM dim_route WHERE school_year = ?", [school_year])
    # all_varchar keeps codes as text; GROUP BY guards against repeated route rows in the file
    con.execute(f"""
        INSERT INTO dim_route
        SELECT School_Year, Route_Number, any_value(Vendor_Code), any_value(Vendor_Name), any_value(Service_Type)
        FROM read_csv({_sql_path(Path(result.raw_dir) / 'routes.csv')}, header = true, all_varchar = true)
        WHERE School_Year = ?
        GROUP BY School_Year, Route_Number""", [school_year])


def load_sites(con, result, school_year):
    if result.status not in REFERENCE_LOADABLE:
        log.warning("sites not loadable (%s); dim_site for %s left unchanged", result.status, school_year)
        return
    con.execute("DELETE FROM dim_site WHERE school_year = ?", [school_year])
    con.execute(f"""
        INSERT INTO dim_site
        SELECT School_Year, OPT_Code, any_value(Name), any_value(Site_Type), any_value(Zip), any_value(City),
               any_value({BOROUGH_FROM_ZIP}),
               any_value(TRY_CAST(Latitude AS DOUBLE)), any_value(TRY_CAST(Longitude AS DOUBLE))
        FROM read_csv({_sql_path(Path(result.raw_dir) / 'sites.csv')}, header = true, all_varchar = true)
        WHERE School_Year = ?
        GROUP BY School_Year, OPT_Code""", [school_year])


def load_weather_hours(con, result, month):
    if result.status not in WEATHER_LOADABLE:
        log.warning("weather unavailable (%s); no dim_hour rows for %s", result.status, month)
        return
    start, end = month_bounds(month)
    hourly = load_weather(result.raw_dir)
    con.register("weather_hourly", hourly)
    con.execute("DELETE FROM dim_hour WHERE hour >= ? AND hour < ?", [start, end + timedelta(days=1)])
    # GROUP BY: the repeated local hour when clocks go back in November
    con.execute("""
        INSERT INTO dim_hour
        SELECT CAST(time AS TIMESTAMP) AS hour, sum(precipitation), sum(snowfall), avg(temperature_2m)
        FROM weather_hourly GROUP BY 1""")
    con.unregister("weather_hourly")


def load_incidents(con, month, validated_path):
    src = f"read_parquet({_sql_path(validated_path)})"
    total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]

    con.execute("DELETE FROM bridge_incident_site WHERE period = ?", [month])
    con.execute("DELETE FROM fact_incident WHERE period = ?", [month])
    con.execute(f"""
        INSERT INTO fact_incident
        SELECT ?, busbreakdown_id, school_year, route_number,
               coalesce(vendor_code, 'NAME:' || upper(trim(vendor_name)), 'UNKNOWN'),
               vendor_source, run_type, breakdown_or_running_late, reason,
               occurred_on, created_on, date_trunc('hour', occurred_on),
               logging_lag_min, students_on_bus, delay_low, delay_high, delay_est, delay_parse_method,
               delay_censored, notified_parents, notified_schools, alerted_opt,
               is_valid, failure_reasons, warning_reasons
        FROM {src}
        WHERE {NOT_A_DUPLICATE}""", [month])
    con.execute(f"""
        INSERT INTO bridge_incident_site
        SELECT DISTINCT ?, busbreakdown_id, trim(code)
        FROM (SELECT busbreakdown_id, unnest(string_split(schools_serviced, ',')) AS code
              FROM {src} WHERE {NOT_A_DUPLICATE})
        WHERE trim(code) <> ''""", [month])

    inserted = con.execute("SELECT count(*) FROM fact_incident WHERE period = ?", [month]).fetchone()[0]
    duplicates = con.execute(f"SELECT count(*) FROM {src} WHERE NOT ({NOT_A_DUPLICATE})").fetchone()[0]
    return {"source_rows": total, "inserted": inserted, "duplicates_skipped": duplicates}


def rebuild_vendor_dim(con):
    """Contract vendors (from every loaded school year) plus name-only vendors seen in incidents."""
    con.execute("""
        CREATE OR REPLACE TABLE dim_vendor AS
        WITH contract AS (
            SELECT vendor_code AS vendor_key, vendor_code,
                   arg_max(vendor_name, school_year) AS vendor_name, TRUE AS in_contract_data
            FROM dim_route WHERE vendor_code IS NOT NULL GROUP BY vendor_code
        )
        SELECT * FROM contract
        UNION ALL
        SELECT DISTINCT vendor_key, NULL, regexp_replace(vendor_key, '^NAME:', ''), FALSE
        FROM fact_incident WHERE vendor_key NOT IN (SELECT vendor_key FROM contract)""")


def integrity_checks(con, month, loaded):
    def one(sql):
        return con.execute(sql, [month]).fetchone()[0]

    expected = loaded["source_rows"] - loaded["duplicates_skipped"]
    orphan_vendors = one("""SELECT count(*) FROM fact_incident f
                            WHERE f.period = ? AND f.vendor_key NOT IN (SELECT vendor_key FROM dim_vendor)""")
    orphan_bridge = one("""SELECT count(*) FROM bridge_incident_site b
                           WHERE b.period = ? AND NOT EXISTS (SELECT 1 FROM fact_incident f
                               WHERE f.period = b.period AND f.busbreakdown_id = b.busbreakdown_id)""")
    unresolved_routes = one("""SELECT count(*) FROM fact_incident f
                               WHERE f.period = ? AND f.vendor_source = 'routes' AND NOT EXISTS (
                                   SELECT 1 FROM dim_route r
                                   WHERE r.school_year = f.school_year AND r.route_number = f.route_number)""")
    return {
        "fact_matches_validated": {"passed": loaded["inserted"] == expected,
                                   "detail": f"{loaded['inserted']} loaded, {expected} expected"},
        "fact_not_empty": {"passed": loaded["inserted"] > 0, "detail": f"{loaded['inserted']} rows"},
        "vendor_keys_resolve": {"passed": orphan_vendors == 0, "detail": f"{orphan_vendors} unresolved"},
        "bridge_has_no_orphans": {"passed": orphan_bridge == 0, "detail": f"{orphan_bridge} orphans"},
        "contract_routes_resolve": {"passed": unresolved_routes == 0, "detail": f"{unresolved_routes} unresolved"},
    }
