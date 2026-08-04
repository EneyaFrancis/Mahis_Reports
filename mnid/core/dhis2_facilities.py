"""Facility name <-> DHIS2 facility_code crosswalk.

The MAHIS-side facility lookups (mnid/core/constants.py's FACILITY_NAMES /
FACILITY_DISTRICT, and data/*/dcc_dropdown_json/facilities_dropdowns.json)
are keyed by MAHIS's own Facility_CODE values, which have nothing to do with
DHIS2's facility_code space (data/mnid_aggregates/dhis2/indicator_aggregates.parquet).
Selecting a MAHIS-named facility can never match a DHIS2 aggregate row without
a crosswalk -- this is that crosswalk, sourced from the curated
data/geo/facilities_dhis2.json list.

That list is a work in progress (currently 67 facilities across
Blantyre/Lilongwe/Mzimba, not yet nationwide) -- callers should treat an
unmapped name/code as "not yet covered", not as an error.
"""
from __future__ import annotations

import json
from pathlib import Path

_FACILITIES_DHIS2_PATH = Path(__file__).resolve().parents[2] / 'data' / 'geo' / 'facilities_dhis2.json'
_records_cache: list[dict] | None = None


def _load() -> list[dict]:
    global _records_cache
    if _records_cache is None:
        try:
            with open(_FACILITIES_DHIS2_PATH, 'r', encoding='utf-8') as fh:
                _records_cache = json.load(fh) or []
        except Exception:
            _records_cache = []
    return _records_cache


def _facility_name(rec: dict) -> str:
    # A few records are missing "NAME" outright (a source-data gap, not a
    # code bug) but still carry "COMMON NAME" -- fall back to that instead
    # of silently dropping the facility from every lookup below.
    return str(rec.get('NAME') or rec.get('COMMON NAME') or '').strip()


def dhis2_facility_records() -> list[dict]:
    """Raw records: CODE, NAME, DISTRICT, FACILITY LEVEL, DHIS2 ID, ..."""
    return _load()


def dhis2_districts() -> list[str]:
    return sorted({rec['DISTRICT'] for rec in _load() if rec.get('DISTRICT')})


def dhis2_facilities_by_district(district: str | None = None) -> list[str]:
    records = _load()
    if district:
        records = [rec for rec in records if rec.get('DISTRICT') == district]
    return sorted({_facility_name(rec) for rec in records if _facility_name(rec)})


def dhis2_name_to_code() -> dict[str, str]:
    return {_facility_name(rec): rec['CODE'] for rec in _load() if _facility_name(rec) and rec.get('CODE')}


def dhis2_code_to_name() -> dict[str, str]:
    return {rec['CODE']: _facility_name(rec) for rec in _load() if _facility_name(rec) and rec.get('CODE')}


def dhis2_code_to_district() -> dict[str, str]:
    return {rec['CODE']: rec['DISTRICT'] for rec in _load() if rec.get('DISTRICT') and rec.get('CODE')}


def dhis2_facility_codes_for_names(names: list[str] | None) -> list[str]:
    """Resolve facility names (as shown in the filter dropdown) to DHIS2
    facility_code values. Names with no crosswalk entry are dropped silently
    -- the caller ends up with an empty/partial code list, which correctly
    reads as "not covered yet" rather than raising."""
    mapping = dhis2_name_to_code()
    return [mapping[name] for name in (names or []) if name in mapping]


def dhis2_known_facility_codes() -> set[str]:
    """Every facility_code this crosswalk has a name for. The published
    DHIS2 aggregate has data for far more facility_codes than this covers
    (nationwide, unnamed) -- by policy, MNID's DHIS2-route views are capped
    to this known set everywhere (badges, totals, filtering) rather than
    mixing named and unnamed facilities in the same numbers."""
    return {rec['CODE'] for rec in _load() if rec.get('CODE')}
