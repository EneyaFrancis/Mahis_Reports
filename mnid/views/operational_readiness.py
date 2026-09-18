"""Operational Readiness tab - EmONC-style facility readiness assessment.

Rebuilt against "Dashboard readiness v2.docx": 5 lazily-loaded sub-tabs
(Overview / Signal Functions / People / Products & Commodities / Systems &
Infrastructure). Each tab shows a single-facility detail view when exactly one
facility is in scope, or a comparison table across facilities otherwise -
matching the doc's section 9 interaction rules.

Signal Functions is the one section with real underlying data: the 9 WHO
EmONC signal functions already exist as ordinary MNID coverage indicators
(mnid/core/indicators.py, sub_category='signal_functions') - a facility's
status here is a re-interpretation of the same aggregate numerator/denominator
data every other MNID view already reads, not new computation infrastructure.
People / Products & Commodities / Systems & Infrastructure have no real MAHIS
data yet, so their rows are built the same way Nest360's not-yet-available
indicators are (mnid/dashboards/MNH-Nest360/indicators.py): present, properly
labeled "Not reported", ready to light up once real data exists - no
fabricated values.
"""
from __future__ import annotations

import pandas as pd
from dash import html, dcc, callback, dash_table, Input, Output, State, ctx, no_update
from dash.exceptions import PreventUpdate
import dash_mantine_components as dmc

from mnid.charts.chart_helpers import _cov, _grouped_filter_counts
from mnid.core.cache import _MNID_DATA_DISK_CACHE
from mnid.core.constants import FACILITY_NAMES, FACILITY_DISTRICT
from mnid.core.data_utils import resolve_facility_level, _remember_ui_payload, deserialize_store_df
from mnid.core.data_source import get_mnid_data_source
from mnid.views.executive_views import _profile_scope_name, _summary_card
from mnid.core.readiness_data import (
    is_readiness_data_available,
    compute_readiness_matrix,
    compute_readiness_detail,
    get_facility_emonc_classification,
)

GREEN = "#15803D"
AMBER = "#D97706"
RED = "#DC2626"
MUTED = "#64748B"
BORDER = "#E2E8F0"
SURFACE = "#FFFFFF"
BACKGROUND = "#F8FAFC"
TEXT = "#0F172A"

STATUS_COLORS = {
    "green": (GREEN, "#DCFCE7"),
    "amber": (AMBER, "#FEF3C7"),
    "red": (RED, "#FEE2E2"),
    "na": (MUTED, "#F1F5F9"),
    "awaiting": (MUTED, "#F1F5F9"),
    "unavailable": (MUTED, "#F1F5F9"),
    "plain": (TEXT, "#FFFFFF"),
}
STATUS_ICONS = {"green": "✓", "amber": "⚠", "red": "✕", "na": "–", "awaiting": "○"}

# A bare color name ("Red") tells the user a tone, not what's actually wrong or
# what to do about it - every context below pairs the same tone/icon with
# wording that actually says something. Nothing computes against these
# strings; they're display-only, so it's safe to word them for the audience
# rather than the color wheel.
SIGNAL_DETAIL_LABELS = {
    "green": f"{STATUS_ICONS['green']} Performed",
    "red": f"{STATUS_ICONS['red']} Not performed",
    "na": f"{STATUS_ICONS['na']} Not expected at this level",
    "unavailable": f"{STATUS_ICONS['na']} Not reported via this data source",
}
RATE_LABELS = {
    "green": f"{STATUS_ICONS['green']} On track",
    "amber": f"{STATUS_ICONS['amber']} Needs attention",
    "red": f"{STATUS_ICONS['red']} Below target",
}
AWAITING_LABEL = f"{STATUS_ICONS['awaiting']} Not yet reported"
EMONC_LABELS = {
    "CEmONC": f"{STATUS_ICONS['green']} CEmONC",
    "BEmONC": f"{STATUS_ICONS['amber']} BEmONC",
    "Unclassified": f"{STATUS_ICONS['red']} Unclassified",
}
EMONC_TONES = {"CEmONC": "green", "BEmONC": "amber", "Unclassified": "red"}

# ---------------------------------------------------------------------------
# The 9 WHO EmONC signal functions, already tracked as ordinary MNID coverage
# indicators (mnid/core/indicators.py:751-849, category='Labour',
# sub_category='signal_functions'). `comprehensive_only` marks the 2 that a
# Primary-level facility is not expected to perform (Table 4's N/A examples).
# ---------------------------------------------------------------------------
# `agg_label` is the exact "Signal: X" text stored in indicator_label in BOTH
# the default and DHIS2 aggregates - the MAHIS-side and DHIS2-side numerator
# rows for the same signal function are published under different
# indicator_id values (confirmed: mnid_lab_moh_028..036 don't exist at all in
# the DHIS2 aggregate), so resolve_indicator_id()'s label fallback is what
# actually finds the DHIS2-side data. Caesarean section's DHIS2 row is
# labeled "Overall caesarean section rate" instead (it reuses an existing
# MAHIS indicator's id/label rather than a dedicated "Signal: ..." row), so
# it needs an explicit id alias instead of a label match.
SIGNAL_FUNCTIONS = [
    {"id": "mnid_lab_moh_028", "label": "Parenteral antibiotics", "agg_label": "Signal: Parenteral antibiotics", "comprehensive_only": False},
    {"id": "mnid_lab_moh_029", "label": "Anticonvulsants (magnesium sulphate)", "agg_label": "Signal: Anticonvulsants (MgSO4)", "comprehensive_only": False},
    {"id": "mnid_lab_moh_030", "label": "Uterotonics (oxytocics)", "agg_label": "Signal: Oxytocics", "comprehensive_only": False},
    {"id": "mnid_lab_moh_031", "label": "Manual removal of placenta", "agg_label": "Signal: Manual placenta removal", "comprehensive_only": False},
    {"id": "mnid_lab_moh_032", "label": "Removal of retained products (MVA)", "agg_label": "Signal: MVA / retained products", "comprehensive_only": False},
    {"id": "mnid_lab_moh_033", "label": "Assisted vaginal delivery", "agg_label": "Signal: Assisted vaginal delivery", "comprehensive_only": False},
    {"id": "mnid_lab_moh_034", "label": "Newborn resuscitation (bag and mask)", "agg_label": "Signal: Neonatal resuscitation", "comprehensive_only": False},
    {"id": "mnid_lab_moh_035", "label": "Caesarean section", "agg_label": "Signal: Caesarean section", "dhis2_alias_id": "mnid_lab_prog_006", "comprehensive_only": True},
    {"id": "mnid_lab_moh_036", "label": "Blood transfusion", "agg_label": "Signal: Blood transfusion", "comprehensive_only": True},
]

# No per-client "performed" flag exists yet for these (doc Table 5) - shown as
# awaiting data, same convention as People/Products/Systems below.
NEWBORN_SIGNAL_FUNCTIONS = [
    "Initiate and support early and exclusive breastfeeding",
    "Resuscitate a newborn using a bag and mask",
    "Administer parenteral antibiotics to newborns",
    "Provide immediate Kangaroo Mother Care for preterm or low-birthweight newborns",
    "Provide thermal care using a radiant warmer or incubator",
    "Administer oxygen therapy with pulse oximetry",
    "Provide CPAP treatment",
    "Provide phototherapy",
    "Provide newborn blood transfusion",
    "Enable assisted feeding with expressed breast milk (cup, spoon or tube)",
    "Administer intravenous fluids",
    "Provide invasive mechanical ventilation",
    "Screen and treat retinopathy of prematurity",
]

CADRES_NEONATAL = ["Nurses/midwives", "Clinical officers", "General doctors",
                    "Paediatricians/neonatologists", "Data clerks"]
CADRES_MATERNITY = ["Anesthesiologists", "Anaesthetist technicians", "Clinical officers",
                     "General medical doctors", "Nurse-midwives/obstetric nurses",
                     "Nurse-midwife technicians", "Obstetrician-gynaecologists"]

TRACER_MEDICINES_MATERNITY = [
    ("Anemia prevention", "Iron supplementation"),
    ("Maternal nutrition", "Multiple micronutrient supplementation"),
    ("Postpartum hemorrhage", "Oxytocin injection"),
    ("Postpartum hemorrhage", "Misoprostol 200 microgram tablets"),
    ("Postpartum hemorrhage", "Tranexamic acid"),
    ("Pre-eclampsia/eclampsia", "Magnesium sulphate injection"),
    ("Pre-eclampsia/eclampsia", "Calcium gluconate injection"),
    ("Pre-eclampsia/eclampsia", "Hydralazine injection"),
    ("Maternal sepsis", "Injectable broad-spectrum antibiotic"),
    ("Preterm labour management", "Dexamethasone injection"),
    ("Fluid replacement", "Sodium chloride 0.9% IV solution"),
    ("Fluid replacement", "Ringer's lactate IV solution"),
]
TRACER_MEDICINES_NEWBORN = [
    ("Fluids and glucose management", "Dextrose 10%"),
    ("Fluid replacement", "Sodium chloride 0.9%"),
    ("Neonatal sepsis", "Gentamicin injection"),
    ("Neonatal sepsis", "Benzylpenicillin injection"),
    ("Neonatal sepsis", "Ampicillin injection"),
    ("Management of seizures", "Phenobarbitone injection"),
    ("Apnoea of prematurity", "Caffeine citrate"),
    ("Prevention of vitamin K deficiency bleeding", "Vitamin K1 injection"),
    ("Advanced neonatal resuscitation", "Adrenaline/epinephrine injection"),
    ("Emergency electrolyte management", "Calcium gluconate 10% injection"),
]
EQUIPMENT_MATERNITY = [
    ("Delivery care", "Delivery packs"),
    ("Postpartum hemorrhage", "Calibrated blood-loss measurement drapes"),
    ("Maternal sepsis", "FAST-M Charts"),
    ("Maternal monitoring", "Partograph"),
    ("Maternal monitoring", "Fetal stethoscopes/Pinards"),
    ("Maternal monitoring", "Fetal monitors/Dopplers"),
    ("Antenatal diagnostics", "Ultrasound scans"),
    ("Antenatal diagnostics", "Blood-pressure machine"),
    ("Newborn resuscitation", "Resuscitation table with heat source"),
    ("Newborn resuscitation", "Bag and mask, size 0"),
    ("Newborn resuscitation", "Bag and mask, size 1"),
]
EQUIPMENT_NEWBORN = [
    ("Glucose monitoring", "Glucometer"),
    ("Resuscitation", "Neonatal bag and masks, sizes 0 and 1"),
    ("Oxygen therapy", "Pulse oximeter"),
    ("Oxygen therapy", "Oxygen concentrator"),
    ("Oxygen therapy", "Oxygen cylinder"),
    ("Thermal care", "Incubator"),
    ("Thermal care", "Radiant warmer with probes"),
    ("Kangaroo Mother Care", "Designated KMC beds or spaces"),
    ("Jaundice care", "Phototherapy unit"),
    ("Jaundice care", "Bilirubinometer"),
    ("Respiratory support", "CPAP unit"),
]
INFRASTRUCTURE_MATERNITY = [
    ("Capacity", "Number of combined labour and delivery beds"),
    ("Physical environment", "Adequate lighting is available during the day and night"),
    ("Physical environment", "Adequate ventilation"),
    ("Surgical capacity", "Functional major operating theatre"),
    ("Surgical capacity", "Maternity theatre supported by backup power"),
    ("Respectful care infrastructure", "Labour companion permitted during delivery"),
    ("WASH and waste management", "Reliable running water available in maternity"),
    ("WASH and waste management", "Functional and private toilets available near maternity"),
    ("WASH and waste management", "Sharps container available"),
    ("WASH and waste management", "Functional autoclave available"),
]
INFRASTRUCTURE_NEONATAL = [
    ("Capacity", "Number of neonatal cots"),
    ("Capacity", "Total neonatal unit capacity (cots, warmers, incubators)"),
    ("Capacity", "Neonatal unit occupancy"),
    ("Capacity", "Number of KMC beds"),
    ("Spatial organisation", "Designated area for high-risk or acutely ill newborns"),
    ("Spatial organisation", "Isolation area for inborn newborns"),
    ("Power supply", "Stable electricity supply during the previous seven days"),
    ("Oxygen infrastructure", "Functional oxygen source available in the neonatal unit"),
    ("Family-centred care", "Mothers and caregivers allowed to visit at any time"),
    ("WASH and waste management", "Reliable running water available in the neonatal unit"),
]
REFERRAL_TRANSPORT = [
    "Dedicated neonatal transport cot/trolley with thermal protection and portable oxygen",
    "Functional motorised vehicle ambulances",
    "Sufficient fuel available to transport referrals",
    "Driver available",
    "Nurse or paramedic available to transport newborns today",
    "Routine preventive maintenance schedule available",
    "Person responsible for corrective maintenance of motor vehicles",
    "Fuel-management plan available",
]
DATA_QI_SYSTEMS = [
    "Maternity register available",
    "Newborn register available",
    "Electronic medical record for maternity",
    "Facility quality-improvement dashboard",
    "Maternity ward QI team available",
    "Neonatal care QI team available",
]


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

def _tone_pill(tone: str, text: str) -> html.Span:
    """A rounded, colored badge. `text` is the full display string (icon and
    all) - callers pick the wording from one of the *_LABELS maps above so
    each context reads like a message, not a color name."""
    color, bg = STATUS_COLORS.get(tone, (MUTED, "#F1F5F9"))
    return html.Span(text, style={
        "fontSize": "11px", "fontWeight": "700", "padding": "3px 12px",
        "borderRadius": "99px", "background": bg, "color": color, "display": "inline-block",
    })


def _tone_column_style(column_id: str, tones: list[str]) -> list[dict]:
    """Color one column's text per row by an explicit tone list (row order must
    match the table's rows) - for columns whose text varies per row rather than
    coming from one of the fixed *_LABELS maps, e.g. a rate row's "✓ On track · 83%"."""
    styles = []
    for i, tone in enumerate(tones):
        color, _ = STATUS_COLORS.get(tone, (TEXT, "#FFFFFF"))
        rule = {"if": {"row_index": i, "column_id": column_id}, "color": color, "fontWeight": "700"}
        if tone == "na":
            rule["backgroundColor"] = "#F1F5F9"
            rule["color"] = MUTED
            rule["fontWeight"] = "600"
        styles.append(rule)
    return styles


def _data_table(
    columns: list[str], rows: list[list],
    tone_column: str | None = None, tones: list[str] | None = None,
    classification_column: str | None = None,
    tooltips: list[dict] | None = None,
    filterable: bool = False,
) -> dash_table.DataTable:
    conditional_style = [{"if": {"row_index": "odd"}, "backgroundColor": "#FAFCFE"}]
    if tone_column and tones:
        conditional_style += _tone_column_style(tone_column, tones)
    if classification_column:
        # Built per EmONC tier (not a plain {tone: text} map) so it keeps
        # working unchanged if a future tier ever shares a tone with another.
        conditional_style += [
            {
                "if": {"filter_query": f'{{{classification_column}}} = "{label}"', "column_id": classification_column},
                "color": STATUS_COLORS[EMONC_TONES[key]][0], "fontWeight": "700",
            }
            for key, label in EMONC_LABELS.items()
        ]
    return dash_table.DataTable(
        data=[dict(zip(columns, row)) for row in rows],
        columns=[{"name": c, "id": c} for c in columns],
        page_size=15,
        sort_action="native",
        filter_action="native" if filterable else "none",
        tooltip_data=tooltips or [],
        tooltip_delay=250,
        tooltip_duration=None,
        style_as_list_view=True,
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": BACKGROUND, "fontWeight": 700,
            "borderBottom": f"1px solid {BORDER}", "color": MUTED,
            "fontSize": "11px", "textTransform": "uppercase", "letterSpacing": ".05em",
        },
        style_cell={
            "fontFamily": "Segoe UI, sans-serif", "fontSize": "12px",
            "padding": "10px 9px", "textAlign": "left",
            "borderBottom": f"1px solid {BORDER}",
            "maxWidth": "320px", "whiteSpace": "normal",
        },
        style_data_conditional=conditional_style,
        # dash_table's built-in filter-row inputs default to a pale pink
        # background (its way of marking them editable) that reads as
        # broken/unstyled next to the rest of this file's clean palette.
        css=[{"selector": ".dash-filter input", "rule":
              f"background-color: {SURFACE} !important; border: 1px solid {BORDER} !important; color: {TEXT} !important;"}],
    )


def _card(children, **style) -> dmc.Paper:
    base = {"borderColor": BORDER, "marginBottom": "16px"}
    base.update(style)
    return dmc.Paper(children, withBorder=True, radius="md", p="md", style=base)


def _section_title(text: str) -> html.Div:
    return html.Div([
        html.Span(style={
            "width": "4px", "height": "20px", "borderRadius": "99px",
            "background": GREEN, "flexShrink": "0",
        }),
        html.Span(text),
    ], style={
        "display": "flex", "alignItems": "center", "gap": "10px",
        "fontSize": "14px", "fontWeight": "800", "color": TEXT,
        "letterSpacing": ".025em", "textTransform": "uppercase",
        "marginBottom": "13px",
    })


def _resolve_data_source(route: str):
    """Same convention used throughout mnid/views/renderer.py and
    mnid/charts/coverage.py: scope_meta already carries a resolved 'route'
    ('default' or 'dhis2'), so wrap it in the shared MNIDDataSource for
    aggregate access/labeling instead of branching on the route string
    ourselves everywhere it's used."""
    return get_mnid_data_source(route, source="dhis2" if route == "dhis2" else "mahis")


def _facility_universe(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty or "Facility_CODE" not in df.columns:
        return []
    return sorted(df["Facility_CODE"].dropna().astype(str).unique().tolist())


def _source_facility_universe(df: pd.DataFrame, scope_meta: dict | None) -> list[str]:
    """Resolve facilities from raw MAHIS rows or the configured aggregate."""
    route = (scope_meta or {}).get("route", "default")
    data_source = _resolve_data_source(route)
    facilities = _facility_universe(df)
    if data_source.requires_raw_dataset and facilities:
        return facilities
    aggregate = data_source.aggregate()
    if aggregate is None or aggregate.empty or "facility_code" not in aggregate.columns:
        return facilities

    selected_districts = {
        str(value).strip().lower().replace(" district", "").replace(" dho", "")
        for value in (scope_meta or {}).get("selected_districts") or [] if value
    }
    if selected_districts and "district" in aggregate.columns:
        district_keys = aggregate["district"].fillna("").astype(str).str.strip().str.lower()
        district_keys = district_keys.str.replace(r"\s+(district|dho)$", "", regex=True)
        aggregate = aggregate[district_keys.isin(selected_districts)]
    facilities = sorted({str(code).strip() for code in aggregate["facility_code"].dropna() if str(code).strip()})

    selected = {str(value).strip() for value in (scope_meta or {}).get("selected_facilities") or [] if value}
    if selected:
        # FACILITY_NAMES is MAHIS-keyed and never matches a DHIS2 facility_code
        # -- resolve the selected names through the DHIS2 crosswalk instead so
        # a picked facility actually narrows this list, rather than always
        # coming up empty.
        from mnid.core.dhis2_facilities import dhis2_facility_codes_for_names
        selected_codes = set(dhis2_facility_codes_for_names(list(selected))) | selected
        facilities = [code for code in facilities if code in selected_codes or _facility_label(code) in selected]
    return facilities


def _facility_label(code: str) -> str:
    name = FACILITY_NAMES.get(code)
    if name:
        return name
    from mnid.core.dhis2_facilities import dhis2_code_to_name
    return dhis2_code_to_name().get(code, code)


def _facility_district(code: str) -> str:
    district = FACILITY_DISTRICT.get(code)
    if district:
        return district
    from mnid.core.dhis2_facilities import dhis2_code_to_district
    return dhis2_code_to_district().get(code, "")


_FACILITY_TYPE_BY_CODE: dict[str, str] | None = None


def _facility_type_by_code() -> dict[str, str]:
    """Load data/geo/facilities_levels.json once into {Facility_CODE: TYPE}
    (Central Hospital / District Hospital / Health Centre) - the same
    reference file mnid.core.data_utils.resolve_facility_level() reads,
    just keyed to the clinically-recognizable referral-level label instead
    of the Primary/Secondary/Tertiary tier derived from it."""
    global _FACILITY_TYPE_BY_CODE
    if _FACILITY_TYPE_BY_CODE is not None:
        return _FACILITY_TYPE_BY_CODE
    import json
    import os
    path = os.path.join(os.getcwd(), "data", "geo", "facilities_levels.json")
    try:
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        _FACILITY_TYPE_BY_CODE = {
            str(r.get("CODE")): r.get("TYPE") for r in records if r.get("CODE") and r.get("TYPE")
        }
    except Exception:
        _FACILITY_TYPE_BY_CODE = {}
    return _FACILITY_TYPE_BY_CODE


def _median_iqr(values: list[float], pct: bool = False) -> str | None:
    """'median [Q1-Q3]' formatted the same way as the source workbook -
    None (not 0) when the group has no facilities so the table shows
    "awaiting" rather than a fabricated zero."""
    if not values:
        return None
    s = pd.Series(values, dtype="float64")
    median, q1, q3 = s.median(), s.quantile(0.25), s.quantile(0.75)
    unit = "%" if pct else ""
    return f"{median:.0f}{unit} [{q1:.0f}-{q3:.0f}{unit}]"


def _resolve_aggregate_indicator_id(agg_df: pd.DataFrame | None, indicator_id: str,
                                     indicator_label: str | None = None,
                                     alias_id: str | None = None) -> str | None:
    """Resolve indicator_id against this route's aggregate: try the id as
    given, then a label match, then an explicit alias id (for the one case -
    caesarean section - where the DHIS2 row reuses an unrelated existing
    indicator's id/label rather than a dedicated one). Returns None if the
    aggregate has no data for this indicator under any of the three, which
    is distinct from "resolved fine but the numerator happens to be zero"."""
    if agg_df is None or agg_df.empty:
        return None
    from mnid.aggregation.store import resolve_indicator_id as _resolve_id, _resolve_lookup
    available_ids = _resolve_lookup(agg_df)["ids"]
    resolved = _resolve_id(agg_df, indicator_id, indicator_label)
    if resolved in available_ids:
        return resolved
    if alias_id and alias_id in available_ids:
        return alias_id
    return None


def _numerators_by_facility(indicator_id: str, numerator_filters: dict,
                             df: pd.DataFrame, agg_df: pd.DataFrame | None,
                             start_date, end_date, indicator_label: str | None = None,
                             alias_id: str | None = None) -> dict[str, int]:
    """{facility_code: numerator_count} for one indicator over the window.

    Prefers the pre-built aggregate (single filtered groupby, same fast path
    every other MNID view uses); falls back to a live groupby over raw rows
    when the aggregate isn't available - same resilience pattern used
    throughout mnid/views/trends.py."""
    if agg_df is not None and not agg_df.empty:
        from mnid.aggregation.store import _candidate_grains, _floor_to_period
        resolved = _resolve_aggregate_indicator_id(agg_df, indicator_id, indicator_label, alias_id)
        if resolved is None:
            # This route's aggregate has no data for this indicator at all
            # (checked by id, label, and alias) - every caller here passes
            # numerator_filters={} because it relies entirely on the
            # aggregate, so there is no meaningful raw-dataset fallback for
            # it. Returning {} (verified empty above: falling through used
            # to silently run _grouped_filter_counts with an empty filter,
            # which counts every row in the raw dataframe as "the
            # numerator" - a large bogus positive, not the intended zero).
            return {}
        grains = _candidate_grains("monthly")
        try:
            start_ts = pd.to_datetime(start_date) if start_date else agg_df["period_start"].min()
            end_ts = pd.to_datetime(end_date) if end_date else agg_df["period_start"].max()
            floor = min(_floor_to_period(start_ts, g) for g in grains)
            mask = (
                (agg_df["indicator_id"] == resolved)
                & (agg_df["grain"].isin(grains))
                & (agg_df["period_start"] >= floor)
                & (agg_df["period_start"] <= end_ts)
            )
            sub = agg_df[mask]
            return sub.groupby("facility_code")["numerator"].sum().astype(int).to_dict() if not sub.empty else {}
        except Exception:
            return {}
    if df is None or df.empty or "Facility_CODE" not in df.columns or not numerator_filters:
        return {}
    counts = _grouped_filter_counts(df, ["Facility_CODE"], numerator_filters)
    return {str(k): int(v) for k, v in counts.items()}


def _scope_view(facility_codes: list[str], detail_fn, comparison_fn):
    """Single facility in scope -> detail view; otherwise -> comparison view."""
    if len(facility_codes) == 1:
        return detail_fn(facility_codes[0])
    return comparison_fn(facility_codes)


# ---------------------------------------------------------------------------
# Signal Functions
# ---------------------------------------------------------------------------

def _signal_function_rows(facility_codes: list[str], df: pd.DataFrame,
                           agg_df: pd.DataFrame | None, start_date, end_date) -> dict:
    """{sig_id: {facility_code: numerator}} for all 9 signal functions."""
    return {
        sf["id"]: _numerators_by_facility(
            sf["id"], {}, df, agg_df, start_date, end_date,
            indicator_label=sf.get("agg_label"), alias_id=sf.get("dhis2_alias_id"),
        )
        for sf in SIGNAL_FUNCTIONS
    }


def _unavailable_signal_function_ids(agg_df: pd.DataFrame | None) -> set[str]:
    """Signal function ids this route's aggregate has no data for at all
    (checked by id, label, and alias) - distinct from a specific facility
    simply not performing one. Computed once per render, since availability
    is a property of the data source/route, not of any one facility. Empty
    for the raw-dataset fallback path (agg_df absent), since availability
    there is inherently per-row rather than per-route.
    """
    if agg_df is None or agg_df.empty:
        return set()
    return {
        sf["id"] for sf in SIGNAL_FUNCTIONS
        if _resolve_aggregate_indicator_id(agg_df, sf["id"], sf.get("agg_label"), sf.get("dhis2_alias_id")) is None
    }


def _facility_status(sf: dict, numerators: dict, code: str, level: str,
                      unavailable_ids: frozenset[str] = frozenset()) -> str:
    if sf["id"] in unavailable_ids:
        return "unavailable"
    if sf["comprehensive_only"] and level == "Primary":
        return "na"
    return "green" if numerators.get(code, 0) > 0 else "red"


def _classify_emonc(numerators_by_sig: dict, code: str, level: str,
                     unavailable_ids: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    """WHO/UNFPA/UNICEF EmONC classification: BEmONC requires all 7 basic
    signal functions performed in the period; CEmONC requires all 7 plus the
    2 comprehensive-only ones (surgery, blood transfusion). A facility that's
    "na" (not expected/equipped) for a comprehensive function still can't
    qualify as CEmONC - na is never treated as performed.

    Gap analysis: a facility missing exactly one basic function still reports
    as "BEmONC" (not "Unclassified") but with the missing function named in
    the second return value, so a single-gap near-miss stays visible without
    splitting the tier list into a fourth "BEmONC-1" bucket the user found
    more confusing than useful once they saw it in the live table.

    Functions this route can't report at all (unavailable_ids - e.g. neonatal
    resuscitation under the DHIS2 route, which has no mapping at all) are
    excluded from the required-function gate entirely, rather than counted
    as a failure the facility gets penalized for - a data-source gap isn't
    evidence the facility doesn't perform the function.

    Returns (classification_key, missing_function_label_or_empty_string,
    note_or_empty_string) - note explains when/why the gate ran on fewer
    than the full set of functions.
    """
    statuses = {sf["id"]: _facility_status(sf, numerators_by_sig[sf["id"]], code, level, unavailable_ids) for sf in SIGNAL_FUNCTIONS}
    basic = [sf for sf in SIGNAL_FUNCTIONS if not sf["comprehensive_only"]]
    comprehensive = [sf for sf in SIGNAL_FUNCTIONS if sf["comprehensive_only"]]
    gated_basic = [sf for sf in basic if sf["id"] not in unavailable_ids]
    gated_comprehensive = [sf for sf in comprehensive if sf["id"] not in unavailable_ids]
    missing_basic = [sf["label"] for sf in gated_basic if statuses[sf["id"]] != "green"]
    missing_comprehensive = [sf["label"] for sf in gated_comprehensive if statuses[sf["id"]] != "green"]
    note = ""
    excluded = [sf["label"] for sf in SIGNAL_FUNCTIONS if sf["id"] in unavailable_ids]
    if excluded:
        note = (f"Classification based on {len(gated_basic)} of {len(basic)} basic functions "
                f"({', '.join(excluded)} not reported via this data source)")
    if not missing_basic and not missing_comprehensive:
        return "CEmONC", "", note
    if not missing_basic:
        return "BEmONC", "", note
    if len(missing_basic) == 1:
        return "BEmONC", missing_basic[0], note
    return "Unclassified", ", ".join(missing_basic), note


def _get_facility_classification(code: str, numerators_by_sig: dict, level: str,
                                 unavailable_ids: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    """Get EmONC classification for a facility, prioritizing the HFA survey's sd_del_emonc2 classification."""
    if is_readiness_data_available():
        hfa_cls = get_facility_emonc_classification()
        # 1. Direct facility_code lookup
        if code in hfa_cls:
            return hfa_cls[code], "", "Classification from Health Facility Assessment (HFA)"
        # 2. Facility label / name lookup
        label = _facility_label(code)
        if label in hfa_cls:
            return hfa_cls[label], "", "Classification from Health Facility Assessment (HFA)"
        # 3. Normalized name lookup
        from mnid.core.readiness_data import _normalize_name
        norm_label = _normalize_name(label)
        if norm_label in hfa_cls:
            return hfa_cls[norm_label], "", "Classification from Health Facility Assessment (HFA)"
        norm_code = _normalize_name(code)
        if norm_code in hfa_cls:
            return hfa_cls[norm_code], "", "Classification from Health Facility Assessment (HFA)"

    # Fallback to referral level classification or signal function gate
    if level in ["Secondary", "Tertiary"]:
        return "CEmONC", "", f"Classification based on facility tier ({level})"
    elif level == "Primary":
        return "BEmONC", "", f"Classification based on facility tier ({level})"
    return _classify_emonc(numerators_by_sig, code, level, unavailable_ids)


def _matrix_tone(pct: float | None) -> str:
    if pct is None:
        return "awaiting"
    return "green" if pct >= 80 else "amber" if pct >= 50 else "red"


def _matrix_cell(pct: float | int | str | None, detail: str | None = None) -> html.Td:
    """One traffic-light cell: a solid tone fill with white text for percentages,
    or a grey background with 'N/A' for CEmONC-only / not applicable items,
    or a plain tabular cell for median-IQR metrics. `detail` becomes the native
    hover tooltip via the HTML title attribute."""
    common = {"textAlign": "center", "padding": "9px 10px", "fontSize": "12px"}
    if pct in (None, ""):
        return html.Td(STATUS_ICONS["awaiting"], title=detail, style={
            **common, "color": MUTED, "background": BACKGROUND, "borderBottom": f"1px solid {BORDER}",
        })
    if isinstance(pct, str) and pct.strip().upper() in ["N/A", "NOT APPLICABLE", "NA", "–", "-"]:
        return html.Td("N/A", title=detail or "Not applicable for BEmONC facilities (CEmONC-only indicator)", style={
            **common, "color": MUTED, "background": "#F1F5F9", "borderBottom": f"1px solid {BORDER}",
            "fontWeight": "600", "fontSize": "11px",
        })
    if isinstance(pct, (int, float)):
        tone = _matrix_tone(float(pct))
        color, _ = STATUS_COLORS[tone]
        return html.Td(f"{float(pct):.0f}%", title=detail, style={
            **common, "fontWeight": "700",
            "color": "#FFFFFF", "background": color, "borderBottom": f"1px solid {SURFACE}",
        })
    return html.Td(str(pct), title=detail, style={
        **common, "fontWeight": "600",
        "color": TEXT, "borderBottom": f"1px solid {BORDER}", "fontVariantNumeric": "tabular-nums",
    })


def _plain_cell(value: str | None, detail: str | None = None) -> html.Td:
    """A plain, uncolored cell for figures with no target to traffic-light
    against - facility counts and median [IQR] service-volume statistics.
    Coloring "928 [546-2260] deliveries" green/amber/red would imply a
    performance judgment the number doesn't carry."""
    common = {"textAlign": "center", "padding": "9px 10px", "fontSize": "12px"}
    if value in (None, ""):
        return html.Td(STATUS_ICONS["awaiting"], title=detail, style={
            **common, "color": MUTED, "borderBottom": f"1px solid {BORDER}",
        })
    if isinstance(value, str) and value.strip().upper() in ["N/A", "NOT APPLICABLE", "NA", "–", "-"]:
        return html.Td("N/A", title=detail or "Not applicable for BEmONC facilities (CEmONC-only indicator)", style={
            **common, "color": MUTED, "background": "#F1F5F9", "borderBottom": f"1px solid {BORDER}",
            "fontWeight": "600", "fontSize": "11px",
        })
    return html.Td(value, title=detail, style={
        **common, "fontWeight": "600",
        "color": TEXT, "borderBottom": f"1px solid {BORDER}", "fontVariantNumeric": "tabular-nums",
    })


_DEFAULT_MATRIX_COLUMNS = [("CEmONC", "cemonc"), ("BEmONC", "bemonc")]


def _matrix_table(rows: list[dict], columns: list[tuple[str, str]] | None = None,
                   cell_fn=None, label_column: str = "Item") -> html.Div:
    """An (label_column | col1 | col2 | ...) matrix - the standard shape for
    every Operational Readiness comparison table. `columns` is a list of
    (header label, row key) pairs - defaults to the CEmONC/BEmONC 2-column
    shape used everywhere else; pass a 3-tuple list (e.g. CEmONC/BEmONC/Total)
    where a reconciling total matters. `rows` is
    [{"label", "category" (optional), <row key>: ..., <row key>_detail (optional
    hover text): ..., ...}, ...]; a category divider row renders whenever
    `category` changes from the row before it, mirroring the section headers
    already used in the source workbook (Antenatal diagnostics, Delivery
    care, Postpartum haemorrhage...). `cell_fn` defaults to the traffic-light
    percentage cell; pass `_plain_cell` for counts/median-IQR rows that have
    no target to color against. Value columns each get a fixed 20% share (so
    they read as evenly spread regardless of how wide the parent card is);
    `label_column` takes whatever's left - name it for what the rows actually
    are (Signal Function, Cadre, Commodity, Facility Type...), never the bare
    generic "Item".
    """
    if not rows:
        return html.Div(
            "No indicators with reported data currently available for the selected facilities.",
            style={"fontSize": "12px", "color": MUTED, "padding": "12px 8px"},
        )
    columns = columns or _DEFAULT_MATRIX_COLUMNS
    cell_fn = cell_fn or _matrix_cell
    span = 1 + len(columns)
    value_col_width = "20%"
    item_col_width = f"{max(100 - 20 * len(columns), 30)}%"
    header = html.Tr([
        html.Th(label_column, style={
            "textAlign": "left", "padding": "9px 10px", "fontSize": "10px", "fontWeight": "700",
            "color": MUTED, "textTransform": "uppercase", "letterSpacing": ".05em", "width": item_col_width,
            "background": BACKGROUND, "borderBottom": f"1px solid {BORDER}", "position": "sticky", "top": 0,
        }),
        *[html.Th(label, style={
            "textAlign": "center", "padding": "9px 10px", "fontSize": "10px", "fontWeight": "700",
            "color": MUTED, "textTransform": "uppercase", "letterSpacing": ".05em", "width": value_col_width,
            "background": BACKGROUND, "borderBottom": f"1px solid {BORDER}", "position": "sticky", "top": 0,
        }) for label, _ in columns],
    ])
    body = []
    last_category = object()
    for row in rows:
        category = row.get("category")
        if category is not None and category != last_category:
            body.append(html.Tr([html.Td(category, colSpan=span, style={
                "padding": "7px 10px", "fontSize": "10.5px", "fontWeight": "700", "color": TEXT,
                "background": BACKGROUND, "borderBottom": f"1px solid {BORDER}", "borderTop": f"1px solid {BORDER}",
            })]))
        last_category = category
        body.append(html.Tr([
            html.Td(row["label"], style={
                "padding": "9px 10px", "fontSize": "12px", "color": TEXT, "borderBottom": f"1px solid {BORDER}",
            }),
            *[cell_fn(row.get(key), row.get(f"{key}_detail")) for _, key in columns],
        ]))
    return html.Div(html.Table([html.Thead(header), html.Tbody(body)], style={
        "width": "100%", "borderCollapse": "collapse", "background": SURFACE, "tableLayout": "fixed",
    }), style={"overflowX": "auto"})


def _signal_functions_newborn_detail(code: str) -> html.Div:
    if is_readiness_data_available():
        hfa_sf = compute_readiness_detail("SF", code)
        nb_hfa = [r for r in hfa_sf if r.get("category") == "Newborn signal functions"]
        if nb_hfa:
            rows = []
            for r in nb_hfa:
                st = r["status"]
                disp = r["display_value"]
                lbl = "N/A" if st == "na" else (SIGNAL_DETAIL_LABELS.get(st, disp) if st in SIGNAL_DETAIL_LABELS else disp)
                rows.append(html.Div([
                    html.Span(r["label"], style={"fontSize": "12px", "color": TEXT, "flex": "1"}),
                    _tone_pill(st, lbl),
                ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "padding": "8px 0", "borderBottom": f"1px solid {BORDER}"}))
            return html.Div(rows)
    return html.Div("No newborn signal function data currently available for this facility.", style={"fontSize": "12px", "color": MUTED, "padding": "8px 0"})


def _signal_functions_detail(code: str, numerators_by_sig: dict, df: pd.DataFrame,
                              unavailable_ids: frozenset[str] = frozenset()) -> html.Div:
    level = resolve_facility_level(code, _facility_label(code))
    rows = []
    for sf in SIGNAL_FUNCTIONS:
        status = _facility_status(sf, numerators_by_sig[sf["id"]], code, level, unavailable_ids)
        if status == "na":
            rows.append(html.Div([
                html.Span(sf["label"], style={"fontSize": "12px", "color": TEXT, "flex": "1"}),
                _tone_pill("na", "N/A"),
            ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "padding": "8px 0", "borderBottom": f"1px solid {BORDER}"}))
        else:
            rows.append(html.Div([
                html.Span(sf["label"], style={"fontSize": "12px", "color": TEXT, "flex": "1"}),
                _tone_pill(status, SIGNAL_DETAIL_LABELS[status]),
            ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "padding": "8px 0", "borderBottom": f"1px solid {BORDER}"}))
    classification, missing, note = _get_facility_classification(code, numerators_by_sig, level, unavailable_ids)
    classification_line = [
        html.Span(f"{_facility_district(code)} · {level} · ", style={"fontSize": "11px", "color": MUTED}),
        _tone_pill(EMONC_TONES[classification], EMONC_LABELS[classification]),
    ]
    if missing:
        classification_line.append(html.Span(f" · missing: {missing}", style={"fontSize": "11px", "color": MUTED, "marginLeft": "6px"}))
    header_children = [
        html.Div(_facility_label(code), style={"fontSize": "15px", "fontWeight": 800, "color": TEXT}),
        html.Div(classification_line, style={"marginTop": "4px", "display": "flex", "alignItems": "center"}),
    ]
    if note:
        header_children.append(html.Div(note, style={"fontSize": "10px", "color": MUTED, "marginTop": "4px"}))
    return html.Div([
        _card([
            html.Div(header_children, style={"marginBottom": "12px"}),
            _section_title("Maternal Signal Functions"),
            html.Div(rows),
        ]),
        _card([
            _section_title("Newborn Signal Functions"),
            _signal_functions_newborn_detail(code),
        ]),
    ])


def _signal_functions_comparison(facility_codes: list[str], numerators_by_sig: dict,
                                  unavailable_ids: frozenset[str] = frozenset()) -> html.Div:
    """Two service-area matrices (Maternal / Newborn), each showing what
    fraction of the CEmONC-classified and BEmONC-classified facilities in
    scope actually perform each signal function - the same "EmONC type"
    cross-tab convention as the source workbook, without the per-facility
    detail a national summary doesn't need.
    """
    classifications = {
        code: _get_facility_classification(code, numerators_by_sig, resolve_facility_level(code, _facility_label(code)), unavailable_ids)[0]
        for code in facility_codes
    }
    cemonc_group = [c for c in facility_codes if classifications[c] == "CEmONC"]
    bemonc_group = [c for c in facility_codes if classifications[c] == "BEmONC"]

    def _group_pct(sf: dict, group: list[str], group_label: str) -> tuple[float | str | None, str | None]:
        if sf["id"] in unavailable_ids or not group:
            return None, None
        performing = sum(1 for code in group if numerators_by_sig[sf["id"]].get(code, 0) > 0)
        pct = round(performing / len(group) * 100, 1)
        return pct, f"{performing} of {len(group)} {group_label}-classified facilities performing"

    maternal_rows = []
    for sf in SIGNAL_FUNCTIONS:
        cemonc_pct, cemonc_detail = _group_pct(sf, cemonc_group, "CEmONC")
        if sf.get("comprehensive_only"):
            bemonc_pct = "N/A"
            bemonc_detail = "Not applicable for BEmONC facilities (CEmONC-only indicator)"
        else:
            bemonc_pct, bemonc_detail = _group_pct(sf, bemonc_group, "BEmONC")
        # Deactivate / remove variable if there is no data for both CEmONC and BEmONC
        if cemonc_pct is None and (bemonc_pct is None or (sf.get("comprehensive_only") and bemonc_pct == "N/A")):
            continue
        maternal_rows.append({
            "label": sf["label"],
            "cemonc": cemonc_pct, "cemonc_detail": cemonc_detail,
            "bemonc": bemonc_pct, "bemonc_detail": bemonc_detail,
        })
    if is_readiness_data_available():
        nb_matrix = compute_readiness_matrix("SF", facility_codes, cemonc_codes=cemonc_group, bemonc_codes=bemonc_group)
        newborn_rows = [r for r in nb_matrix if r.get("category") == "Newborn signal functions"]
    else:
        newborn_rows = []

    note_children = [
        f"Share of {len(cemonc_group)} CEmONC- and {len(bemonc_group)} BEmONC-classified facilities in scope "
        "performing each function in the reporting period.",
    ]
    if unavailable_ids:
        excluded_labels = [sf["label"] for sf in SIGNAL_FUNCTIONS if sf["id"] in unavailable_ids]
        note_children.append(html.Br())
        note_children.append(f"{', '.join(excluded_labels)}: not reported via this data source.")

    newborn_card_content = [
        _section_title("Newborn Signal Functions"),
    ]
    if newborn_rows:
        newborn_card_content.append(_matrix_table(newborn_rows, label_column="Signal Function"))
    else:
        newborn_card_content.append(html.Div(
            "No newborn signal function data currently available for facilities in scope.",
            style={"fontSize": "12px", "color": MUTED, "padding": "8px 0"},
        ))

    return html.Div([
        _card([
            _section_title("Maternal Signal Functions"),
            _matrix_table(maternal_rows, label_column="Signal Function"),
            html.Div(note_children, style={"fontSize": "10px", "color": MUTED, "marginTop": "8px"}),
        ]),
        _card(newborn_card_content),
    ])


def _build_signal_functions_tab(facility_codes: list[str], df: pd.DataFrame,
                                 agg_df: pd.DataFrame | None, start_date, end_date) -> html.Div:
    numerators_by_sig = _signal_function_rows(facility_codes, df, agg_df, start_date, end_date)
    unavailable_ids = frozenset(_unavailable_signal_function_ids(agg_df))
    return _scope_view(
        facility_codes,
        detail_fn=lambda code: _signal_functions_detail(code, numerators_by_sig, df, unavailable_ids),
        comparison_fn=lambda codes: _signal_functions_comparison(codes, numerators_by_sig, unavailable_ids),
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

_FACILITY_TYPE_ORDER = ["Central Hospital", "District Hospital", "Health Centre"]


_PROFILE_STATS_COLUMNS = [
    ("CEmONC Facilities", "cemonc"), ("BEmONC Facilities", "bemonc"), ("All Facilities", "total"),
]


def _facility_profile_rows(all_codes: list[str], cemonc_group: list[str], bemonc_group: list[str]) -> list[dict]:
    """Central/District Hospital vs Health Centre facility counts, Total and
    per EmONC group - re-expresses the same referral-level tier that already
    drives Primary/Secondary/Tertiary EmONC eligibility, under the
    clinically-recognizable label the source workbook uses, rather than new
    data. Total is every facility in scope, not just cemonc_group +
    bemonc_group - CEmONC/BEmONC only cover facilities that qualify for one
    of those tiers, so Total is what still accounts for Unclassified
    facilities without breaking them out on their own."""
    type_by_code = _facility_type_by_code()

    def _count_and_detail(group: list[str], kind: str, group_label: str) -> tuple[str, str | None]:
        n = sum(1 for c in group if type_by_code.get(c) == kind)
        detail = f"{n} of {len(group)} {group_label} facilities" if group else None
        return str(n), detail

    rows = []
    for kind in _FACILITY_TYPE_ORDER:
        total_n, total_detail = _count_and_detail(all_codes, kind, "total")
        cemonc_n, cemonc_detail = _count_and_detail(cemonc_group, kind, "CEmONC")
        bemonc_n, bemonc_detail = _count_and_detail(bemonc_group, kind, "BEmONC")
        rows.append({
            "label": kind,
            "total": total_n, "total_detail": total_detail,
            "cemonc": cemonc_n, "cemonc_detail": cemonc_detail,
            "bemonc": bemonc_n, "bemonc_detail": bemonc_detail,
        })
    return rows


def _service_stats_rows(all_codes: list[str], cemonc_group: list[str], bemonc_group: list[str],
                         births_by_facility: dict, caesareans_by_facility: dict,
                         admissions_by_facility: dict, admissions_available: bool) -> list[dict]:
    """Deliveries / caesareans / caesarean rate / neonatal admissions as
    median [IQR] across facilities - Total (every facility in scope) plus
    each EmONC group - for the current reporting period. The source
    workbook's "Service-specific statistics" section, showing spread across
    facilities rather than a single national sum (which the summary cards
    above already give)."""
    def _vals(mapping: dict, group: list[str]) -> list[float]:
        return [mapping.get(c, 0) for c in group]

    def _rates(group: list[str]) -> list[float]:
        return [
            caesareans_by_facility.get(c, 0) / births_by_facility[c] * 100
            for c in group if births_by_facility.get(c)
        ]

    def _detail(group: list[str], group_label: str) -> str:
        return f"Median across {len(group)} {group_label} facilities"

    def _group_row(label: str, mapping: dict | None = None, rate: bool = False, available: bool = True) -> dict:
        if not available:
            return {"label": label, "total": None, "cemonc": None, "bemonc": None}
        values_fn = _rates if rate else (lambda group: _vals(mapping, group))
        kwargs = {"pct": True} if rate else {}
        return {
            "label": label,
            "total": _median_iqr(values_fn(all_codes), **kwargs), "total_detail": _detail(all_codes, "total"),
            "cemonc": _median_iqr(values_fn(cemonc_group), **kwargs), "cemonc_detail": _detail(cemonc_group, "CEmONC"),
            "bemonc": _median_iqr(values_fn(bemonc_group), **kwargs), "bemonc_detail": _detail(bemonc_group, "BEmONC"),
        }

    return [
        _group_row("Deliveries in period", births_by_facility),
        _group_row("Caesarean deliveries in period", caesareans_by_facility),
        _group_row("Caesarean delivery rate", rate=True),
        _group_row("Neonatal unit admissions in period", admissions_by_facility, available=admissions_available),
    ]


_CLASSIFICATION_FILTER_OPTIONS = [{"label": "All classifications", "value": "All"}] + [
    {"label": EMONC_LABELS[key], "value": key} for key in ("CEmONC", "BEmONC", "Unclassified")
]


def _facility_comparison_records(facility_codes: list[str], classifications: dict,
                                  births_by_facility: dict, caesareans_by_facility: dict,
                                  admissions_by_facility: dict, admissions_available: bool) -> list[dict]:
    """One plain-dict record per facility, stashed in a dcc.Store so the
    classification dropdown filter can re-slice and re-render just this
    table client round-trip, without recomputing the whole Overview tab.
    District is deliberately not a column here - the facility name is
    already unique, and the district filter/scope band above already says
    what's in scope, so repeating it on every row added nothing."""
    return [
        {
            "facility": _facility_label(code),
            "level": resolve_facility_level(code, _facility_label(code)),
            "classification": classifications[code],
            "deliveries": births_by_facility.get(code, 0),
            "caesareans": caesareans_by_facility.get(code, 0),
            "admissions": admissions_by_facility.get(code, 0) if admissions_available else None,
        }
        for code in facility_codes
    ]


def _facility_comparison_table(records: list[dict]) -> dash_table.DataTable:
    """Facility Readiness Comparison rows plus a bold Total row summing
    whatever's currently shown, so a classification-filtered view still
    answers "how much, in total" without switching to a different table."""
    rows = [
        [
            r["facility"], r["level"], EMONC_LABELS[r["classification"]],
            r["deliveries"], r["caesareans"],
            r["admissions"] if r["admissions"] is not None else AWAITING_LABEL,
        ]
        for r in records
    ]
    if records:
        any_admissions = any(r["admissions"] is not None for r in records)
        rows.append([
            f"Total · {len(records)} facilities", "", "",
            sum(r["deliveries"] for r in records),
            sum(r["caesareans"] for r in records),
            sum(r["admissions"] for r in records if r["admissions"] is not None) if any_admissions else AWAITING_LABEL,
        ])
    return _data_table(
        ["Facility", "Facility level", "EmONC classification",
         "Total deliveries", "Caesarean deliveries", "Neonatal unit admissions"],
        rows,
        classification_column="EmONC classification",
        filterable=False,
    )


def _build_overview_tab(facility_codes: list[str], df: pd.DataFrame,
                         agg_df: pd.DataFrame | None, start_date, end_date) -> html.Div:
    numerators_by_sig = _signal_function_rows(facility_codes, df, agg_df, start_date, end_date)
    unavailable_ids = frozenset(_unavailable_signal_function_ids(agg_df))
    classification_results = {
        code: _get_facility_classification(code, numerators_by_sig, resolve_facility_level(code, _facility_label(code)), unavailable_ids)
        for code in facility_codes
    }
    classifications = {code: result[0] for code, result in classification_results.items()}
    missing_by_facility = {code: result[1] for code, result in classification_results.items()}
    classification_note = next((result[2] for result in classification_results.values() if result[2]), "")
    bemonc = sum(1 for v in classifications.values() if v == "BEmONC")
    cemonc = sum(1 for v in classifications.values() if v == "CEmONC")

    births_by_facility = _numerators_by_facility("mnid_lab_core_totalbirths", {}, df, agg_df, start_date, end_date)
    caesarean_sf = next(sf for sf in SIGNAL_FUNCTIONS if sf["id"] == "mnid_lab_moh_035")
    caesareans_by_facility = _numerators_by_facility(
        "mnid_lab_moh_035", {}, df, agg_df, start_date, end_date,
        indicator_label=caesarean_sf.get("agg_label"), alias_id=caesarean_sf.get("dhis2_alias_id"),
    )
    # "mnid_lab_overview_004" is Live Births, not newborn-unit admissions -
    # reusing it here previously made this card track Total Deliveries almost
    # 1:1 (nearly every delivery has a live birth), which reads as nonsense
    # for a "unit admissions" figure. mnid_nb_core_admissions is the real
    # Neonatal Admissions indicator (2026-07 workbook refresh), but it has no
    # MAHIS-side counterpart yet and hasn't been through a live DHIS2 publish
    # (indicators.json/sample_sync.py wiring is in place; the parquet on disk
    # predates it) - so it's genuinely unavailable on both routes today.
    # Detecting that (rather than defaulting to 0) keeps this honest instead
    # of just swapping one wrong number for a different wrong number.
    admissions_available = _resolve_aggregate_indicator_id(agg_df, "mnid_nb_core_admissions") is not None
    admissions_by_facility = _numerators_by_facility("mnid_nb_core_admissions", {}, df, agg_df, start_date, end_date) if admissions_available else {}
    total_births = sum(births_by_facility.values())
    caesareans = sum(caesareans_by_facility.values())
    admissions = sum(admissions_by_facility.values())

    summary_cards = [
        _summary_card("BEmONC facilities", str(bemonc), "Basic EmONC classification", AMBER),
        _summary_card("CEmONC facilities", str(cemonc), "Comprehensive EmONC classification", "#7C3AED"),
        _summary_card("Total deliveries", f"{total_births:,}", "Reported in selected period", GREEN),
        _summary_card("Total caesarean deliveries", f"{caesareans:,}", "Caesarean sections performed", "#DB2777"),
        _summary_card(
            "Neonatal unit admissions", f"{admissions:,}" if admissions_available else AWAITING_LABEL,
            "Admitted to newborn/neonatal care unit" if admissions_available else "Not yet published for this data source",
            AMBER,
        ),
    ]
    summary = html.Div([
        _section_title("Readiness Summary · Current Reporting Period"),
        html.Div(summary_cards, style={
            "display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(190px, 1fr))",
            "gap": "14px", "marginBottom": "20px",
        }),
    ])

    if len(facility_codes) == 1:
        code = facility_codes[0]
        classification = classifications[code]
        missing = missing_by_facility[code]
        header_children = [
            html.Span(f"{_facility_district(code)} · {resolve_facility_level(code, _facility_label(code))} · ",
                      style={"fontSize": "12px", "color": MUTED}),
            _tone_pill(EMONC_TONES[classification], EMONC_LABELS[classification]),
        ]
        if missing:
            header_children.append(html.Span(f" · missing: {missing}", style={"fontSize": "12px", "color": MUTED, "marginLeft": "6px"}))
        detail_children = [
            _section_title(_facility_label(code)),
            html.Div(header_children, style={"display": "flex", "alignItems": "center"}),
        ]
        if classification_note:
            detail_children.append(html.Div(classification_note, style={"fontSize": "10px", "color": MUTED, "marginTop": "4px"}))
        detail = _card(detail_children)
        return html.Div([summary, detail])

    cemonc_group = [c for c in facility_codes if classifications[c] == "CEmONC"]
    bemonc_group = [c for c in facility_codes if classifications[c] == "BEmONC"]

    profile = html.Div([
        _section_title("Facility Profile"),
        _card([_matrix_table(
            _facility_profile_rows(facility_codes, cemonc_group, bemonc_group),
            columns=_PROFILE_STATS_COLUMNS, cell_fn=_plain_cell, label_column="Facility Type",
        )]),
    ])
    stats = html.Div([
        _section_title("Service Statistics · Median [IQR] Across Facilities"),
        _card([_matrix_table(
            _service_stats_rows(facility_codes, cemonc_group, bemonc_group, births_by_facility,
                                 caesareans_by_facility, admissions_by_facility, admissions_available),
            columns=_PROFILE_STATS_COLUMNS, cell_fn=_plain_cell, label_column="Indicator",
        )]),
    ])
    comparison_records = _facility_comparison_records(
        facility_codes, classifications, births_by_facility, caesareans_by_facility,
        admissions_by_facility, admissions_available,
    )
    comparison = html.Div([
        _section_title("Facility Readiness Comparison"),
        _card([
            html.Div([
                html.Label("Filter by classification:", style={
                    "fontSize": "11px", "fontWeight": 700, "color": MUTED, "marginRight": "8px",
                }),
                dcc.Dropdown(
                    id="operational-readiness-classification-filter",
                    options=_CLASSIFICATION_FILTER_OPTIONS,
                    value="All",
                    clearable=False,
                    style={"minWidth": "220px", "fontSize": "12px"},
                ),
            ], style={"display": "flex", "alignItems": "center", "marginBottom": "12px"}),
            dcc.Store(id="operational-readiness-comparison-records", data=comparison_records),
            html.Div(
                id="operational-readiness-comparison-table-container",
                children=_facility_comparison_table(comparison_records),
            ),
        ]),
    ])
    return html.Div([summary, profile, stats, comparison])


# ---------------------------------------------------------------------------
# People / Products / Systems
# ---------------------------------------------------------------------------

def _awaiting_matrix_rows(items: list) -> list[dict]:
    """CEmONC/BEmONC matrix rows with no real per-item data yet - every cell
    renders as 'awaiting'."""
    if items and isinstance(items[0], tuple):
        return [{"label": label, "category": category, "cemonc": None, "bemonc": None} for category, label in items]
    return [{"label": label, "cemonc": None, "bemonc": None} for label in items]


def _awaiting_detail_table(items: list[str], label_column: str = "Cadre") -> dash_table.DataTable:
    rows = [[cadre, AWAITING_LABEL] for cadre in items]
    tones = ["awaiting"] * len(rows)
    return _data_table([label_column, "Status"], rows, tone_column="Status", tones=tones)


def _awaiting_domain_detail_table(items: list[tuple[str, str]], label_column: str = "Commodity") -> dash_table.DataTable:
    rows = [[cat, label, AWAITING_LABEL] for cat, label in items]
    tones = ["awaiting"] * len(rows)
    return _data_table(["Category", label_column, "Status"], rows, tone_column="Status", tones=tones)


def _real_indicator_rows(indicators: list[dict], df: pd.DataFrame, agg_df: pd.DataFrame | None,
                          start_date, end_date, facility_codes: list[str]) -> tuple[list[list], list[str], list[dict]]:
    rows, tones, tooltips = [], [], []
    for ind in indicators:
        if not isinstance(ind, dict) or "id" not in ind:
            continue
        label = ind.get("name") or ind.get("label", ind["id"])
        try:
            num, den, rate = _cov(df, ind["id"], facility_codes)
        except Exception:
            num, den, rate = 0, 0, None
        if rate is not None:
            pct = round(rate * 100, 1)
            tone = "green" if pct >= 80 else "amber" if pct >= 50 else "red"
            rows.append([label, f"{den:,}", f"{RATE_LABELS[tone]} · {pct:.0f}%"])
            tones.append(tone)
            tooltips.append({"Result": {
                "value": f"{num:,} out of {den:,} assessed records", "type": "text",
            }})
        else:
            rows.append([label, f"{den:,}", AWAITING_LABEL])
            tones.append("awaiting")
    return rows, tones, tooltips


def _hfa_detail_table(sheet_name: str, facility_code: str, label_column: str = "Indicator") -> dash_table.DataTable:
    detail_records = compute_readiness_detail(sheet_name, facility_code)
    if not detail_records:
        return _awaiting_detail_table([], label_column=label_column)
    rows = []
    tones = []
    tooltips = []
    has_category = any(r.get("category") for r in detail_records)
    for r in detail_records:
        disp_val = str(r["display_value"])
        st = r["status"]
        if st in ["green", "amber", "red"]:
            pill_text = f"{STATUS_ICONS.get(st, '')} {disp_val}"
        elif st == "na":
            pill_text = "N/A"
        elif st == "awaiting":
            pill_text = AWAITING_LABEL
        else:
            pill_text = disp_val
            st = "plain"

        if has_category:
            rows.append([r.get("category") or "", r["label"], pill_text])
        else:
            rows.append([r["label"], pill_text])
        tones.append(st)
        tooltips.append({"Result": {"value": f"Survey value: {r.get('raw_value', 'N/A')}", "type": "text"}})

    cols = ["Category", label_column, "Result"] if has_category else [label_column, "Result"]
    return _data_table(cols, rows, tone_column="Result", tones=tones, tooltips=tooltips)


def _people_tab(facility_codes: list[str], wf_inds: list[dict] | None, df: pd.DataFrame) -> html.Div:
    real, tones, tooltips = _real_indicator_rows(wf_inds or [], df, None, None, None, facility_codes)
    real_card = _card([
        _section_title("Workforce Competency (tracked)"),
        _data_table(["Indicator", "Assessed, n", "Result"], real, tone_column="Result", tones=tones, tooltips=tooltips) if real else html.Div(
            "No workforce competency indicators configured for this report.", style={"fontSize": "12px", "color": MUTED}),
    ])
    staffing_card = _card([
        _section_title("Facility Staffing Cadres"),
        html.Div("Staffing cadre indicators are not currently reported across CeMoC and BEmONC facilities.", style={"fontSize": "12px", "color": MUTED, "padding": "4px 0"}),
    ])
    return html.Div([real_card, staffing_card])


def _products_tab(facility_codes: list[str], supply_inds: list[dict] | None, df: pd.DataFrame) -> html.Div:
    real, tones, tooltips = _real_indicator_rows(supply_inds or [], df, None, None, None, facility_codes)
    real_card = _card([
        _section_title("Commodity Availability (live tracked)"),
        _data_table(["Indicator", "Assessed, n", "Result"], real, tone_column="Result", tones=tones, tooltips=tooltips) if real else html.Div(
            "No commodity indicators configured for this live report.", style={"fontSize": "12px", "color": MUTED}),
    ])
    if is_readiness_data_available():
        body = _scope_view(
            facility_codes,
            detail_fn=lambda code: html.Div([
                _card([_section_title(f"Maternity Equipment Availability · {_facility_label(code)}"), _hfa_detail_table("Eqt - Maternity (AVL)", code, label_column="Equipment")]),
                _card([_section_title(f"Maternity Equipment Functionality · {_facility_label(code)}"), _hfa_detail_table("Eqt - Maternity (FC)", code, label_column="Equipment")]),
                _card([_section_title(f"Maternity Essential Medicines Availability · {_facility_label(code)}"), _hfa_detail_table("Meds - Maternity (AVL)", code, label_column="Medicine")]),
                _card([_section_title(f"Maternity Medicines Stockouts (last 30 days) · {_facility_label(code)}"), _hfa_detail_table("Meds - Maternity (SO)", code, label_column="Medicine")]),
                _card([_section_title(f"Newborn Equipment Availability · {_facility_label(code)}"), _hfa_detail_table("Eqt - Newborn (AVL)", code, label_column="Equipment")]),
                _card([_section_title(f"Newborn Equipment Functionality · {_facility_label(code)}"), _hfa_detail_table("Eqt - Newborn (FC)", code, label_column="Equipment")]),
                _card([_section_title(f"Newborn Tracer Medicines Availability · {_facility_label(code)}"), _hfa_detail_table("Meds - Newborn (AVL)", code, label_column="Medicine")]),
                _card([_section_title(f"Newborn Medicines Stockouts (last 30 days) · {_facility_label(code)}"), _hfa_detail_table("Meds - Newborn (SO)", code, label_column="Medicine")]),
            ]),
            comparison_fn=lambda codes: html.Div([
                _card([_section_title("Maternity Equipment Availability"), _matrix_table(compute_readiness_matrix("Eqt - Maternity (AVL)", codes), label_column="Equipment")]),
                _card([_section_title("Maternity Equipment Functionality (in facilities with equipment available)"), _matrix_table(compute_readiness_matrix("Eqt - Maternity (FC)", codes), label_column="Equipment")]),
                _card([_section_title("Maternity Essential Medicines Availability"), _matrix_table(compute_readiness_matrix("Meds - Maternity (AVL)", codes), label_column="Medicine")]),
                _card([_section_title("Maternity Medicines Stockouts (last 30 days)"), _matrix_table(compute_readiness_matrix("Meds - Maternity (SO)", codes), label_column="Medicine")]),
                _card([_section_title("Newborn Equipment Availability"), _matrix_table(compute_readiness_matrix("Eqt - Newborn (AVL)", codes), label_column="Equipment")]),
                _card([_section_title("Newborn Equipment Functionality (in facilities with equipment available)"), _matrix_table(compute_readiness_matrix("Eqt - Newborn (FC)", codes), label_column="Equipment")]),
                _card([_section_title("Newborn Tracer Medicines Availability"), _matrix_table(compute_readiness_matrix("Meds - Newborn (AVL)", codes), label_column="Medicine")]),
                _card([_section_title("Newborn Medicines Stockouts (last 30 days)"), _matrix_table(compute_readiness_matrix("Meds - Newborn (SO)", codes), label_column="Medicine")]),
            ]),
        )
    else:
        body = _card([
            _section_title("Operational Readiness Survey Data"),
            html.Div("Operational readiness survey data is not currently available for the selected facilities.", style={"fontSize": "12px", "color": MUTED, "padding": "4px 0"}),
        ])
    return html.Div([real_card, body])


def _systems_tab(facility_codes: list[str], dq_inds: list[dict] | None, df: pd.DataFrame) -> html.Div:
    real, tones, tooltips = _real_indicator_rows(dq_inds or [], df, None, None, None, facility_codes)
    real_card = _card([
        _section_title("Data Quality (live tracked)"),
        _data_table(["Indicator", "Assessed, n", "Result"], real, tone_column="Result", tones=tones, tooltips=tooltips) if real else html.Div(
            "No data-quality indicators configured for this live report.", style={"fontSize": "12px", "color": MUTED}),
    ])
    if is_readiness_data_available():
        body = _scope_view(
            facility_codes,
            detail_fn=lambda code: html.Div([
                _card([_section_title(f"Maternity Unit Infrastructure · {_facility_label(code)}"), _hfa_detail_table("INF - Maternity", code, label_column="Infrastructure Indicator")]),
                _card([_section_title(f"Neonatal Care Unit Infrastructure · {_facility_label(code)}"), _hfa_detail_table("INF - Newborn", code, label_column="Infrastructure Indicator")]),
                _card([_section_title(f"Facility Systems, Referral & Governance · {_facility_label(code)}"), _hfa_detail_table("Facility systems", code, label_column="System Indicator")]),
            ]),
            comparison_fn=lambda codes: html.Div([
                _card([_section_title("Maternity Unit Infrastructure"), _matrix_table(compute_readiness_matrix("INF - Maternity", codes), label_column="Infrastructure Indicator")]),
                _card([_section_title("Neonatal Care Unit Infrastructure"), _matrix_table(compute_readiness_matrix("INF - Newborn", codes), label_column="Infrastructure Indicator")]),
                _card([_section_title("Facility Systems, Referral & Governance"), _matrix_table(compute_readiness_matrix("Facility systems", codes), label_column="System Indicator")]),
            ]),
        )
    else:
        body = _card([
            _section_title("Operational Readiness Infrastructure & Systems"),
            html.Div("Operational readiness survey data is not currently available for the selected facilities.", style={"fontSize": "12px", "color": MUTED, "padding": "4px 0"}),
        ])
    return html.Div([real_card, body])


# ---------------------------------------------------------------------------
# Lazy sub-tab shell (same pattern as mnid/dashboards/MNH-Nest360/layout.py)
# ---------------------------------------------------------------------------

_TABS = [
    ("overview", "Overview"),
    ("signal-functions", "Signal Functions"),
    ("people", "People"),
    ("products", "Products & Commodities"),
    ("systems", "Systems & Infrastructure"),
]


def render_operational_readiness(
    df: pd.DataFrame,
    indicators: list[dict] | None = None,
    selected_indicators: list[str] | None = None,
    scope_meta: dict | None = None,
    start_date=None,
    end_date=None,
    agg_df: pd.DataFrame | None = None,
    supply_inds: list[dict] | None = None,
    wf_inds: list[dict] | None = None,
    dq_inds: list[dict] | None = None,
    **kwargs,
) -> html.Div:
    """The root Operational Readiness view. Lazily mounts 5 sub-tabs so we
    don't compute all 5 tabs on every load."""
    facility_codes = _source_facility_universe(df, scope_meta)
    scope_info = _profile_scope_name(scope_meta)
    scope_name = scope_info.get("eyebrow") if isinstance(scope_info, dict) else str(scope_info or "National")
    level_label = str((scope_meta or {}).get("level") or "National").capitalize()

    header = html.Div([
        html.Div([
            html.Span("OPERATIONAL READINESS", style={
                "fontSize": "11px", "fontWeight": "800", "letterSpacing": ".08em",
                "color": GREEN, "textTransform": "uppercase",
            }),
            html.H2("EmONC Facility Readiness & Capacity", style={
                "margin": "2px 0 0 0", "fontSize": "20px", "fontWeight": "800", "color": TEXT,
            }),
            html.Div(f"Assessing readiness across {len(facility_codes)} facilities in scope ({scope_name})", style={
                "fontSize": "12px", "color": MUTED, "marginTop": "2px",
            }),
        ]),
        html.Div(html.Span(level_label, style={
            "background": "#f0fdf4", "border": f"1px solid {GREEN}",
            "color": GREEN, "fontSize": "11px", "fontWeight": "700",
            "padding": "4px 10px", "borderRadius": "6px",
        }), style={"alignSelf": "flex-start"}),
    ], style={
        "display": "flex", "justifyContent": "space-between", "alignItems": "flex-start",
        "marginBottom": "16px", "paddingBottom": "12px", "borderBottom": f"1px solid {BORDER}",
    })

    tabs = dcc.Tabs(
        id="operational-readiness-subtabs",
        value="overview",
        children=[
            dcc.Tab(
                label=label,
                value=val,
                style={
                    "padding": "10px 18px",
                    "fontSize": "13px",
                    "fontWeight": "600",
                    "color": MUTED,
                    "backgroundColor": "#FFFFFF",
                    "border": f"1px solid {BORDER}",
                    "borderRadius": "8px 8px 0 0",
                    "marginRight": "4px",
                },
                selected_style={
                    "padding": "10px 18px",
                    "fontSize": "13px",
                    "fontWeight": "700",
                    "color": GREEN,
                    "backgroundColor": "#F0FDF4",
                    "border": f"1px solid {BORDER}",
                    "borderBottom": "2px solid transparent",
                    "borderTop": f"3px solid {GREEN}",
                    "borderRadius": "8px 8px 0 0",
                    "marginRight": "4px",
                },
                children=dcc.Loading(
                    html.Div(id=f"operational-readiness-tab-{val}-content", style={"paddingTop": "16px"}),
                    type="dot",
                    color=GREEN,
                ),
            )
            for val, label in _TABS
        ],
        style={"marginBottom": "16px"},
    )

    payload_id = _remember_ui_payload("op_readiness", {
        "df": df,
        "agg_df": agg_df,
        "scope_meta": scope_meta,
        "start_date": start_date,
        "end_date": end_date,
        "supply_inds": supply_inds or indicators or [],
        "wf_inds": wf_inds or [],
        "dq_inds": dq_inds or [],
    })
    store = dcc.Store(id="operational-readiness-tab-data-store", data={"payload_id": payload_id})
    return html.Div([header, tabs, store])


def _restore_readiness_payload(payload_id: str | None) -> dict:
    if not payload_id:
        return {}
    data = _MNID_DATA_DISK_CACHE.get(payload_id)
    if isinstance(data, dict):
        raw_df = data.get("df")
        df = raw_df if isinstance(raw_df, pd.DataFrame) else (deserialize_store_df(raw_df) if raw_df is not None else pd.DataFrame())
        raw_agg = data.get("agg_df")
        agg_df = raw_agg if isinstance(raw_agg, pd.DataFrame) else (deserialize_store_df(raw_agg) if raw_agg is not None else None)
        return {
            "df": df,
            "agg_df": agg_df,
            "scope_meta": data.get("scope_meta"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "supply_inds": data.get("supply_inds") or [],
            "wf_inds": data.get("wf_inds") or [],
            "dq_inds": data.get("dq_inds") or [],
        }
    if isinstance(data, pd.DataFrame):
        return {
            "df": data,
            "agg_df": None,
            "scope_meta": None,
            "start_date": None,
            "end_date": None,
            "supply_inds": [],
            "wf_inds": [],
            "dq_inds": [],
        }
    return {}


# ---------------------------------------------------------------------------
# Lazy sub-tab callback
# ---------------------------------------------------------------------------

@callback(
    Output("operational-readiness-tab-overview-content", "children"),
    Output("operational-readiness-tab-signal-functions-content", "children"),
    Output("operational-readiness-tab-people-content", "children"),
    Output("operational-readiness-tab-products-content", "children"),
    Output("operational-readiness-tab-systems-content", "children"),
    Input("operational-readiness-subtabs", "value"),
    State("operational-readiness-tab-data-store", "data"),
    prevent_initial_call=False,
)
def _render_operational_readiness_tab(active_tab: str | None, store_data: dict | None):
    if not active_tab or not store_data:
        raise PreventUpdate
    payload = _restore_readiness_payload(store_data.get("payload_id"))
    df = payload.get("df") if payload.get("df") is not None else pd.DataFrame()
    agg_df = payload.get("agg_df")
    scope_meta = payload.get("scope_meta")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    facility_codes = _source_facility_universe(df, scope_meta)

    # Empty responses for inactive tabs so we don't re-render them
    outputs = [no_update] * len(_TABS)
    tab_indices = {val: i for i, (val, _) in enumerate(_TABS)}
    target_idx = tab_indices.get(active_tab)
    if target_idx is None:
        raise PreventUpdate

    if active_tab == "overview":
        content = _build_overview_tab(facility_codes, df, agg_df, start_date, end_date)
    elif active_tab == "signal-functions":
        content = _build_signal_functions_tab(facility_codes, df, agg_df, start_date, end_date)
    elif active_tab == "people":
        content = _people_tab(facility_codes, payload.get("wf_inds") or [], df)
    elif active_tab == "products":
        content = _products_tab(facility_codes, payload.get("supply_inds") or [], df)
    elif active_tab == "systems":
        content = _systems_tab(facility_codes, payload.get("dq_inds") or [], df)
    else:
        content = html.Div("Tab content not found")

    outputs[target_idx] = content
    return tuple(outputs)


@callback(
    Output("operational-readiness-comparison-table-container", "children"),
    Input("operational-readiness-classification-filter", "value"),
    State("operational-readiness-comparison-records", "data"),
    prevent_initial_call=True,
)
def _filter_facility_comparison_table(selected_classification: str | None, records: list[dict] | None):
    if not records:
        raise PreventUpdate
    if not selected_classification or selected_classification == "All":
        filtered = records
    else:
        filtered = [r for r in records if r.get("classification") == selected_classification]
    return _facility_comparison_table(filtered)
