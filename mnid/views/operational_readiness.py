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
from mnid.core.constants import FACILITY_NAMES, FACILITY_DISTRICT
from mnid.core.data_utils import resolve_facility_level, _remember_ui_payload, _restore_ui_dataframe
from mnid.core.data_source import get_mnid_data_source
from mnid.views.executive_views import _hierarchy_scope, _profile_scope_name, _summary_card

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
    return [
        {"if": {"row_index": i, "column_id": column_id}, "color": STATUS_COLORS[tone][0], "fontWeight": "700"}
        for i, tone in enumerate(tones)
    ]


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
        facilities = [code for code in facilities if code in selected or _facility_label(code) in selected]
    return facilities


def _facility_label(code: str) -> str:
    return FACILITY_NAMES.get(code, code)


def _facility_district(code: str) -> str:
    return FACILITY_DISTRICT.get(code, "")


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
    is distinct from "resolved fine but the numerator happens to be zero".
    """
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
    throughout mnid/views/trends.py.
    """
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


def _matrix_tone(pct: float | None) -> str:
    if pct is None:
        return "awaiting"
    return "green" if pct >= 80 else "amber" if pct >= 50 else "red"


def _matrix_cell(pct: float | None, detail: str | None = None) -> html.Td:
    """One traffic-light cell: a solid tone fill with white text, matching
    the Facility Performance matrix on the Maternal dashboard
    (mnid/charts/heatmap.py::_build_facility_performance_heatmap_fig) - a
    solid, saturated fill scans faster across many rows than the pale-wash
    pills used elsewhere in this file for single-value status text.
    `detail` (e.g. "12 of 16 facilities performing") becomes the native
    hover tooltip via the HTML title attribute - no extra JS/callback
    needed for a hover to show the calculation behind the percentage."""
    tone = _matrix_tone(pct)
    common = {"textAlign": "center", "padding": "9px 10px", "fontSize": "12px"}
    if pct is None:
        return html.Td(STATUS_ICONS["awaiting"], title=detail, style={
            **common, "color": MUTED, "background": BACKGROUND, "borderBottom": f"1px solid {BORDER}",
        })
    color, _ = STATUS_COLORS[tone]
    return html.Td(f"{pct:.0f}%", title=detail, style={
        **common, "fontWeight": "700",
        "color": "#FFFFFF", "background": color, "borderBottom": f"1px solid {SURFACE}",
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


def _signal_functions_detail(code: str, numerators_by_sig: dict, df: pd.DataFrame,
                              unavailable_ids: frozenset[str] = frozenset()) -> html.Div:
    level = resolve_facility_level(code, _facility_label(code))
    rows = []
    for sf in SIGNAL_FUNCTIONS:
        status = _facility_status(sf, numerators_by_sig[sf["id"]], code, level, unavailable_ids)
        rows.append(html.Div([
            html.Span(sf["label"], style={"fontSize": "12px", "color": TEXT, "flex": "1"}),
            _tone_pill(status, SIGNAL_DETAIL_LABELS[status]),
        ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "padding": "8px 0", "borderBottom": f"1px solid {BORDER}"}))
    classification, missing, note = _classify_emonc(numerators_by_sig, code, level, unavailable_ids)
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
            html.Div([
                html.Div([
                    html.Span(label, style={"fontSize": "12px", "color": TEXT, "flex": "1"}),
                    _tone_pill("awaiting", AWAITING_LABEL),
                ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "padding": "8px 0", "borderBottom": f"1px solid {BORDER}"})
                for label in NEWBORN_SIGNAL_FUNCTIONS
            ]),
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
        code: _classify_emonc(numerators_by_sig, code, resolve_facility_level(code, _facility_label(code)), unavailable_ids)[0]
        for code in facility_codes
    }
    cemonc_group = [c for c in facility_codes if classifications[c] == "CEmONC"]
    bemonc_group = [c for c in facility_codes if classifications[c] == "BEmONC"]

    def _group_pct(sf: dict, group: list[str], group_label: str) -> tuple[float | None, str | None]:
        if sf["id"] in unavailable_ids or not group:
            return None, None
        performing = sum(1 for code in group if numerators_by_sig[sf["id"]].get(code, 0) > 0)
        pct = round(performing / len(group) * 100, 1)
        return pct, f"{performing} of {len(group)} {group_label}-classified facilities performing"

    maternal_rows = []
    for sf in SIGNAL_FUNCTIONS:
        cemonc_pct, cemonc_detail = _group_pct(sf, cemonc_group, "CEmONC")
        bemonc_pct, bemonc_detail = _group_pct(sf, bemonc_group, "BEmONC")
        maternal_rows.append({
            "label": sf["label"],
            "cemonc": cemonc_pct, "cemonc_detail": cemonc_detail,
            "bemonc": bemonc_pct, "bemonc_detail": bemonc_detail,
        })
    newborn_rows = [{"label": label, "cemonc": None, "bemonc": None} for label in NEWBORN_SIGNAL_FUNCTIONS]

    note_children = [
        f"Share of {len(cemonc_group)} CEmONC- and {len(bemonc_group)} BEmONC-classified facilities in scope "
        "performing each function in the reporting period.",
    ]
    if unavailable_ids:
        excluded_labels = [sf["label"] for sf in SIGNAL_FUNCTIONS if sf["id"] in unavailable_ids]
        note_children.append(html.Br())
        note_children.append(f"{', '.join(excluded_labels)}: not reported via this data source.")

    return html.Div([
        _card([
            _section_title("Maternal Signal Functions"),
            _matrix_table(maternal_rows, label_column="Signal Function"),
            html.Div(note_children, style={"fontSize": "10px", "color": MUTED, "marginTop": "8px"}),
        ]),
        _card([
            _section_title("Newborn Signal Functions"),
            _matrix_table(newborn_rows, label_column="Signal Function"),
        ]),
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
        code: _classify_emonc(numerators_by_sig, code, resolve_facility_level(code, _facility_label(code)), unavailable_ids)
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

    records = _facility_comparison_records(
        facility_codes, classifications, births_by_facility,
        caesareans_by_facility, admissions_by_facility, admissions_available,
    )
    table_children = [
        html.Div(
            dcc.Dropdown(
                id="oprd-classification-filter",
                options=_CLASSIFICATION_FILTER_OPTIONS, value="All", clearable=False,
                style={"width": "220px", "fontSize": "12px"},
            ),
            style={"display": "flex", "justifyContent": "flex-end", "marginBottom": "12px"},
        ),
        dcc.Store(id="oprd-facility-rows-store", data=records),
        html.Div(id="oprd-facility-table-container", children=_facility_comparison_table(records)),
    ]
    if classification_note:
        table_children.append(html.Div(classification_note, style={"fontSize": "10px", "color": MUTED, "marginTop": "8px"}))
    table = html.Div([
        _section_title("Facility Readiness Comparison"),
        _card(table_children),
    ])
    return html.Div([summary, profile, stats, table])


# ---------------------------------------------------------------------------
# People / Products & Commodities / Systems & Infrastructure -- awaiting data
# ---------------------------------------------------------------------------

def _awaiting_detail_table(items: list[str], label_column: str = "Indicator") -> dash_table.DataTable:
    rows = [[label, AWAITING_LABEL] for label in items]
    tones = ["awaiting"] * len(items)
    return _data_table([label_column, "Result"], rows, tone_column="Result", tones=tones)


def _awaiting_domain_detail_table(domain_items: list[tuple[str, str]], label_column: str = "Indicator") -> dash_table.DataTable:
    rows = [[d, i, AWAITING_LABEL] for d, i in domain_items]
    tones = ["awaiting"] * len(domain_items)
    return _data_table(["Domain", label_column, "Result"], rows, tone_column="Result", tones=tones)


def _awaiting_matrix_rows(items: list) -> list[dict]:
    """Convert a flat item list or a list of (category, item) tuples into
    CEmONC/BEmONC matrix rows with no real per-item data yet - every cell
    renders as "awaiting" until People/Products/Systems get a real data
    source, at which point only this function's output changes, not the
    table it feeds."""
    if items and isinstance(items[0], tuple):
        return [{"label": label, "category": category, "cemonc": None, "bemonc": None} for category, label in items]
    return [{"label": label, "cemonc": None, "bemonc": None} for label in items]


def _real_indicator_rows(indicators: list[dict], df: pd.DataFrame,
                          agg_df: pd.DataFrame | None, start_date, end_date,
                          facility_codes: list[str]) -> tuple[list[list], list[str], list[dict]]:
    """Render already-real indicators (supply/workforce/data-quality) the same
    row shape as the awaiting-data tables, so real and placeholder rows sit
    together in one table without the UI needing to know which is which.
    Returns rows, tones and hover details for the percentage result."""
    rows, tones, tooltips = [], [], []
    for ind in indicators:
        num, den, pct = _cov(df, ind.get("numerator_filters", {}), ind.get("denominator_filters", {}))
        label = ind.get("label", "Indicator")
        if den:
            tone = "green" if pct >= 80 else "amber" if pct >= 50 else "red"
            rows.append([label, f"{den:,}", f"{RATE_LABELS[tone]} · {pct:.0f}%"])
            tones.append(tone)
            tooltips.append({"Result": {
                "value": f"{num:,} out of {den:,} assessed records", "type": "text",
            }})
        else:
            rows.append([label, f"{den:,}", AWAITING_LABEL])
            tones.append("awaiting")
            tooltips.append({"Result": {"value": "No assessed records", "type": "text"}})
    return rows, tones, tooltips


def _people_tab(facility_codes: list[str], wf_inds: list[dict] | None, df: pd.DataFrame) -> html.Div:
    real, tones, tooltips = _real_indicator_rows(wf_inds or [], df, None, None, None, facility_codes)
    real_card = _card([
        _section_title("Workforce Competency (tracked)"),
        _data_table(["Indicator", "Assessed, n", "Result"], real, tone_column="Result", tones=tones, tooltips=tooltips) if real else html.Div(
            "No workforce competency indicators configured for this report.", style={"fontSize": "12px", "color": MUTED}),
    ])
    body = _scope_view(
        facility_codes,
        detail_fn=lambda code: html.Div([
            _card([_section_title(f"Neonatal Care Unit Staffing · {_facility_label(code)}"), _awaiting_detail_table(CADRES_NEONATAL, label_column="Cadre")]),
            _card([_section_title(f"Maternity Staffing · {_facility_label(code)}"), _awaiting_detail_table(CADRES_MATERNITY, label_column="Cadre")]),
        ]),
        comparison_fn=lambda codes: html.Div([
            _card([_section_title("Neonatal Care Unit Staffing"), _matrix_table(_awaiting_matrix_rows(CADRES_NEONATAL), label_column="Cadre")]),
            _card([_section_title("Maternity Staffing"), _matrix_table(_awaiting_matrix_rows(CADRES_MATERNITY), label_column="Cadre")]),
        ]),
    )
    return html.Div([real_card, body])


def _products_tab(facility_codes: list[str], supply_inds: list[dict] | None, df: pd.DataFrame) -> html.Div:
    real, tones, tooltips = _real_indicator_rows(supply_inds or [], df, None, None, None, facility_codes)
    real_card = _card([
        _section_title("Commodity Availability (tracked)"),
        _data_table(["Indicator", "Assessed, n", "Result"], real, tone_column="Result", tones=tones, tooltips=tooltips) if real else html.Div(
            "No commodity indicators configured for this report.", style={"fontSize": "12px", "color": MUTED}),
    ])
    body = _scope_view(
        facility_codes,
        detail_fn=lambda code: html.Div([
            _card([_section_title(f"Maternity Equipment · {_facility_label(code)}"), _awaiting_domain_detail_table(EQUIPMENT_MATERNITY, label_column="Commodity")]),
            _card([_section_title(f"Maternity Essential Medicines · {_facility_label(code)}"), _awaiting_domain_detail_table(TRACER_MEDICINES_MATERNITY, label_column="Commodity")]),
            _card([_section_title(f"Newborn Equipment · {_facility_label(code)}"), _awaiting_domain_detail_table(EQUIPMENT_NEWBORN, label_column="Commodity")]),
            _card([_section_title(f"Newborn Tracer Medicines · {_facility_label(code)}"), _awaiting_domain_detail_table(TRACER_MEDICINES_NEWBORN, label_column="Commodity")]),
        ]),
        comparison_fn=lambda codes: html.Div([
            _card([_section_title("Maternity Equipment & Medicines"), _matrix_table(_awaiting_matrix_rows(EQUIPMENT_MATERNITY + TRACER_MEDICINES_MATERNITY), label_column="Commodity")]),
            _card([_section_title("Newborn Equipment & Medicines"), _matrix_table(_awaiting_matrix_rows(EQUIPMENT_NEWBORN + TRACER_MEDICINES_NEWBORN), label_column="Commodity")]),
        ]),
    )
    return html.Div([real_card, body])


def _systems_tab(facility_codes: list[str], dq_inds: list[dict] | None, df: pd.DataFrame) -> html.Div:
    real, tones, tooltips = _real_indicator_rows(dq_inds or [], df, None, None, None, facility_codes)
    real_card = _card([
        _section_title("Data Quality (tracked)"),
        _data_table(["Indicator", "Assessed, n", "Result"], real, tone_column="Result", tones=tones, tooltips=tooltips) if real else html.Div(
            "No data-quality indicators configured for this report.", style={"fontSize": "12px", "color": MUTED}),
    ])
    body = _scope_view(
        facility_codes,
        detail_fn=lambda code: html.Div([
            _card([_section_title(f"Maternity Unit Infrastructure · {_facility_label(code)}"), _awaiting_domain_detail_table(INFRASTRUCTURE_MATERNITY, label_column="Infrastructure Indicator")]),
            _card([_section_title(f"Neonatal Care Unit Infrastructure · {_facility_label(code)}"), _awaiting_domain_detail_table(INFRASTRUCTURE_NEONATAL, label_column="Infrastructure Indicator")]),
            _card([_section_title(f"Referral and Transport · {_facility_label(code)}"), _awaiting_detail_table(REFERRAL_TRANSPORT)]),
            _card([_section_title(f"Data and Quality-Improvement Systems · {_facility_label(code)}"), _awaiting_detail_table(DATA_QI_SYSTEMS)]),
        ]),
        comparison_fn=lambda codes: html.Div([
            _card([_section_title("Maternity Infrastructure"), _matrix_table(_awaiting_matrix_rows(INFRASTRUCTURE_MATERNITY), label_column="Infrastructure Indicator")]),
            _card([_section_title("Neonatal Care Unit Infrastructure"), _matrix_table(_awaiting_matrix_rows(INFRASTRUCTURE_NEONATAL), label_column="Infrastructure Indicator")]),
            _card([_section_title("Referral, Transport & QI Systems"), _matrix_table(_awaiting_matrix_rows(REFERRAL_TRANSPORT + DATA_QI_SYSTEMS), label_column="Indicator")]),
        ]),
    )
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

_TAB_STYLE = {
    "padding": "16px 18px", "fontSize": "14px", "fontWeight": "700",
    "color": MUTED, "background": "transparent", "border": "none",
    "borderBottom": "2px solid transparent", "minWidth": "132px", "flexShrink": "0",
}
_TAB_SELECTED_STYLE = {
    **_TAB_STYLE, "color": GREEN, "background": "#F0FDF4",
    "borderBottom": f"2px solid {GREEN}",
}


def _period_label(start_date, end_date) -> str:
    def _format(value):
        if value is None:
            return "N/A"
        try:
            return pd.to_datetime(value).strftime("%d %b %Y")
        except (TypeError, ValueError):
            return str(value)
    return f"{_format(start_date)} - {_format(end_date)}"


def _readiness_header(df: pd.DataFrame, scope_meta: dict | None,
                      facility_codes: list[str], start_date, end_date) -> list:
    profile = _profile_scope_name(scope_meta)
    period = _period_label(start_date, end_date)
    district_count = len({_facility_district(code) for code in facility_codes if _facility_district(code)})
    route = (scope_meta or {}).get("route", "default")
    source = "MAHIS dataset" if _resolve_data_source(route).requires_raw_dataset else "DHIS2 aggregate"
    scope_items = _hierarchy_scope(df if df is not None else pd.DataFrame(), scope_meta, period)

    badge_style = {
        "background": "#F8FAFC", "border": f"1px solid {BORDER}", "color": "#475569",
        "fontSize": "11px", "fontWeight": "700", "padding": "5px 11px", "borderRadius": "99px",
    }
    hero = dmc.Paper(
        withBorder=True, radius="lg", shadow="xs", p="xl",
        style={"marginBottom": "20px", "borderColor": BORDER},
        children=[
            # Keep this eyebrow independent of the Country Profile scope label.
            html.Div("Operational Readiness", style={
                "fontSize": "11px", "fontWeight": "700", "color": "#0F766E",
                "letterSpacing": ".12em", "textTransform": "uppercase", "marginBottom": "10px",
            }),
            html.H1("Maternal and Newborn Service Readiness", style={
                "fontSize": "26px", "fontWeight": "800", "color": TEXT,
                "letterSpacing": "-.04em", "lineHeight": "1.15", "marginBottom": "6px",
            }),
            html.P(
                f"{profile['overview']} · EmONC signal functions · Workforce · Commodities · Systems",
                style={"fontSize": "13px", "color": MUTED, "marginBottom": "16px"},
            ),
            html.Div([
                html.Span("Live assessment", style={**badge_style, "background": "#ECFDF5", "borderColor": "#BBF7D0", "color": GREEN}),
                html.Span(period, style=badge_style),
                html.Span(f"{district_count} Districts · {len(facility_codes)} Facilities", style=badge_style),
                html.Span(source, style=badge_style),
            ], style={"display": "flex", "gap": "8px", "flexWrap": "wrap"}),
        ],
    )
    scope_band = html.Div([
        html.Div([
            html.Span(item["label"], style={
                "fontSize": "10px", "fontWeight": "700", "color": "#94A3B8",
                "textTransform": "uppercase", "letterSpacing": ".07em", "display": "block", "marginBottom": "2px",
            }),
            html.Span(item["value"], style={"fontSize": "12px", "fontWeight": "600", "color": TEXT}),
        ], style={"padding": "8px 14px", "borderRight": f"1px solid {BORDER}"})
        for item in scope_items
    ], style={
        "display": "flex", "flexWrap": "wrap", "background": BACKGROUND,
        "border": f"1px solid {BORDER}", "borderRadius": "10px",
        "overflow": "hidden", "marginBottom": "20px",
    })
    return [hero, scope_band]


def _render_tab_content(tab_value: str, stored: dict) -> html.Div:
    df = _restore_ui_dataframe(stored.get("data_key"))
    facility_codes = stored.get("facility_codes") or _facility_universe(df)
    start_date = stored.get("start_date")
    end_date = stored.get("end_date")
    agg_df = _resolve_data_source(stored.get("route", "default")).aggregate()

    if not facility_codes:
        return dmc.Paper(
            withBorder=True, radius="md", p="xl",
            style={"borderColor": BORDER, "textAlign": "center"},
            children=[
                html.Div("No facilities available in the selected scope", style={
                    "fontSize": "14px", "fontWeight": "700", "color": TEXT, "marginBottom": "5px",
                }),
                html.Div(
                    "Adjust the district or facility filters, or confirm that the configured data source contains facility-level records.",
                    style={"fontSize": "12px", "color": MUTED},
                ),
            ],
        )

    if tab_value == "overview":
        return _build_overview_tab(facility_codes, df, agg_df, start_date, end_date)
    if tab_value == "signal-functions":
        return _build_signal_functions_tab(facility_codes, df, agg_df, start_date, end_date)
    if tab_value == "people":
        return _people_tab(facility_codes, stored.get("wf_inds"), df)
    if tab_value == "products":
        return _products_tab(facility_codes, stored.get("supply_inds"), df)
    if tab_value == "systems":
        return _systems_tab(facility_codes, stored.get("dq_inds"), df)
    return html.Div()


@callback(
    Output("oprd-tab-container", "children"),
    Input("oprd-subtabs", "value"),
    State("oprd-store", "data"),
)
def _oprd_sync_tab(tab_value, stored):
    if not tab_value or not stored:
        raise PreventUpdate
    return _render_tab_content(tab_value, stored)


@callback(
    Output("oprd-subtabs", "value"),
    Output("oprd-active-subtab-store", "data"),
    Input("oprd-subtabs", "value"),
    Input("oprd-active-subtab-store", "data"),
)
def _oprd_sync_subtab(tab_value: str | None, stored_tab: str | None):
    """Two-way sync between the visible tab and its session-store mirror,
    written as ONE callback with both properties as both Input and Output -
    Dash's documented "circular callback" pattern (two separate callbacks
    each outputting the other's Input raises "Dependency Cycle Found" at
    startup, which is exactly what tripped here originally).

    `ctx.triggered_id` tells the two directions apart:
    - user actually changed the tab -> persist that value to the store,
      leave the tab's own value alone (no_update breaks the loop: the store
      write below won't re-enter the "restore" branch because tab_value
      already matches by the time it re-fires).
    - page/component load (triggered_id is None) or the store itself
      changed -> restore the stored tab if it's valid and differs from the
      hardcoded default_tab this component mounted with.
    """
    valid_tabs = {value for value, _ in _TABS}
    if ctx.triggered_id == "oprd-subtabs":
        if not tab_value:
            raise PreventUpdate
        return no_update, tab_value
    if stored_tab and stored_tab in valid_tabs and stored_tab != tab_value:
        return stored_tab, no_update
    raise PreventUpdate


@callback(
    Output("oprd-facility-table-container", "children"),
    Input("oprd-classification-filter", "value"),
    State("oprd-facility-rows-store", "data"),
    prevent_initial_call=True,
)
def _oprd_filter_facility_table(selected: str, records: list[dict] | None):
    """Re-slice the already-computed facility records client round-trip
    (dcc.Store) rather than recomputing the Overview tab - the same pattern
    mnid/views/callbacks.py::update_performance_heatmap uses for the Maternal
    dashboard's district/indicator filters."""
    if not records:
        raise PreventUpdate
    filtered = records if not selected or selected == "All" else [r for r in records if r["classification"] == selected]
    return _facility_comparison_table(filtered)


def render_operational_readiness(
    df: pd.DataFrame,
    supply_inds: list[dict] | None = None,
    wf_inds: list[dict] | None = None,
    dq_inds: list[dict] | None = None,
    scope_meta: dict | None = None,
    start_date=None,
    end_date=None,
) -> html.Div:
    scope_meta = scope_meta or {}
    facility_codes = _source_facility_universe(df, scope_meta)
    route = scope_meta.get("route", "default")
    store_data = {
        "data_key": _remember_ui_payload("oprd", df if df is not None else pd.DataFrame()),
        "facility_codes": facility_codes,
        "start_date": str(start_date) if start_date else None,
        "end_date": str(end_date) if end_date else None,
        "supply_inds": supply_inds or [],
        "wf_inds": wf_inds or [],
        "dq_inds": dq_inds or [],
        "route": route,
    }
    default_tab = _TABS[0][0]
    initial_content = _render_tab_content(default_tab, store_data)

    return html.Div(className="mnid-executive-page", children=[
        dcc.Store(id="oprd-store", data=store_data),
        # Session-scoped (survives a dashboard-container rebuild within the
        # same browser tab, same pattern as pages/home.py's
        # mnid-active-tab-store) - without this, the periodic
        # dashboard-interval-update-today tick indirectly forces
        # update_dashboard to rebuild dashboard-container's children (see
        # pages/home.py's own comments on that spurious refire), and since
        # this component always mounts fresh with value=default_tab, whatever
        # sub-tab (e.g. People) the user had open would silently reset to
        # Overview on every rebuild - this restores it instead.
        dcc.Store(id="oprd-active-subtab-store", storage_type="session"),
        *_readiness_header(df, scope_meta, facility_codes, start_date, end_date),
        html.Div([
            dcc.Tabs(
                id="oprd-subtabs", value=default_tab,
                children=[dcc.Tab(
                    label=label, value=value, style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED_STYLE,
                ) for value, label in _TABS],
                style={"borderBottom": "none", "minWidth": "720px"},
                parent_style={"overflowX": "auto", "overflowY": "hidden"},
            ),
        ], style={
            "background": SURFACE, "border": f"1px solid {BORDER}", "borderRadius": "10px",
            "overflow": "hidden", "marginBottom": "20px",
        }),
        dcc.Loading(
            html.Div(id="oprd-tab-container", children=initial_content),
            type="circle", color=GREEN,
        ),
    ])
