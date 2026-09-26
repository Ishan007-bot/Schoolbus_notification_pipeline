"""Type the raw strings and derive the columns rules and metrics need.

Nothing is dropped or corrected here: unreadable values become NaT / NA and the
rules decide what that means.
"""
import numpy as np
import pandas as pd

from src.parse_delay import parse_delay_column

YES_NO = {"Yes": True, "No": False}
FLAG_COLUMNS = {
    "has_contractor_notified_parents": "notified_parents",
    "has_contractor_notified_schools": "notified_schools",
    "have_you_alerted_opt": "alerted_opt",
}


def prepare_incidents(raw, routes, school_year, use_route_contracts=True):
    df = raw.copy()
    for col in ("occurred_on", "created_on", "informed_on", "last_updated_on"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df["students_on_bus"] = pd.to_numeric(df["number_of_students_on_the_bus"], errors="coerce").astype("Float64")
    df["logging_lag_min"] = (df["created_on"] - df["occurred_on"]).dt.total_seconds() / 60
    for source, target in FLAG_COLUMNS.items():
        df[target] = df[source].map(YES_NO).astype("boolean")

    df = df.join(parse_delay_column(df["how_long_delayed"]))
    return df.join(attribute_vendor(df, routes, school_year, use_route_contracts))


def attribute_vendor(df, routes, school_year, use_route_contracts=True):
    """Who is responsible for each incident, and how we know.

    vendor_source:
      routes         route_number found in OPT's contract data for this school year (authoritative)
      name_match     route not found, but the reported company name exactly matches a contracted vendor
      reported_name  neither - fall back to the name the vendor typed (e.g. Pre-K routes, absent from Routes)

    use_route_contracts=False (summer months): route contracts describe the school year, not summer
    service, so only the name steps are used.
    """
    reported = df["bus_company_name"].str.strip()
    empty = pd.Series(pd.NA, index=df.index, dtype="string")
    route_code, route_name, name_code = empty, empty, empty

    if routes is not None:
        year = routes[routes["School_Year"] == school_year]
        if len(year):
            by_route = year.drop_duplicates("Route_Number").set_index("Route_Number")
            by_name = year.drop_duplicates("Vendor_Name").set_index("Vendor_Name")["Vendor_Code"]
            if use_route_contracts:
                route_code = df["route_number"].map(by_route["Vendor_Code"]).astype("string")
                route_name = df["route_number"].map(by_route["Vendor_Name"]).astype("string")
            name_code = reported.map(by_name).astype("string")

    use_route = route_code.notna()
    use_name = ~use_route & name_code.notna()
    return pd.DataFrame({
        "vendor_code": route_code.where(use_route, name_code),
        "vendor_name": route_name.where(use_route, reported),
        "vendor_source": pd.Series(np.select([use_route, use_name], ["routes", "name_match"], "reported_name"),
                                   index=df.index, dtype="string"),
        "contract_vendor_name": route_name,
    })
