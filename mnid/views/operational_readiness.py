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
import plotly.graph_objects as go
from dash import html, dcc, callback, dash_table, Input, Output, State
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
SIGNAL_AGGREGATE_LABELS = {
    "green": f"{STATUS_ICONS['green']} On track (≥80% of facilities)",
    "amber": f"{STATUS_ICONS['amber']} Needs attention (50-79%)",
    "red": f"{STATUS_ICONS['red']} Critical (<50%)",
    "na": f"{STATUS_ICONS['na']} No eligible facilities",
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
    "BEmONC-1": f"{STATUS_ICONS['amber']} BEmONC-1",
    "Unclassified": f"{STATUS_ICONS['red']} Unclassified",
}
EMONC_TONES = {"CEmONC": "green", "BEmONC": "amber", "BEmONC-1": "amber", "Unclassified": "red"}

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


def _status_column_style(column_id: str, label_map: dict[str, str]) -> list[dict]:
    """Color a status-style column's text by tone, keyed on the exact display
    strings a given context uses (e.g. SIGNAL_AGGREGATE_LABELS) - the closest
    a DataTable cell can get to the rounded status pills used elsewhere."""
    return [
        {
            "if": {"filter_query": f'{{{column_id}}} = "{text}"', "column_id": column_id},
            "color": STATUS_COLORS[tone][0], "fontWeight": "700",
        }
        for tone, text in label_map.items()
    ]


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
    status_column: str | None = None, status_label_map: dict[str, str] | None = None,
    tone_column: str | None = None, tones: list[str] | None = None,
    classification_column: str | None = None,
    tooltips: list[dict] | None = None,
) -> dash_table.DataTable:
    conditional_style = [{"if": {"row_index": "odd"}, "backgroundColor": "#FAFCFE"}]
    if status_column and status_label_map:
        conditional_style += _status_column_style(status_column, status_label_map)
    if tone_column and tones:
        conditional_style += _tone_column_style(tone_column, tones)
    if classification_column:
        # Unlike other status columns, 4 EmONC tiers share only 3 tones
        # (BEmONC and BEmONC-1 are both "amber") - a plain {tone: text} map
        # can't express that, so build the filter rules directly per tier.
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
        filter_action="native",
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

    Gap analysis: a facility missing exactly one basic function is reported
    as "BEmONC-1" with the missing function named, rather than a flat
    "Unclassified" - the same convention EmONC assessment toolkits use to
    flag near-misses worth a training/equipment follow-up.

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
        return "BEmONC-1", missing_basic[0], note
    return "Unclassified", ", ".join(missing_basic), note


_TONE_HEX = {"green": GREEN, "amber": AMBER, "red": RED, "na": MUTED, "awaiting": MUTED, "unavailable": MUTED}


def _bare_chart_layout(fig: go.Figure, height: int) -> go.Figure:
    """Shared minimal chrome so charts read as part of the card, not a widget
    bolted on - no toolbar, no axis clutter, transparent so the card's own
    background shows through in both light and dark theme."""
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="inherit", color=TEXT, size=12),
        showlegend=False,
    )
    return fig


def _emonc_breakdown_chart(classifications: dict[str, str]) -> dcc.Graph:
    """Donut of CEmONC/BEmONC/BEmONC-1/Unclassified counts across the facilities
    in scope - a part-to-whole glance, not a data table: no label rides the
    slices (a number on every wedge is exactly the clutter that made the
    first version illegible), identity and counts live in the legend, and the
    exact count/percent surface on hover. The legend sits in its own row
    below the donut so short and long labels ("CEmONC" vs "Unclassified")
    wrap onto their own lines instead of colliding mid-word."""
    order = ["CEmONC", "BEmONC", "BEmONC-1", "Unclassified"]
    counts = {key: sum(1 for v in classifications.values() if v == key) for key in order}
    present = [key for key in order if counts[key] > 0]
    total = len(classifications)
    fig = go.Figure(data=[go.Pie(
        labels=[EMONC_LABELS[key].split(" ", 1)[1] for key in present],
        values=[counts[key] for key in present],
        hole=0.66, sort=False, direction="clockwise",
        marker=dict(colors=[_TONE_HEX[EMONC_TONES[key]] for key in present], line=dict(color=SURFACE, width=2)),
        textinfo="none",
        hovertemplate="%{label}: %{value} facilities (%{percent})<extra></extra>",
    )])
    fig.update_layout(annotations=[dict(
        text=f"<b>{total}</b><br><span style='font-size:10px'>facilit{'y' if total == 1 else 'ies'}</span>",
        x=0.5, y=0.5, showarrow=False, font=dict(size=18, color=TEXT),
    )])
    _bare_chart_layout(fig, height=190)
    fig.update_layout(
        showlegend=True,
        legend=dict(
            orientation="h", x=0.5, xanchor="center", y=-0.05, yanchor="top",
            font=dict(size=11), itemwidth=30,
            traceorder="normal", tracegroupgap=4,
        ),
        margin=dict(l=8, r=8, t=8, b=64),
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"height": "280px"})


def _signal_function_bar_chart(chart_rows: list[dict]) -> dcc.Graph:
    """Horizontal 100%-style bar of % eligible facilities performing each
    signal function, colored to the same green/amber/red thresholds as the
    table, with the 50%/80% cut lines drawn in so the bars are legible without
    cross-referencing the legend text."""
    labels = [r["label"] for r in chart_rows]
    pcts = [r["pct"] for r in chart_rows]
    colors = [_TONE_HEX[r["status"]] for r in chart_rows]
    text = [f"{r['pct']:.0f}%" if r["status"] not in ("na", "unavailable") else "N/A" for r in chart_rows]
    fig = go.Figure(data=[go.Bar(
        x=pcts, y=labels, orientation="h", marker=dict(color=colors),
        text=text, textposition="outside", cliponaxis=False,
    )])
    fig.add_vline(x=80, line=dict(dash="dot", color=GREEN, width=1), opacity=0.5)
    fig.add_vline(x=50, line=dict(dash="dot", color=AMBER, width=1), opacity=0.5)
    fig.update_xaxes(range=[0, 112], title=dict(text="% of eligible facilities performing", font=dict(size=10)),
                      tickfont=dict(size=10))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=11))
    _bare_chart_layout(fig, height=max(220, 34 * len(chart_rows)))
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


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
    rows = []
    tooltips = []
    chart_rows = []
    for sf in SIGNAL_FUNCTIONS:
        if sf["id"] in unavailable_ids:
            rows.append([sf["label"], "N/A", "N/A", "N/A", SIGNAL_AGGREGATE_LABELS["unavailable"]])
            tooltips.append({})
            chart_rows.append({"label": sf["label"], "pct": 0.0, "status": "unavailable"})
            continue
        eligible = [
            code for code in facility_codes
            if not (sf["comprehensive_only"] and resolve_facility_level(code, _facility_label(code)) == "Primary")
        ]
        performing = [code for code in eligible if numerators_by_sig[sf["id"]].get(code, 0) > 0]
        n_eligible = len(eligible)
        n_performing = len(performing)
        pct = round(n_performing / n_eligible * 100, 1) if n_eligible else 0.0
        status = "na" if n_eligible == 0 else ("green" if pct >= 80 else "amber" if pct >= 50 else "red")
        rows.append([
            sf["label"], n_eligible,
            f"{pct:.0f}%" if n_eligible else "N/A",
            f"{100 - pct:.0f}%" if n_eligible else "N/A",
            SIGNAL_AGGREGATE_LABELS[status],
        ])
        tooltips.append({
            "Performing (%)": {
                "value": f"{n_performing} out of {n_eligible} eligible facilities",
                "type": "text",
            },
            "Not performing (%)": {
                "value": f"{n_eligible - n_performing} out of {n_eligible} eligible facilities",
                "type": "text",
            },
        })
        chart_rows.append({"label": sf["label"], "pct": pct, "status": status})
    footer_children = [
        f"{SIGNAL_AGGREGATE_LABELS['green']} means at least 80% of eligible facilities perform the function; "
        f"{SIGNAL_AGGREGATE_LABELS['amber']} means 50-79%; {SIGNAL_AGGREGATE_LABELS['red']} means under 50%. "
        "Only facilities expected to perform the function (by facility level) count toward eligibility.",
    ]
    if unavailable_ids:
        excluded_labels = [sf["label"] for sf in SIGNAL_FUNCTIONS if sf["id"] in unavailable_ids]
        footer_children.append(html.Br())
        footer_children.append(f"{', '.join(excluded_labels)}: not reported via this data source - excluded from EmONC classification rather than counted as not performed.")
    return _card([
        _section_title(f"Signal-Function Performance · {len(facility_codes)} facilities in scope"),
        _signal_function_bar_chart(chart_rows),
        _data_table(
            ["Signal function", "Eligible facilities", "Performing (%)", "Not performing (%)", "Status"],
            rows, status_column="Status", status_label_map=SIGNAL_AGGREGATE_LABELS, tooltips=tooltips,
        ),
        html.Div(footer_children, style={"fontSize": "10px", "color": MUTED, "marginTop": "8px"}),
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
    districts = {_facility_district(c) for c in facility_codes if _facility_district(c)}
    bemonc = sum(1 for v in classifications.values() if v in ("BEmONC", "BEmONC-1"))
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
        _summary_card("Facilities selected", str(len(facility_codes)), "In current scope", GREEN),
        _summary_card("Districts represented", str(len(districts)), "Districts covered by selection", "#0284C7"),
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

    rows = [
        [
            _facility_district(code), _facility_label(code),
            resolve_facility_level(code, _facility_label(code)), EMONC_LABELS[classifications[code]],
            births_by_facility.get(code, 0),
            caesareans_by_facility.get(code, 0),
            admissions_by_facility.get(code, 0) if admissions_available else AWAITING_LABEL,
        ]
        for code in facility_codes
    ]
    breakdown_children = [_emonc_breakdown_chart(classifications)]
    if classification_note:
        breakdown_children.append(html.Div(classification_note, style={"fontSize": "10px", "color": MUTED, "marginTop": "8px"}))
    breakdown = html.Div([
        _section_title("EmONC Classification Breakdown"),
        _card(breakdown_children),
    ], style={"flex": "0 0 340px"})
    table = html.Div([
        _section_title("Facility Readiness Comparison"),
        _card([
        _data_table(
            ["District", "Facility", "Facility level", "EmONC classification",
             "Total deliveries", "Caesarean deliveries", "Neonatal unit admissions"],
            rows,
            classification_column="EmONC classification",
        ),
        ]),
    ], style={"flex": "1", "minWidth": "0"})
    comparison = html.Div([breakdown, table], style={
        "display": "flex", "gap": "16px", "flexWrap": "wrap", "alignItems": "flex-start",
    })
    return html.Div([summary, comparison])


# ---------------------------------------------------------------------------
# People / Products & Commodities / Systems & Infrastructure -- awaiting data
# ---------------------------------------------------------------------------

def _awaiting_detail_table(items: list[str]) -> dash_table.DataTable:
    rows = [[label, AWAITING_LABEL] for label in items]
    tones = ["awaiting"] * len(items)
    return _data_table(["Indicator", "Result"], rows, tone_column="Result", tones=tones)


def _awaiting_comparison_table(items: list[str], facility_codes: list[str]) -> dash_table.DataTable:
    facility_count = len(facility_codes)
    rows = [[label, facility_count, AWAITING_LABEL] for label in items]
    tones = ["awaiting"] * len(items)
    tooltips = [{"Available/reported": {
        "value": f"0 out of {facility_count} facilities", "type": "text",
    }} for _ in items]
    return _data_table(["Indicator", "Facilities assessed, n", "Available/reported"], rows,
                        tone_column="Available/reported", tones=tones, tooltips=tooltips)


def _awaiting_domain_detail_table(domain_items: list[tuple[str, str]]) -> dash_table.DataTable:
    rows = [[d, i, AWAITING_LABEL] for d, i in domain_items]
    tones = ["awaiting"] * len(domain_items)
    return _data_table(["Domain", "Item", "Result"], rows, tone_column="Result", tones=tones)


def _awaiting_domain_comparison_table(domain_items: list[tuple[str, str]], facility_codes: list[str]) -> dash_table.DataTable:
    facility_count = len(facility_codes)
    rows = [[d, i, facility_count, AWAITING_LABEL] for d, i in domain_items]
    tones = ["awaiting"] * len(domain_items)
    tooltips = [{"Available": {
        "value": f"0 out of {facility_count} facilities", "type": "text",
    }} for _ in domain_items]
    return _data_table(["Domain", "Item", "Facilities assessed, n", "Available"], rows,
                        tone_column="Available", tones=tones, tooltips=tooltips)


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
            _card([_section_title(f"Neonatal Care Unit Staffing · {_facility_label(code)}"), _awaiting_detail_table(CADRES_NEONATAL)]),
            _card([_section_title(f"Maternity Staffing · {_facility_label(code)}"), _awaiting_detail_table(CADRES_MATERNITY)]),
        ]),
        comparison_fn=lambda codes: html.Div([
            _card([_section_title("Neonatal Care Unit Staffing · Comparison"), _awaiting_comparison_table(CADRES_NEONATAL, codes)]),
            _card([_section_title("Maternity Staffing · Comparison"), _awaiting_comparison_table(CADRES_MATERNITY, codes)]),
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
            _card([_section_title(f"Maternity Equipment · {_facility_label(code)}"), _awaiting_domain_detail_table(EQUIPMENT_MATERNITY)]),
            _card([_section_title(f"Maternity Essential Medicines · {_facility_label(code)}"), _awaiting_domain_detail_table(TRACER_MEDICINES_MATERNITY)]),
            _card([_section_title(f"Newborn Equipment · {_facility_label(code)}"), _awaiting_domain_detail_table(EQUIPMENT_NEWBORN)]),
            _card([_section_title(f"Newborn Tracer Medicines · {_facility_label(code)}"), _awaiting_domain_detail_table(TRACER_MEDICINES_NEWBORN)]),
        ]),
        comparison_fn=lambda codes: html.Div([
            _card([_section_title("Maternity Equipment · Comparison"), _awaiting_domain_comparison_table(EQUIPMENT_MATERNITY, codes)]),
            _card([_section_title("Newborn Equipment & Medicines · Comparison"), _awaiting_domain_comparison_table(EQUIPMENT_NEWBORN + TRACER_MEDICINES_NEWBORN, codes)]),
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
            _card([_section_title(f"Maternity Unit Infrastructure · {_facility_label(code)}"), _awaiting_domain_detail_table(INFRASTRUCTURE_MATERNITY)]),
            _card([_section_title(f"Neonatal Care Unit Infrastructure · {_facility_label(code)}"), _awaiting_domain_detail_table(INFRASTRUCTURE_NEONATAL)]),
            _card([_section_title(f"Referral and Transport · {_facility_label(code)}"), _awaiting_detail_table(REFERRAL_TRANSPORT)]),
            _card([_section_title(f"Data and Quality-Improvement Systems · {_facility_label(code)}"), _awaiting_detail_table(DATA_QI_SYSTEMS)]),
        ]),
        comparison_fn=lambda codes: html.Div([
            _card([_section_title("Infrastructure · Comparison"), _awaiting_domain_comparison_table(INFRASTRUCTURE_MATERNITY + INFRASTRUCTURE_NEONATAL, codes)]),
            _card([_section_title("Referral, Transport & QI Systems · Comparison"), _awaiting_comparison_table(REFERRAL_TRANSPORT + DATA_QI_SYSTEMS, codes)]),
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
