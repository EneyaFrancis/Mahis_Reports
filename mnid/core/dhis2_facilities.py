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
_ORG_UNITS_PATH = Path(__file__).resolve().parents[2] / 'mnid' / 'dhis2' / 'config' / 'organisation_units.json'
_records_cache: list[dict] | None = None
_org_units_by_id_cache: dict[str, dict] | None = None


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


def dhis2_known_org_unit_ids() -> list[str]:
    """DHIS2 ID for every one of the 67 crosswalk facilities, for requesting
    them explicitly in an analytics query.

    Confirmed by direct query (2026-08-04): passing 'ou:LEVEL-4' as a bare
    level wildcard silently scopes to whatever org-unit subtree the API
    user's own account root sits under (DHIS2's normal behavior for an
    unqualified LEVEL-n dimension item -- it resolves relative to the
    calling user, not the whole system) -- for this API user, that returned
    data for only 5 org units total (department-level sub-units of the 3
    Central Hospitals), even though all 67 crosswalk facilities have real
    reported data for the same period. Requesting these 67 IDs directly
    bypasses that scoping and returns every facility's real data.
    """
    return [rec['DHIS2 ID'] for rec in _load() if rec.get('DHIS2 ID')]


def _org_units_by_id() -> dict[str, dict]:
    global _org_units_by_id_cache
    if _org_units_by_id_cache is None:
        try:
            with open(_ORG_UNITS_PATH, 'r', encoding='utf-8') as fh:
                units = json.load(fh).get('organisation_units', [])
            _org_units_by_id_cache = {u['org_unit_id']: u for u in units if u.get('org_unit_id')}
        except Exception:
            _org_units_by_id_cache = {}
    return _org_units_by_id_cache


def dhis2_extra_org_unit_ids() -> list[str]:
    """DHIS2 org_unit_id values for crosswalk facilities that don't sit at
    the standard org-unit level the sync query requests (LEVEL-4/facility).

    Malawi's Central/Referral Hospitals (Queen Elizabeth, Kamuzu Central,
    Mzuzu Central, ...) are modelled directly under a national "Central
    Hospital" grouping, bypassing the normal District Health Office
    structure -- DHIS2 places them at level 3 ("district" in this registry's
    own terms), not level 4. A query for org_units=['LEVEL-4'] alone
    structurally excludes them, independent of any facility_code mapping --
    the publish/sample-sync analytics calls need to request these explicitly
    alongside the LEVEL-4 wildcard.
    """
    by_id = _org_units_by_id()
    extra = []
    for rec in _load():
        dhis2_id = rec.get('DHIS2 ID')
        unit = by_id.get(dhis2_id)
        if unit is not None and unit.get('level') != 'facility':
            extra.append(dhis2_id)
    return extra


def dhis2_resolve_ancestor_facility_code(org_unit_id: str, max_hops: int = 6) -> str | None:
    """Resolve org_unit_id to a named facility_code, walking up the DHIS2
    parent chain if the unit itself isn't one of the 67 crosswalk facilities.

    Our own facility structure is 67 named facilities under 3 districts --
    that's the structure data should ultimately belong to, no matter how
    DHIS2 models it underneath (a department, a ward, a sub-unit one or more
    levels below a named facility; Central Hospitals reporting at level 3
    instead of level 4 is the same kind of mismatch, just one hop up rather
    than down). Rather than hand-mapping each new sub-unit DHIS2 introduces
    one at a time, walk up parent_org_unit_id until an ancestor with a known
    local_facility_code is found -- that ancestor is the named facility this
    data belongs to in our structure, whatever DHIS2 level it reports at.
    """
    by_id = _org_units_by_id()
    current_id = org_unit_id
    for _ in range(max_hops):
        unit = by_id.get(current_id)
        if unit is None:
            return None
        code = unit.get('local_facility_code')
        if code:
            return code
        parent_id = unit.get('parent_org_unit_id')
        if not parent_id or parent_id == current_id:
            return None
        current_id = parent_id
    return None
