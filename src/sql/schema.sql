-- SilentDelay star schema (DuckDB).
-- Grain is stated for every table; mixing grains is the classic join bug.

-- One row per incident (grain: busbreakdown_id within a period).
-- All validated rows are kept, including invalid ones: metrics filter on is_valid.
-- Only later copies of duplicated IDs (rule V01) are left out, since they are the same incident.
CREATE TABLE IF NOT EXISTS fact_incident (
    period              VARCHAR   NOT NULL,   -- YYYY-MM the pipeline ran for
    busbreakdown_id     VARCHAR   NOT NULL,
    school_year         VARCHAR,
    route_number        VARCHAR,
    vendor_key          VARCHAR   NOT NULL,   -- -> dim_vendor
    vendor_source       VARCHAR,              -- routes | name_match | reported_name
    run_type            VARCHAR,
    incident_type       VARCHAR,              -- Breakdown | Running Late
    reason              VARCHAR,              -- -> dim_reason
    occurred_on         TIMESTAMP,
    created_on          TIMESTAMP,
    occurred_hour       TIMESTAMP,            -- -> dim_hour
    logging_lag_min     DOUBLE,
    students_on_bus     DOUBLE,
    delay_low           DOUBLE,
    delay_high          DOUBLE,
    delay_est           DOUBLE,
    delay_parse_method  VARCHAR,
    delay_censored      BOOLEAN,
    notified_parents    BOOLEAN,
    notified_schools    BOOLEAN,
    alerted_opt         BOOLEAN,
    is_valid            BOOLEAN   NOT NULL,
    failure_reasons     VARCHAR,
    warning_reasons     VARCHAR,
    PRIMARY KEY (period, busbreakdown_id)
);

-- One row per incident x school served (a bus often serves several schools).
-- Count incidents from fact_incident, never from this table.
CREATE TABLE IF NOT EXISTS bridge_incident_site (
    period           VARCHAR NOT NULL,
    busbreakdown_id  VARCHAR NOT NULL,
    opt_code         VARCHAR NOT NULL,
    PRIMARY KEY (period, busbreakdown_id, opt_code)
);

-- One row per contracted route per school year: ALL routes, not only those with incidents,
-- because the route count per vendor is the denominator for incidents-per-100-routes.
CREATE TABLE IF NOT EXISTS dim_route (
    school_year   VARCHAR NOT NULL,
    route_number  VARCHAR NOT NULL,
    vendor_code   VARCHAR,
    vendor_name   VARCHAR,
    service_type  VARCHAR,
    PRIMARY KEY (school_year, route_number)
);

-- One row per transportation site per school year.
-- borough is DERIVED from the ZIP code: the reported City column is ~83% empty in 2024-26 data.
CREATE TABLE IF NOT EXISTS dim_site (
    school_year    VARCHAR NOT NULL,
    opt_code       VARCHAR NOT NULL,
    name           VARCHAR,
    site_type      VARCHAR,
    zip            VARCHAR,
    city_reported  VARCHAR,
    borough        VARCHAR,   -- Manhattan | Bronx | Brooklyn | Queens | Staten Island | Outside NYC | Unknown
    latitude       DOUBLE,
    longitude      DOUBLE,
    PRIMARY KEY (school_year, opt_code)
);

-- One row per hour (single NYC weather point).
CREATE TABLE IF NOT EXISTS dim_hour (
    hour              TIMESTAMP NOT NULL PRIMARY KEY,
    precipitation_mm  DOUBLE,
    snowfall_cm       DOUBLE,
    temperature_c     DOUBLE
);

-- One row per incident reason, from reference/reason_categories.csv.
CREATE TABLE IF NOT EXISTS dim_reason (
    reason     VARCHAR NOT NULL PRIMARY KEY,
    category   VARCHAR NOT NULL,   -- vendor_controllable | route_design | external | unknown
    rationale  VARCHAR
);
