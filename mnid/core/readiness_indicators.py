"""Operational Readiness (HFA) indicator catalog.

Same role as mnid/dashboards/MNH-MoH/indicators.py and
mnid/dashboards/MNH-Nest360/indicators.py: one get_X_indicators() entry point
other code can import, matching every other MNID indicator domain.

Unlike those two, these 216 indicators aren't hand-authored here -- they're
generated from the HFA source workbook by mnid/tools/convert_readiness_data.py
into data/readiness/hfa_indicators.json, so this module is a thin, always-
in-sync wrapper around that file rather than a second copy that could drift.
Re-run the converter (see data/readiness/README.md) after a source workbook
update; this file needs no changes.

No numerator_filters/denominator_filters here -- these aren't computed from
MAHIS/DHIS2 person-level rows like every other indicator domain. Each is a
column on data/readiness/hfa_data.parquet (one row per facility, no date
dimension -- see that README's "why this is static" section), read via
variable/statistic/response by mnid.core.readiness_data.compute_readiness_*.
"""
from __future__ import annotations

import re

from mnid.core.readiness_data import get_readiness_metadata


def _slug(sheet_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", sheet_name.lower()).strip("_")


def get_readiness_indicators() -> list[dict]:
    """All 216 HFA indicators, one dict per row of hfa_indicators.json's
    all_indicators, each given a stable id: readiness__<sheet>__<variable>.
    variable alone isn't unique (e.g. the shared facility autoclave and KMC
    bed count are reported on more than one sheet) -- (sheet, variable) is.
    Returns [] if the readiness dataset hasn't been generated yet."""
    meta = get_readiness_metadata()
    if not meta:
        return []
    out = []
    for ind in meta.get("all_indicators", []):
        out.append({
            "id": f"readiness__{_slug(ind['sheet'])}__{ind['variable']}",
            "variable": ind["variable"],
            "sheet": ind["sheet"],
            "label": ind.get("label"),
            "domain": ind.get("domain"),
            "sub_domain": ind.get("sub_domain"),
            "unit": ind.get("unit"),
            "aspect": ind.get("aspect"),
            "section_header": ind.get("section_header"),
            "statistic": ind.get("statistic", "n_percent"),
            "response": ind.get("response"),
            "cemonc_only": ind.get("cemonc_only", False),
            "denominator_variable": ind.get("denominator_variable"),
            "denominator_response": ind.get("denominator_response"),
            "flag_over100": ind.get("flag_over100", False),
        })
    return out


def get_readiness_indicators_by_sheet(sheet_name: str) -> list[dict]:
    """Same records as get_readiness_indicators(), filtered to one sheet --
    mnid.core.readiness_data.get_indicators_for_sheet() already does this
    against the raw JSON for the compute functions; this is the id-bearing
    equivalent for callers that want the same catalog shape as MoH/Nest360."""
    return [ind for ind in get_readiness_indicators() if ind["sheet"] == sheet_name]
