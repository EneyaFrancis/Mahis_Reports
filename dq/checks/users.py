"""User-roster validity checks for the Data Quality page's Users tab.

Operates on the combined user roster for one route --
data/{route}/single_tables/users_data.csv (if present) merged with
data/{route}/dcc_dropdown_json/user_properties.json's GUI-configured
overrides -- cross-checked against the districts and facility codes
actually present in that route's raw extract, since a user's configured
scope is only as good as the data it's scoped to.
"""
import json
import os

import pandas as pd

RULES = {
    "U1": {
        "label": "Orphaned district",
        "severity": "high",
        "description": "Assigned district doesn't exist anywhere in this route's current data.",
    },
    "U2": {
        "label": "Orphaned facility code",
        "severity": "high",
        "description": "Assigned facility code doesn't exist anywhere in this route's current data.",
    },
    "U3": {
        "label": "Duplicate uuid",
        "severity": "critical",
        "description": "This uuid appears more than once in the combined roster.",
    },
    "U4": {
        "label": "Missing role",
        "severity": "critical",
        "description": "No role is set for this user.",
    },
    "U5": {
        "label": "Invalid user level",
        "severity": "high",
        "description": "user_level is not one of national, district, or facility.",
    },
    "U6": {
        "label": "Scope not set for level",
        "severity": "medium",
        "description": "A district- or facility-level user has no district or facility actually assigned.",
    },
    "U7": {
        "label": "Facility/district mismatch",
        "severity": "medium",
        "description": "The assigned facility code's real district (per the current data) doesn't match "
                        "the user's own assigned district.",
    },
    "U8": {
        "label": "Conflicting entries across sources",
        "severity": "high",
        "description": "This uuid appears in both users_data.csv and user_properties.json with "
                        "different role/level/district/facility values, not just a plain duplicate.",
    },
}
RULE_ORDER = ["U1", "U2", "U3", "U4", "U5", "U6", "U7", "U8"]

_VALID_LEVELS = {"national", "district", "facility"}

_ROSTER_COLUMNS = [
    "source", "username", "uuid", "role", "user_level", "district", "facility_code",
    "person_id", "location_id", "given_name", "family_name", "facility_name", "facility_name_source",
]


def load_user_roster(route: str) -> tuple[pd.DataFrame, bool]:
    """Combined roster: users_data.csv rows (if the file exists) plus
    user_properties.json's GUI-configured overrides. Deliberately does NOT
    include the live page's synthetic demo-user row (added only so the demo
    login always resolves) -- including it here would misrepresent the real
    roster. Names/facility names are filled in by _enrich_identities below,
    not here. Returns (roster_df, users_csv_present).
    """
    root = os.getcwd()
    users_path = os.path.join(root, f"data/{route}", "single_tables", "users_data.csv")
    props_path = os.path.join(root, f"data/{route}", "dcc_dropdown_json", "user_properties.json")

    rows = []
    users_csv_present = os.path.exists(users_path)
    if users_csv_present:
        try:
            csv_df = pd.read_csv(users_path)
            for _, r in csv_df.iterrows():
                rows.append({
                    "source": "users_data.csv",
                    "username": r.get("username") if "username" in csv_df.columns else r.get("user_id"),
                    "uuid": r.get("uuid"),
                    "role": r.get("role"),
                    "user_level": r.get("user_level"),
                    "district": r.get("district"),
                    "facility_code": r.get("facility_code"),
                    "person_id": r.get("person_id"),
                    "location_id": r.get("location_id"),
                    "facility_name": None,
                    "facility_name_source": None,
                })
        except Exception:
            pass

    try:
        with open(props_path, encoding="utf-8") as f:
            props = json.load(f)
        for entry in props.get("users", []):
            p = entry.get("properties", {})
            district = p.get("district")
            if isinstance(district, list):
                district = district[0] if district else None
            facility_name = p.get("facility_name")
            if isinstance(facility_name, list):
                facility_name = ", ".join(str(f) for f in facility_name if f) or None
            rows.append({
                "source": "user_properties.json",
                "username": entry.get("username"),
                "uuid": p.get("uuid"),
                "role": p.get("role"),
                "user_level": p.get("user_level"),
                "district": district,
                "facility_code": p.get("facility_code"),
                "person_id": None,
                "location_id": None,
                # Configured directly by whoever set up this user -- trusted
                # as given, unlike the locations_data.csv lookup below.
                "facility_name": facility_name,
                "facility_name_source": "configured" if facility_name else None,
            })
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    df = pd.DataFrame(rows, columns=_ROSTER_COLUMNS)
    return _enrich_identities(df, route), users_csv_present


def _enrich_identities(df: pd.DataFrame, route: str) -> pd.DataFrame:
    """Fills in given_name/family_name for rows with a person_id (joined
    against this route's own raw parquet -- the same person_id used
    throughout the rest of the data), and facility_name for rows with a
    location_id but no configured name yet (joined against
    locations_data.csv). The locations_data.csv join is marked with
    facility_name_source="looked_up (locations_data.csv)" rather than
    trusted outright -- that file is known to carry stale/demo-era
    location_id -> name pairs that don't match the current raw extract, so a
    name resolved from it should be read with that caveat, not as fact.
    """
    if df.empty:
        return df

    person_ids = df["person_id"].dropna().unique().tolist()
    if person_ids:
        try:
            from data_storage import DataStorage
            from config import PERSON_ID_, FIRST_NAME_, LAST_NAME_

            ids_sql = ", ".join(str(int(pid)) for pid in person_ids if pd.notna(pid))
            if ids_sql:
                names_df = DataStorage.query_duckdb(
                    f"SELECT {PERSON_ID_} AS person_id, MAX({FIRST_NAME_}) AS given_name, "
                    f"MAX({LAST_NAME_}) AS family_name FROM 'data/{route}/parquet' "
                    f"WHERE {PERSON_ID_} IN ({ids_sql}) GROUP BY {PERSON_ID_}"
                )
                name_map = names_df.set_index("person_id")[["given_name", "family_name"]].to_dict("index")
                df["given_name"] = df["person_id"].map(lambda pid: name_map.get(pid, {}).get("given_name"))
                df["family_name"] = df["person_id"].map(lambda pid: name_map.get(pid, {}).get("family_name"))
        except Exception:
            pass

    needs_facility_lookup = df["location_id"].notna() & df["facility_name"].isna()
    if needs_facility_lookup.any():
        try:
            root = os.getcwd()
            loc_path = os.path.join(root, f"data/{route}", "single_tables", "locations_data.csv")
            loc_df = pd.read_csv(loc_path)
            loc_map = loc_df.set_index("location_id")["name"].to_dict()
            resolved = df.loc[needs_facility_lookup, "location_id"].map(loc_map)
            df.loc[needs_facility_lookup, "facility_name"] = resolved
            df.loc[needs_facility_lookup & resolved.notna(), "facility_name_source"] = \
                "looked up (locations_data.csv -- may be stale)"
        except Exception:
            pass

    return df


def evaluate_users(
    roster_df: pd.DataFrame,
    valid_districts: set | None,
    valid_facility_codes: set | None,
    facility_district_map: dict | None = None,
) -> pd.DataFrame:
    """Adds one boolean column per RULE_ORDER id, plus an 'issues' column
    listing which rule ids each row tripped.

    valid_districts/valid_facility_codes being None (rather than an empty
    set) means "no ground truth available" -- e.g. the route's raw extract
    doesn't exist at all -- and skips U1/U2 (and U7, which also needs real
    facility/district data) entirely instead of flagging every assigned
    district/facility as orphaned, which an empty set would do (a
    false-positive storm, not a real finding).
    """
    df = roster_df.copy()
    if df.empty:
        for rid in RULE_ORDER:
            df[rid] = pd.Series(dtype=bool)
        df["issues"] = pd.Series(dtype=object)
        return df

    if valid_districts is None:
        df["U1"] = False
    else:
        valid_districts = {str(d) for d in valid_districts}
        df["U1"] = df["district"].apply(lambda d: bool(d) and pd.notna(d) and str(d) not in valid_districts)

    if valid_facility_codes is None:
        df["U2"] = False
    else:
        valid_facility_codes = {str(f) for f in valid_facility_codes}
        df["U2"] = df["facility_code"].apply(lambda f: bool(f) and pd.notna(f) and str(f) not in valid_facility_codes)

    uuid_counts = df["uuid"].value_counts()
    df["U3"] = df["uuid"].apply(lambda u: bool(u) and pd.notna(u) and uuid_counts.get(u, 0) > 1)

    df["U4"] = df["role"].apply(lambda r: not bool(str(r or "").strip()) or pd.isna(r))
    df["U5"] = df["user_level"].apply(lambda lv: str(lv or "").strip().lower() not in _VALID_LEVELS)

    def _scope_missing(row):
        level = str(row.get("user_level") or "").strip().lower()
        if level == "district":
            return not row.get("district") or pd.isna(row.get("district"))
        if level == "facility":
            return not row.get("facility_code") or pd.isna(row.get("facility_code"))
        return False

    df["U6"] = df.apply(_scope_missing, axis=1)

    if not facility_district_map:
        df["U7"] = False
    else:
        fd_map = {str(k): v for k, v in facility_district_map.items()}

        def _facility_district_mismatch(row):
            fac, dist = row.get("facility_code"), row.get("district")
            if not fac or pd.isna(fac) or not dist or pd.isna(dist):
                return False
            real_district = fd_map.get(str(fac))
            return real_district is not None and str(real_district) != str(dist)

        df["U7"] = df.apply(_facility_district_mismatch, axis=1)

    # Conflicting (not just duplicate) entries for the same uuid: the two
    # sources disagree on role/level/district/facility rather than one
    # simply repeating the other.
    conflict_fields = ["role", "user_level", "district", "facility_code"]

    def _has_conflict(group):
        if len(group) < 2:
            return False
        return any(group[f].nunique(dropna=True) > 1 for f in conflict_fields)

    conflicting_uuids = {
        u for u, g in df[df["uuid"].notna()].groupby("uuid") if _has_conflict(g)
    }
    df["U8"] = df["uuid"].apply(lambda u: u in conflicting_uuids)

    df["issues"] = df.apply(lambda row: [rid for rid in RULE_ORDER if row.get(rid)], axis=1)
    return df
