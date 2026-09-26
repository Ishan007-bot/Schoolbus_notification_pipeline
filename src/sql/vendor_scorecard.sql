-- One row per vendor for the period. Reads the temp tables built by src/metrics.py:
--   period_incidents  fact_incident rows for the period + reason_category, trusted, m3_eligible
--   period_routes     contracted routes per vendor_code for the period's school year
-- Rates are fractions (0-1). Invalid rows (critical rule failures) are excluded from M1-M4
-- but INCLUDED in M5, because failing rows are exactly what lowers trust.
SELECT
    v.vendor_key,
    v.vendor_name,
    v.vendor_code,
    v.in_contract_data,
    count(*)                                                                    AS incidents,
    count(*) FILTER (WHERE i.is_valid)                                          AS valid_incidents,

    -- M1 parent notification rate (vendor-claimed)
    avg(i.notified_parents::INT) FILTER (WHERE i.is_valid AND i.notified_parents IS NOT NULL)
                                                                                AS m1_parent_notified_rate,
    -- M2 logging lag
    median(i.logging_lag_min) FILTER (WHERE i.is_valid)                         AS m2_median_logging_lag_min,
    avg(i.logged_quickly::INT) FILTER (WHERE i.is_valid)                        AS m2_logged_quickly_rate,

    -- M3 student-minutes delayed (lower bound; low/high from the delay bucket bounds)
    coalesce(sum(i.delay_est  * i.students_on_bus) FILTER (WHERE i.m3_eligible), 0) AS m3_student_minutes,
    coalesce(sum(i.delay_low  * i.students_on_bus) FILTER (WHERE i.m3_eligible), 0) AS m3_student_minutes_low,
    coalesce(sum(i.delay_high * i.students_on_bus) FILTER (WHERE i.m3_eligible), 0) AS m3_student_minutes_high,

    -- M4 incidents per 100 contracted routes (only vendors present in OPT's route contracts)
    r.contracted_routes,
    count(*) FILTER (WHERE i.is_valid) * 100.0 / r.contracted_routes            AS m4_incidents_per_100_routes,
    count(*) FILTER (WHERE i.is_valid AND i.reason_category = 'vendor_controllable') * 100.0
        / r.contracted_routes                                                   AS m4_controllable_per_100_routes,

    -- M5 data-trust score
    avg(i.trusted::INT)                                                         AS m5_trust_score,

    count(*) FILTER (WHERE i.vendor_source = 'routes')                          AS attributed_via_routes
FROM period_incidents i
JOIN dim_vendor v USING (vendor_key)
LEFT JOIN period_routes r ON r.vendor_code = v.vendor_code
GROUP BY v.vendor_key, v.vendor_name, v.vendor_code, v.in_contract_data, r.contracted_routes
ORDER BY v.vendor_key
