import dash
from dash import html, dcc, dash_table, Input, Output, State, callback
from dash.exceptions import PreventUpdate
import json
import math
import os
import pandas as pd
import plotly.graph_objects as go

from data_storage import DataStorage
from config import (
    PROGRAM_, FACILITY_, DISTRICT_, DATE_, PERSON_ID_, ENCOUNTER_ID_, OBS_DATETIME_,
    CONCEPT_NAME_, ENCOUNTER_,AGE_GROUP_,
    IDENTIFIER_, FIRST_NAME_, LAST_NAME_, GENDER_, HOME_DISTRICT_, TA_, VILLAGE_,
    BIRTHDATE_, CELL_, DEMO_LOCATION, DEMO_UUID, FACILITY_CODE_, VALUE_NUMERIC_, DRUG_NAME_
)
from mnid.core.constants import BG, BORDER, TEXT
from dq.theme import BRAND, BRAND_TINT
import dq.theme  # noqa: F401 -- registers the "dq" Plotly template
from dq.checks.duplicates import (
    RULES as DUP_RULES, RULE_ORDER as DUP_RULE_ORDER, FIELD_OPTIONS as DUP_FIELD_OPTIONS,
    match_duplicates,
)
from dq.checks.validity import (
    RULES as VAL_RULES, RULE_ORDER as VAL_RULE_ORDER, rule_predicates as validity_rule_predicates,
)
from dq.checks.users import (
    RULES as USER_RULES, RULE_ORDER as USER_RULE_ORDER, load_user_roster, evaluate_users,
)

dash.register_page(__name__, path="/data_quality", title="Data Quality")

_CANDIDATES_PAGE_SIZE = 10

# Identity/Demographics core fields used for the Overview facility scorecard's
# field-completeness proxy. The Completeness tab lets the user pick any
# column set; this is a fixed, smaller stand-in so Overview has a real number
# today without duplicating that tab's column-picker logic.
CORE_FIELDS = [IDENTIFIER_, FIRST_NAME_, LAST_NAME_, GENDER_, HOME_DISTRICT_, TA_, VILLAGE_]

# The Completeness tab's own "Mandatory Demographics" picker -- (source
# column, display label) pairs a person record is evaluated against.
DEMOGRAPHIC_FIELD_OPTIONS = [
    (FIRST_NAME_, "Given name"),
    (LAST_NAME_, "Family name"),
    (GENDER_, "Gender"),
    (BIRTHDATE_, "Date of birth"),
    (HOME_DISTRICT_, "Home district"),
    (TA_, "TA"),
    (VILLAGE_, "Village"),
]

_TAB_STYLE = {
    "padding": "10px 18px",
    "border": f"1px solid {BORDER}",
    "backgroundColor": BG,
    "color": TEXT,
}
_TAB_SELECTED_STYLE = {
    "padding": "10px 18px",
    "border": f"1px solid {BRAND}",
    "backgroundColor": BRAND_TINT,
    "color": BRAND,
    "fontWeight": 700,
}

path = os.getcwd()
def _load_user_registry(route) -> pd.DataFrame:
    user_data_path = os.path.join(path, f'data/{route}','single_tables', 'users_data.csv')

    if os.path.exists(user_data_path):
        user_data = pd.read_csv(user_data_path)
    else:
        user_data = pd.DataFrame(columns=['user_id','uuid', 'role','user_level','district','facility_name','facility_code'])
    demo_row = {
        'user_id':1000000,
        'uuid': DEMO_UUID,
        'role': 'reports_admin',
        'user_level': 'national',
        'district': ["Lilongwe"],
        'facility_name': None,
        'facility_code': DEMO_LOCATION,
        'assigned_facility':'Biwi Health Centre'
    }

    user_data = pd.concat([user_data, pd.DataFrame([demo_row])], ignore_index=True)
    for column in ['uuid', 'role', 'user_level', 'district', 'facility_code', 'facility_name']:
        if column not in user_data.columns:
            user_data[column] = pd.NA

    def parse_list(val):
        if pd.isna(val):
            return None
        if isinstance(val, str) and ',' in val:
            return [x.strip() for x in val.split(',')]
        return val

    user_data['district'] = user_data['district'].apply(parse_list)
    user_data['facility_name'] = user_data['facility_name'].apply(parse_list)

    return user_data

def _scope_where_parts(effective_level, location, districts, user_districts, facilities, age, programs=None, is_network=False):
    """Return SQL WHERE clause parts for the given scope and level.

    is_network=True omits the per-facility filter so the network query covers
    the full district/national context for trend comparison.
    """
    parts = []
    if effective_level == 'facility':
        if not is_network and location:
            parts.append(f"{FACILITY_CODE_} = '{location}'")
    elif effective_level == 'district':
        active_dists = districts or user_districts
        if active_dists:
            quoted_dists = ", ".join([f"'{d}'" for d in active_dists])
            parts.append(f"{DISTRICT_} IN ({quoted_dists})")
        if not is_network and facilities:
            quoted_facilities = ", ".join([f"'{f}'" for f in facilities])
            parts.append(f"{FACILITY_} IN ({quoted_facilities})")
    elif effective_level == 'national':
        if districts:
            quoted_dists = ", ".join([f"'{d}'" for d in districts])
            parts.append(f"{DISTRICT_} IN ({quoted_dists})")
        if not is_network and facilities:
            quoted_facilities = ", ".join([f"'{f}'" for f in facilities])
            parts.append(f"{FACILITY_} IN ({quoted_facilities})")
    if age:
        parts.append(f"{AGE_GROUP_} = '{age}'")
    if programs:
        quoted_programs = ", ".join([f"'{p}'" for p in programs])
        parts.append(f"{PROGRAM_} IN ({quoted_programs})")
    return parts

def _load_user_properties(route) -> list:
    props_path = os.path.join(os.getcwd(), f'data/{route}', 'dcc_dropdown_json', 'user_properties.json')
    try:
        with open(props_path) as f:
            return json.load(f).get('users', [])
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []

def _normalize_level(value: str | None) -> str:
    value = str(value or '').strip().lower()
    if value in {'national', 'district', 'facility'}:
        return value
    return 'facility'


def _resolve_user_scope(urlparams, user_data: pd.DataFrame):
    requested_uuid = urlparams.get('uuid', [None])[0] if urlparams else None
    data_route = urlparams.get('route', ["default"])[0] if urlparams else None

    # Check user_properties.json first (GUI-configured overrides)
    for entry in _load_user_properties(data_route):
        p = entry.get('properties', {})
        if p.get('uuid') == requested_uuid:
            level     = _normalize_level(p.get('user_level'))
            districts = p.get('district')
            if isinstance(districts, str):
                districts = [districts] if districts else []
            facilities = p.get('facility_name')
            if isinstance(facilities, str):
                facilities = [facilities] if facilities else []
            scope = {
                'level':      level,
                'districts':  districts  or [],
                'facilities': facilities or [],
                'facility_code': p.get('facility_code'),
            }
            # Still return a dataframe row so callers that use row.get(...) don't break
            user_info = user_data[user_data['uuid'] == requested_uuid]
            row = user_info.iloc[0] if not user_info.empty else None
            return row, scope

    # Fall back to users_data dataframe
    user_info = user_data[user_data['uuid'] == requested_uuid]
    if user_info.empty:
        return None, {}
    row   = user_info.iloc[0]
    level = _normalize_level(row.get('user_level'))
    scope = {
        'level':      level,
        'districts':  row.get('district'),
        'facilities': row.get('facility_name'),
        'facility_code': row.get('facility_code'),
    }
    return row, scope

def _iso_date(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _latest_full_month_window(max_date):
    """Latest full calendar month present in the data, given its max date."""
    if max_date is None or pd.isna(max_date):
        return None, None
    max_date = pd.Timestamp(max_date)
    month_start = max_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = month_start + pd.DateOffset(months=1)
    if max_date < next_month_start - pd.Timedelta(days=1):
        month_start = month_start - pd.DateOffset(months=1)
        next_month_start = month_start + pd.DateOffset(months=1)
    month_end = next_month_start - pd.Timedelta(days=1)
    return month_start.date().isoformat(), month_end.date().isoformat()


def _ceiling_scope_where(level, location, user_districts):
    """WHERE parts for the user's own scope ceiling -- no user-selected narrowing."""
    parts = _scope_where_parts(level, location, None, user_districts, None, None)
    return " AND ".join(parts) if parts else "1=1"


def _selection_where(level, location, user_districts, selected_districts, selected_facilities, program, start_date, end_date):
    """WHERE clause for the user's scope ceiling narrowed by the filter bar's
    own selections (district scope, facility, programme, date range)."""
    parts = _scope_where_parts(
        level, location, selected_districts or None, user_districts, selected_facilities or None, None,
        programs=[program] if program else None,
    )
    if start_date and end_date:
        parts.append(f"{DATE_} BETWEEN '{start_date}'::TIMESTAMP AND '{end_date} 23:59:59'::TIMESTAMP")
    return " AND ".join(parts) if parts else "1=1"


def _dq_prefs_path(route):
    return os.path.join(os.getcwd(), f'data/{route}', 'dcc_dropdown_json', 'dq_preferences.json')


def _load_dq_prefs(route, uuid, program):
    """Per-user Completeness rules, saved under
    data/{route}/dcc_dropdown_json/dq_preferences.json. Demographics are
    global to the uuid; encounters are mapped per-programme (this uuid can
    have a different mandatory encounter list for each programme), stored as
    entry["encounters"] == [{"Program": ..., "encounter_list": [...]}, ...].

    Returns None when this uuid has no saved entry at all -- distinguishes
    "never configured anything here" (the Completeness tab should stay quiet,
    no rule => no issues) from "configured, but this particular programme has
    no mandatory encounters yet" (encounter_list just comes back empty).
    """
    if not uuid:
        return None
    try:
        with open(_dq_prefs_path(route)) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    entry = next((u for u in data.get("users", []) if u.get("uuid") == uuid), None)
    if entry is None:
        return None
    encounters_by_program = entry.get("encounters") or []
    encounter_list = next(
        (p.get("encounter_list") or [] for p in encounters_by_program if p.get("Program") == program),
        [],
    )
    return {
        "demographics": entry.get("demographics") or [],
        "encounters": encounter_list,
    }


def _save_dq_prefs(route, uuid, program, encounters, demographics):
    if not uuid:
        return
    prefs_path = _dq_prefs_path(route)
    try:
        with open(prefs_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    users = data.setdefault("users", [])
    entry = next((u for u in users if u.get("uuid") == uuid), None)
    if entry is None:
        entry = {"uuid": uuid, "demographics": [], "encounters": []}
        users.append(entry)

    entry["demographics"] = demographics or []

    if program:
        encounters_by_program = entry.setdefault("encounters", [])
        prog_entry = next((p for p in encounters_by_program if p.get("Program") == program), None)
        if prog_entry is None:
            prog_entry = {"Program": program, "encounter_list": []}
            encounters_by_program.append(prog_entry)
        prog_entry["encounter_list"] = encounters or []

    os.makedirs(os.path.dirname(prefs_path), exist_ok=True)
    with open(prefs_path, 'w') as f:
        json.dump(data, f, indent=2)


def _presence_expr(col):
    return f'("{col}" IS NOT NULL AND trim(CAST("{col}" AS VARCHAR)) <> \'\')'


def _rule_fields_display(rid, other_fields):
    """D4 ("Other") has no fixed field set -- show whatever the user actually
    picked instead of DUP_RULES's static placeholder text."""
    if rid == "D4":
        if other_fields:
            label_map = dict(DUP_FIELD_OPTIONS)
            return " + ".join(label_map.get(f, f) for f in other_fields)
        return "no fields selected"
    return DUP_RULES[rid]["fields"]


def _tab_label_and_style(base_label, count):
    """Tab label/style pair showing an issue count directly on the tab
    itself (e.g. "Duplicates (2 issues)") -- dcc.Tab's label can only be
    plain text, so the whole label (not just the number) turns red when
    there's something to look at."""
    if count is None:
        return base_label, _TAB_STYLE
    if not count:
        return base_label, _TAB_STYLE
    label = f"{base_label} ({count} issue{'s' if count != 1 else ''})"
    return label, {**_TAB_STYLE, "color": "#DC2626", "fontWeight": 700}


def _kpi_card(label, value, sub=None, rag=None):
    """rag, when given, is 'red'/'amber'/'green' -- tints the card border
    and value to flag a rate that's crossed a threshold, independent of
    (and in addition to) any trend note passed via sub."""
    children = [
        html.Div(label, className="dq-kpi-label"),
        html.Div(value, className="dq-kpi-value"),
    ]
    if sub:
        children.append(html.Div(sub, className="dq-kpi-sub"))
    class_name = "dq-kpi-card" + (f" rag-{rag}" if rag else "")
    return html.Div(children, className=class_name)


def _rag_for_rate(pct, good=95.0, warn=85.0, higher_is_better=True):
    """RAG threshold for a rate-style metric (e.g. completeness %). For a
    'higher is better' rate (completeness, match quality): green at/above
    `good`, amber at/above `warn`, red below. Pass higher_is_better=False
    for a rate where lower is better (e.g. a defect/duplicate rate) and
    the same two thresholds are read as upper bounds instead."""
    if pct is None:
        return None
    if higher_is_better:
        if pct >= good:
            return "green"
        if pct >= warn:
            return "amber"
        return "red"
    else:
        if pct <= (100 - good):
            return "green"
        if pct <= (100 - warn):
            return "amber"
        return "red"


def _previous_period(start_date, end_date):
    """The immediately-preceding window of the same length as
    [start_date, end_date] -- what a trend delta is computed against.
    Returns (None, None) when either bound is missing."""
    if not start_date or not end_date:
        return None, None
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    length_days = (end - start).days + 1
    prev_end = start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=length_days - 1)
    return prev_start.date().isoformat(), prev_end.date().isoformat()


def _trend_sub(current_pct, previous_pct):
    """A KPI card's 'sub' line for a rate's trend vs the prior period --
    None (not shown) when there's nothing to compare against."""
    if current_pct is None or previous_pct is None:
        return "No prior-period data"
    delta = current_pct - previous_pct
    if abs(delta) < 0.05:
        return "No change vs prior period"
    arrow = "▲" if delta > 0 else "▼"
    return f"{arrow} {abs(delta):.1f} pts vs prior period"


def _empty_state(title, body):
    return html.Div(
        [html.Div(title, className="dq-empty-state-title"), html.Div(body)],
        className="dq-empty-state",
    )


def _data_source_available(data_route: str) -> bool:
    """Whether data/{route}/parquet exists and actually has a file in it --
    lets every tab tell "this route has no raw extract at all" apart from
    "the extract exists but nothing matches the current filters," which a
    bare empty-dataframe check can't distinguish. DHIS2/HMIS is the route
    that's normally missing this entirely (analytics-only, no raw rows).
    """
    parquet_dir = os.path.join(os.getcwd(), f"data/{data_route}", "parquet")
    try:
        return os.path.isdir(parquet_dir) and any(
            f.endswith(".parquet") for f in os.listdir(parquet_dir)
        )
    except OSError:
        return False


def _data_source_health_banner(data_route: str, tab_label: str = "Validity and outliers"):
    return (
        html.Div(
            f"No raw data extract exists for the '{data_route}' route "
            f"(data/{data_route}/parquet is missing or empty). This is a data-source availability "
            "problem, not a result of your filters -- every check on this page needs that extract to "
            "run, and showing zero issues here would misleadingly look like a clean result.",
            className="dq-status-message",
        ),
        tab_label, _TAB_STYLE,
    )


def _evaluate_completeness(data_path, where, mandatory_demographics, mandatory_encounters):
    """Shared by the Overview and Completeness tabs so both report the exact
    same numbers -- one row per person_id with facility/identifier/name
    columns, a presence flag per mandatory field/encounter, and a derived
    is_complete. Returns (df, demo_flag_cols, enc_flag_cols); df is empty
    when there's no rule to evaluate (both lists empty) or nothing matched.
    """
    mandatory_demographics = mandatory_demographics or []
    mandatory_encounters = mandatory_encounters or []
    demo_flag_cols = [f"demo_{i}" for i in range(len(mandatory_demographics))]
    enc_flag_cols = [f"enc_{i}" for i in range(len(mandatory_encounters))]

    if not mandatory_demographics and not mandatory_encounters:
        return pd.DataFrame(), demo_flag_cols, enc_flag_cols

    select_parts = [
        f"{PERSON_ID_} AS person_id",
        f"MAX({FACILITY_}) AS facility",
        f"MAX({IDENTIFIER_}) AS identifier",
        f"MAX({FIRST_NAME_}) AS given_name",
        f"MAX({LAST_NAME_}) AS family_name",
    ]
    for col, flag in zip(mandatory_demographics, demo_flag_cols):
        select_parts.append(f"MAX(CASE WHEN {_presence_expr(col)} THEN 1 ELSE 0 END) AS {flag}")
    for enc, flag in zip(mandatory_encounters, enc_flag_cols):
        safe_enc = str(enc).replace("'", "''")
        select_parts.append(f"MAX(CASE WHEN {ENCOUNTER_} = '{safe_enc}' THEN 1 ELSE 0 END) AS {flag}")

    try:
        df = DataStorage.query_duckdb(
            f"SELECT {', '.join(select_parts)} FROM '{data_path}' WHERE {where} GROUP BY {PERSON_ID_}"
        )
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        return df, demo_flag_cols, enc_flag_cols

    df["demographics_complete"] = df[demo_flag_cols].eq(1).all(axis=1) if demo_flag_cols else True
    df["encounters_complete"] = df[enc_flag_cols].eq(1).all(axis=1) if enc_flag_cols else True
    df["is_complete"] = df["demographics_complete"] & df["encounters_complete"]
    return df, demo_flag_cols, enc_flag_cols


layout = html.Div(
    className="dq-page",
    children=[
        html.Div(
            className="dq-header-row",
            children=[
                # html.Div(
                #     className="dq-header-title-col",
                #     children=[html.H2("Data Quality", className="dq-page-title")],
                # ),
                html.Div(
                    id="dq-filter-bar",
                    className="dq-header-filters-col config-controls-grid",
                    children=[
                        html.Div(
                            className="config-control-group",
                            children=[
                                html.Label("Date Range", className="config-label"),
                                dcc.DatePickerRange(
                                    id="dq-date-range",
                                    display_format="YYYY-MM-DD",
                                    minimum_nights=0,
                                    className="modern-datepicker-range",
                                ),
                            ],
                        ),
                        html.Div(
                            className="config-control-group",
                            children=[
                                html.Label("Scope", className="config-label"),
                                dcc.Dropdown(
                                    id="dq-scope",
                                    options=[], value=[], multi=True, clearable=True,
                                    placeholder="All districts in scope",
                                    className="modern-dropdown",
                                ),
                            ],
                        ),
                        html.Div(
                            className="config-control-group",
                            children=[
                                html.Label("Health Facility", className="config-label"),
                                dcc.Dropdown(
                                    id="dq-facility-filter",
                                    options=[], value=[], multi=True, clearable=True,
                                    placeholder="All facilities in scope",
                                    className="modern-dropdown",
                                ),
                            ],
                        ),
                        html.Div(
                            className="config-control-group",
                            children=[
                                html.Label("Programme", className="config-label"),
                                dcc.Dropdown(
                                    id="dq-program-filter",
                                    options=[], value=None, multi=False, clearable=False,
                                    placeholder="Choose a programme…",
                                    className="modern-dropdown",
                                ),
                            ],
                        ),
                        html.Div(
                            className="config-control-group",
                            children=[
                                html.Label(" ", className="config-label"),
                                html.Button("Run DQ", id="dq-run-btn", n_clicks=0, className="dq-run-btn"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        html.Div(id="dq-alert"),
        dcc.Store(id="dq-last-run-filters"),
        html.Div(
            "Filters changed since the last run — click \"Run DQ\" above to refresh these results.",
            id="dq-stale-banner",
            className="dq-stale-banner",
            style={"display": "none"},
        ),
        html.Div(
            id="dq-tabs-wrapper",
            children=[
                dcc.Tabs(
                    id="dq-tabs",
                    value="overview",
                    children=[
                        dcc.Tab(
                            label="Overview", value="overview",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(id="dq-overview-content", className="results-card"),
                        ),
                        dcc.Tab(
                            id="dq-tab-duplicates",
                            label="Duplicates", value="duplicates",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(
                                className="results-card",
                                children=[
                                    html.Div(
                                        className="dq-panel",
                                        children=[
                                            html.H4("Matching rules", className="dq-panel-title"),
                                            html.Div(
                                                "Match score is a hand-assigned weight per rule (how specific its "
                                                "matching fields are), not a calibrated probability that two "
                                                "records are the same patient.",
                                                className="dq-panel-note",
                                            ),
                                            dcc.Checklist(
                                                id="dq-dup-rules",
                                                options=[
                                                    {
                                                        "label": f"{rid} — {DUP_RULES[rid]['label']} "
                                                                 f"(match score {DUP_RULES[rid]['confidence']:.2f}, "
                                                                 f"{DUP_RULES[rid]['severity']} severity)",
                                                        "value": rid,
                                                    }
                                                    for rid in DUP_RULE_ORDER
                                                ],
                                                value=["D1", "D2", "D3"],
                                                className="dq-checklist-row",
                                                labelClassName="dq-checklist-label",
                                                inputStyle={"marginRight": "6px"},
                                            ),
                                            html.Div(
                                                id="dq-dup-other-fields-group",
                                                className="config-control-group",
                                                style={"marginTop": "12px", "display": "none"},
                                                children=[
                                                    html.Label(
                                                        "D4 (Other) fields",
                                                        className="config-label",
                                                    ),
                                                    dcc.Dropdown(
                                                        id="dq-dup-other-fields",
                                                        options=[
                                                            {"label": label, "value": value}
                                                            for value, label in DUP_FIELD_OPTIONS
                                                        ],
                                                        value=[], multi=True, clearable=True,
                                                        placeholder="Choose fields to match on…",
                                                        className="modern-dropdown",
                                                    ),
                                                ],
                                            ),
                                            html.Label(
                                                "Minimum match score to show",
                                                className="config-label",
                                                style={"marginTop": "12px", "display": "block"},
                                            ),
                                            dcc.Slider(
                                                id="dq-dup-min-confidence",
                                                min=0, max=1, step=0.01, value=0,
                                                marks={0: "0", 0.25: "0.25", 0.5: "0.5", 0.75: "0.75", 1: "1"},
                                                tooltip={"placement": "bottom", "always_visible": False},
                                            ),
                                        ],
                                    ),
                                    dcc.Store(id="dq-dup-candidates-page", data=1),
                                    html.Div(id="dq-duplicates-content"),
                                ],
                            ),
                        ),
                        dcc.Tab(
                            id="dq-tab-completeness",
                            label="Completeness", value="completeness",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(
                                className="results-card",
                                children=[
                                    html.Div(
                                        className="dq-panel",
                                        children=[
                                            html.H4("Completeness rules", className="dq-panel-title"),
                                            html.Div(
                                                className="config-controls-grid",
                                                children=[
                                                    html.Div(
                                                        className="config-control-group",
                                                        children=[
                                                            html.Label(
                                                                "Mandatory Demographics",
                                                                className="config-label",
                                                            ),
                                                            dcc.Dropdown(
                                                                id="dq-completeness-demographics",
                                                                options=[
                                                                    {"label": label, "value": value}
                                                                    for value, label in DEMOGRAPHIC_FIELD_OPTIONS
                                                                ],
                                                                value=[], multi=True, clearable=True,
                                                                placeholder="Select required demographic fields…",
                                                                className="modern-dropdown",
                                                            ),
                                                        ],
                                                    ),
                                                    html.Div(
                                                        className="config-control-group",
                                                        children=[
                                                            html.Label(
                                                                "Mandatory Encounters",
                                                                className="config-label",
                                                            ),
                                                            dcc.Dropdown(
                                                                id="dq-completeness-encounters",
                                                                options=[], value=[], multi=True, clearable=True,
                                                                placeholder="Select required encounters…",
                                                                className="modern-dropdown",
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                "A person record is complete when every selected demographic "
                                                "field is filled in and the person has at least one record "
                                                "under each selected encounter. Encounter options reflect the "
                                                "selected programme's last 7 days of activity. Your selection "
                                                "is remembered for next time.",
                                                className="dq-panel-note",
                                            ),
                                        ],
                                    ),
                                    html.Div(id="dq-prefs-save-status", style={"display": "none"}),
                                    html.Div(id="dq-completeness-content", className="card-2"),
                                ],
                            ),
                        ),
                        dcc.Tab(
                            id="dq-tab-validity",
                            label="Validity and outliers", value="validity",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(
                                className="results-card",
                                children=[
                                    html.Div(
                                        className="dq-panel",
                                        children=[
                                            html.H4("Checks to run", className="dq-panel-title"),
                                            dcc.Checklist(
                                                id="dq-val-rules",
                                                options=[
                                                    {
                                                        "label": f"{rid} — {VAL_RULES[rid]['label']} ({VAL_RULES[rid]['category']})",
                                                        "value": rid,
                                                    }
                                                    for rid in VAL_RULE_ORDER
                                                ],
                                                value=list(VAL_RULE_ORDER),
                                                className="dq-checklist-row",
                                                labelClassName="dq-checklist-label",
                                                inputStyle={"marginRight": "6px"},
                                            ),
                                        ],
                                    ),
                                    html.Div(id="dq-validity-content"),
                                ],
                            ),
                        ),
                        dcc.Tab(
                            id="dq-tab-users",
                            label="Users", value="users",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(
                                className="results-card",
                                children=html.Div(id="dq-users-content"),
                            ),
                        ),
                    ],
                ),
            ],
        ),
    ],
)


@callback(
    Output("dq-last-run-filters", "data"),
    Input("dq-run-btn", "n_clicks"),
    State("dq-date-range", "start_date"),
    State("dq-date-range", "end_date"),
    State("dq-scope", "value"),
    State("dq-facility-filter", "value"),
    State("dq-program-filter", "value"),
    prevent_initial_call=True,
)
def snapshot_filters_on_run(_n_clicks, start_date, end_date, districts, facilities, program):
    """Captures the shared filter bar's values at the moment "Run DQ" is
    clicked -- compared against live filter values below to tell whether
    what's on screen still reflects the current filters."""
    return {
        "start_date": start_date, "end_date": end_date,
        "districts": districts, "facilities": facilities, "program": program,
    }


@callback(
    Output("dq-tabs-wrapper", "className"),
    Output("dq-stale-banner", "style"),
    Input("dq-date-range", "start_date"),
    Input("dq-date-range", "end_date"),
    Input("dq-scope", "value"),
    Input("dq-facility-filter", "value"),
    Input("dq-program-filter", "value"),
    State("dq-last-run-filters", "data"),
)
def flag_stale_results(start_date, end_date, districts, facilities, program, last_run):
    """Dims the results and shows a banner when the shared filter bar no
    longer matches what was actually used for the results on screen --
    scoped to the shared bar only (date/scope/facility/programme), not each
    tab's own extra pickers (rule checklists etc.), to keep this to one
    mechanism instead of five bespoke ones."""
    current = {
        "start_date": start_date, "end_date": end_date,
        "districts": districts, "facilities": facilities, "program": program,
    }
    if last_run is None or current == last_run:
        return "", {"display": "none"}
    return "dq-stale", {"display": "block"}


@callback(
    Output("dq-alert", "children"),
    Output("dq-filter-bar", "style"),
    Output("dq-tabs-wrapper", "style"),
    Output("dq-scope", "options"),
    Output("dq-scope", "value"),
    Output("dq-scope", "disabled"),
    Output("dq-facility-filter", "options"),
    Output("dq-facility-filter", "value"),
    Output("dq-facility-filter", "disabled"),
    Output("dq-program-filter", "options"),
    Output("dq-program-filter", "value"),
    Output("dq-date-range", "start_date"),
    Output("dq-date-range", "end_date"),
    Output("dq-date-range", "min_date_allowed"),
    Output("dq-date-range", "max_date_allowed"),
    Input("url-params-store", "data"),
)
def initialize_data_quality_filters(urlparams):
    urlparams = urlparams or {}
    hidden = {"display": "none"}
    unauthorized = (
        html.Div("Unauthorized user. Please contact your system administrator.", className="dq-status-message"),
        hidden, hidden,
        [], [], True,
        [], [], True,
        [], None,
        None, None, None, None,
    )

    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]

    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None:
        return unauthorized

    if not location:
        return (
            html.Div("Missing Location parameter.", className="dq-status-message"),
            hidden, hidden,
            [], [], True,
            [], [], True,
            [], None,
            None, None, None, None,
        )

    data_path = f"data/{data_route}/parquet"
    level = scope.get("level")
    user_districts = scope.get("districts") or []
    if isinstance(user_districts, str):
        user_districts = [user_districts]

    ceiling_where = _ceiling_scope_where(level, location, user_districts)

    try:
        dist_df = DataStorage.query_duckdb(
            f"SELECT DISTINCT {DISTRICT_} FROM '{data_path}' WHERE {ceiling_where} ORDER BY {DISTRICT_}"
        )
        district_options = dist_df[DISTRICT_].dropna().tolist()
    except Exception:
        district_options = []

    try:
        fac_df = DataStorage.query_duckdb(
            f"SELECT DISTINCT {FACILITY_} FROM '{data_path}' WHERE {ceiling_where} ORDER BY {FACILITY_}"
        )
        facility_options = fac_df[FACILITY_].dropna().tolist()
    except Exception:
        facility_options = []

    try:
        prog_df = DataStorage.query_duckdb(
            f"SELECT {PROGRAM_}, COUNT(*) AS n FROM '{data_path}' WHERE {ceiling_where} "
            f"GROUP BY {PROGRAM_} ORDER BY n DESC"
        )
        program_options = prog_df[PROGRAM_].dropna().tolist()
        default_program = program_options[0] if program_options else None
    except Exception:
        program_options, default_program = [], None

    try:
        bounds_df = DataStorage.query_duckdb(f"SELECT MIN({DATE_}) AS min_d, MAX({DATE_}) AS max_d FROM '{data_path}'")
        min_date, max_date = bounds_df["min_d"][0], bounds_df["max_d"][0]
    except Exception:
        min_date, max_date = None, None

    start_date, end_date = _latest_full_month_window(max_date)

    # A district- or facility-level user is already ceilinged to their own
    # district(s) -- Scope has nothing left to narrow, so it's locked to
    # exactly what the ceiling query returned. Only a national-level user
    # picks among more than one district.
    district_disabled = level in ("district", "facility")
    district_value = district_options if district_disabled else []

    facility_disabled = level == "facility"
    facility_value = facility_options if facility_disabled else []

    return (
        None, {}, {},
        [{"label": d, "value": d} for d in district_options], district_value, district_disabled,
        [{"label": f, "value": f} for f in facility_options], facility_value, facility_disabled,
        [{"label": p, "value": p} for p in program_options], default_program,
        start_date, end_date, _iso_date(min_date), _iso_date(max_date),
    )


@callback(
    Output("dq-facility-filter", "options", allow_duplicate=True),
    Output("dq-facility-filter", "value", allow_duplicate=True),
    Input("dq-scope", "value"),
    State("url-params-store", "data"),
    prevent_initial_call=True,
)
def sync_dq_facility_options_from_scope(selected_districts, urlparams):
    """Narrows the Health Facility dropdown's options to whichever
    district(s) are picked in Scope -- mirrors the ceiling-ordered query
    initialize_data_quality_filters runs, just with the Scope selection
    folded into the WHERE clause instead of left out of it."""
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]
    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        raise PreventUpdate

    level = scope.get("level")
    if level == "facility":
        raise PreventUpdate

    user_districts = scope.get("districts") or []
    if isinstance(user_districts, str):
        user_districts = [user_districts]

    data_path = f"data/{data_route}/parquet"
    where_parts = _scope_where_parts(level, location, selected_districts or None, user_districts, None, None)
    where = " AND ".join(where_parts) if where_parts else "1=1"

    try:
        fac_df = DataStorage.query_duckdb(
            f"SELECT DISTINCT {FACILITY_} FROM '{data_path}' WHERE {where} ORDER BY {FACILITY_}"
        )
        facility_options = fac_df[FACILITY_].dropna().tolist()
    except Exception:
        facility_options = []

    return [{"label": f, "value": f} for f in facility_options], []


@callback(
    Output("dq-overview-content", "children"),
    Input("url-params-store", "data"),
    Input("dq-run-btn", "n_clicks"),
    State("dq-date-range", "start_date"),
    State("dq-date-range", "end_date"),
    State("dq-scope", "value"),
    State("dq-facility-filter", "value"),
    State("dq-program-filter", "value"),
    State("dq-completeness-demographics", "value"),
    State("dq-completeness-encounters", "value"),
)
def render_overview_tab(urlparams, run_clicks, start_date, end_date, districts, facilities, program,
                         mandatory_demographics, mandatory_encounters):
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]
    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None

    if not run_clicks:
        return _empty_state(
            "Click \"Run DQ\" to see results",
            "Set your filters above, then click Run DQ to compute this tab.",
        )

    if not _data_source_available(data_route):
        banner, _, _ = _data_source_health_banner(data_route)
        return banner

    if not program:
        return _empty_state(
            "Select a programme",
            "Every number on this tab is scoped to one programme at a time -- "
            "a patient is identified by programme, so there is nothing to compute without one.",
        )

    data_path = f"data/{data_route}/parquet"
    level = scope.get("level")
    user_districts = scope.get("districts") or []
    if isinstance(user_districts, str):
        user_districts = [user_districts]

    where = _selection_where(level, location, user_districts, districts, facilities, program, start_date, end_date)

    # Reuses the exact same evaluation as the Completeness tab (same
    # mandatory-fields/encounters rule, same scope) so "Patients with
    # complete data" and "Field completeness %" below always agree with
    # that tab's own numbers instead of drifting out of sync.
    completeness_df, _, _ = _evaluate_completeness(
        data_path, where, mandatory_demographics, mandatory_encounters
    )
    if not completeness_df.empty:
        completeness_rate = completeness_df["is_complete"].mean() * 100
        facility_completeness = (
            completeness_df.groupby("facility")["is_complete"].mean() * 100
        ).round(1)
    else:
        completeness_rate = None
        facility_completeness = pd.Series(dtype=float)

    try:
        kpi_df = DataStorage.query_duckdb(
            f"SELECT COUNT(DISTINCT {PERSON_ID_}) AS patients, "
            f"COUNT(DISTINCT ({PERSON_ID_}, {CONCEPT_NAME_})) AS obs_rows "
            f"FROM '{data_path}' WHERE {where}"
        )
    except Exception:
        kpi_df = pd.DataFrame()

    if kpi_df.empty or int(kpi_df["obs_rows"][0]) == 0:
        return _empty_state(
            "No records match the current filters",
            "Try widening the date range, clearing the facility filter, or choosing a different programme.",
        )

    patients = int(kpi_df["patients"][0])
    obs_rows = int(kpi_df["obs_rows"][0])

    try:
        roster_df = DataStorage.query_duckdb(
            f"SELECT {PERSON_ID_} AS person_id, "
            f"MAX({LAST_NAME_}) AS family_name, MAX({FIRST_NAME_}) AS given_name, "
            f"MAX({GENDER_}) AS gender, MAX({BIRTHDATE_}) AS birthdate, "
            f"MAX({IDENTIFIER_}) AS identifier, MAX({VILLAGE_}) AS village, "
            f"MAX({TA_}) AS ta, MAX({HOME_DISTRICT_}) AS home_district, MAX({CELL_}) AS cell, "
            f"MAX({FACILITY_}) AS facility, COUNT(DISTINCT {ENCOUNTER_ID_}) AS encounter_count, "
            f"MIN({DATE_}) AS first_encounter, MAX({DATE_}) AS last_encounter "
            f"FROM '{data_path}' WHERE {where} GROUP BY {PERSON_ID_}"
        )
        duplicate_groups, _ = match_duplicates(roster_df, DUP_RULE_ORDER) if not roster_df.empty else ([], {})
    except Exception:
        duplicate_groups = []

    kpi_strip = html.Div(
        [
            _kpi_card("Patients in programme", f"{patients:,}"),
            _kpi_card("Observation rows", f"{obs_rows:,}"),
            _kpi_card("Duplicate groups", f"{len(duplicate_groups):,}"),
            _kpi_card("Records failing a rule", "—", "Needs the Validity tab"),
            _kpi_card(
                "Field completeness %",
                f"{completeness_rate:.1f}%" if completeness_rate is not None else "—",
                "Same rule as the Completeness tab" if completeness_rate is not None else "Needs a completeness rule",
                rag=_rag_for_rate(completeness_rate),
            ),
        ],
        className="dq-kpi-row",
    )

    try:
        vol_df = DataStorage.query_duckdb(
            f"SELECT DATE({DATE_}) AS d, COUNT(DISTINCT ({PERSON_ID_}, {CONCEPT_NAME_})) AS n "
            f"FROM '{data_path}' WHERE {where} GROUP BY d ORDER BY d"
        )
    except Exception:
        vol_df = pd.DataFrame(columns=["d", "n"])

    vol_fig = go.Figure(go.Bar(
        x=vol_df["d"], y=vol_df["n"],
        text=vol_df["n"], texttemplate="%{text:,}",
        textposition="inside", insidetextanchor="end",
    ))
    vol_fig.update_layout(template="dq", xaxis_title=None, yaxis_title="Observations")

    volume_panel = html.Div(
        [
            html.H4("Observations volume by day", className="dq-panel-title"),
            dcc.Graph(figure=vol_fig, config={"displayModeBar": False}),
        ],
        className="dq-panel",
    )

    try:
        fac_df = DataStorage.query_duckdb(
            f'SELECT {FACILITY_} AS "Facility", '
            f'COUNT(DISTINCT {PERSON_ID_}) AS "Patients", '
            f'COUNT(DISTINCT {ENCOUNTER_ID_}) AS "Encounters", '
            f'COUNT(DISTINCT ({PERSON_ID_}, {CONCEPT_NAME_})) AS "Rows" '
            f"FROM '{data_path}' WHERE {where} GROUP BY {FACILITY_} ORDER BY \"Patients\" DESC"
        )
    except Exception:
        fac_df = pd.DataFrame()

    if not fac_df.empty:
        fac_df["Field completeness %"] = (
            fac_df["Facility"].map(facility_completeness) if not facility_completeness.empty else pd.NA
        )
        fac_df["Field completeness %"] = fac_df["Field completeness %"].apply(
            lambda v: "-" if pd.isna(v) else v
        )

        try:
            dup_roster_df = DataStorage.query_duckdb(
                f"SELECT {PERSON_ID_} AS person_id, {FACILITY_} AS facility_group, "
                f"MAX({LAST_NAME_}) AS family_name, MAX({FIRST_NAME_}) AS given_name, "
                f"MAX({GENDER_}) AS gender, MAX({BIRTHDATE_}) AS birthdate, "
                f"MAX({IDENTIFIER_}) AS identifier, MAX({VILLAGE_}) AS village, "
                f"MAX({TA_}) AS ta, MAX({HOME_DISTRICT_}) AS home_district, MAX({CELL_}) AS cell, "
                f"MAX({FACILITY_}) AS facility, COUNT(DISTINCT {ENCOUNTER_ID_}) AS encounter_count, "
                f"MIN({DATE_}) AS first_encounter, MAX({DATE_}) AS last_encounter "
                f"FROM '{data_path}' WHERE {where} GROUP BY {PERSON_ID_}, {FACILITY_}"
            )
        except Exception:
            dup_roster_df = pd.DataFrame()

        dup_group_counts = {}
        if not dup_roster_df.empty:
            for facility_name, sub_roster in dup_roster_df.groupby("facility_group"):
                groups, _ = match_duplicates(sub_roster, DUP_RULE_ORDER)
                dup_group_counts[facility_name] = len(groups)
        fac_df["Duplicate groups"] = fac_df["Facility"].map(dup_group_counts).fillna(0).astype(int)

        fac_df["% of Patients Completing Workflow"] = "-"

    scorecard_panel = html.Div(
        [
            html.H4("Facility scorecard", className="dq-panel-title"),
            html.Div(
                "Field completeness % uses the same mandatory-fields/encounters rule as the "
                "Completeness tab (shows '-' when no rule is set there); "
                "% of Patients Completing Workflow is still a placeholder pending a definition; "
                "Duplicate groups matches this facility's own roster the same way the Duplicates tab does.",
                className="dq-panel-note",
            ),
            dash_table.DataTable(
                columns=[{"name": c, "id": c} for c in fac_df.columns],
                data=fac_df.to_dict("records"),
                page_size=15,
                sort_action="native",
            ) if not fac_df.empty else html.Div("No facility rows in scope.", className="dq-empty-state"),
        ],
        className="dq-panel",
    )

    deferred_panel = _empty_state(
        "Not yet available",
        html.Ul(
            [
                html.Li("Duplicate groups and the duplicate rate -- computed by the Duplicates tab."),
                html.Li("Records failing a rule and the defects table -- computed by the Validity tab's rule list."),
                html.Li("The five-dimension quality index -- needs signal from Duplicates, Completeness and Validity together."),
            ]
        ),
    )

    return html.Div([kpi_strip, volume_panel, scorecard_panel, deferred_panel])


_COMPARE_FIELDS = [
    ("given_name", "Given name"), ("family_name", "Family name"), ("gender", "Gender"),
    ("birthdate", "Birthdate"), ("identifier", "Identifier"), ("village", "Village"),
    ("ta", "TA"), ("home_district", "Home district"), ("cell", "Phone"),
    ("facility", "Facility"), ("encounter_count", "Encounters"),
    ("first_encounter", "First encounter"), ("last_encounter", "Last encounter"),
]


def _format_cell(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return str(value)


def _comparison_table(members_df):
    header = html.Tr([html.Th("Field")] + [html.Th(f"person_id {pid}") for pid in members_df["person_id"]])
    rows = []
    for col, label in _COMPARE_FIELDS:
        values = members_df[col].tolist()
        differs = len({_format_cell(v) for v in values}) > 1
        cells = [html.Td(label, className="dq-diff-field")]
        for v in values:
            cell_class = "dq-diff-cell dq-diff-cell-mismatch" if differs else "dq-diff-cell"
            cells.append(html.Td(_format_cell(v), className=cell_class))
        rows.append(html.Tr(cells))
    return html.Table([html.Thead(header), html.Tbody(rows)], className="dq-compare-table")


def _person_attributes_cell(best):
    fields = [
        ("Identifier", best.get("identifier")),
        ("Given Name", best.get("given_name")),
        ("Last Name", best.get("family_name")),
        ("Gender", best.get("gender")),
        ("Birthdate", best.get("birthdate")),
    ]
    return html.Div([html.Div(f"{label}: {_format_cell(value)}") for label, value in fields])


def _candidate_row(sn, group, roster_df):
    """One 'Candidate groups' table row -- SN, the representative person's
    attributes (most encounters, ties broken by obs_rows), an expandable
    'Similar Records' cell (native <details>/<summary>, same side-by-side
    comparison as before, just moved out of the summary line), Match score,
    and Rules."""
    members_df = roster_df[roster_df["person_id"].isin(group["members"])].copy()
    best = members_df.sort_values(["encounter_count", "obs_rows"], ascending=False).iloc[0]
    keep_line = html.Div(
        f"A merge would keep person_id {best['person_id']} ({int(best['encounter_count'])} encounters) "
        f"and retire {len(members_df) - 1} record(s). Read-only -- no merge is performed.",
        className="dq-panel-note",
    )
    n = len(group["members"])
    similar_records = html.Details(
        [
            html.Summary(f"{n} Record{'s' if n != 1 else ''}", className="dq-group-summary"),
            _comparison_table(members_df),
            keep_line,
        ],
        className="dq-group-details",
    )
    return html.Tr([
        html.Td(str(sn)),
        html.Td(_person_attributes_cell(best)),
        html.Td(similar_records),
        html.Td(f"{group['confidence']:.2f}"),
        html.Td(", ".join(group["rules"]) or "none"),
    ])


@callback(
    Output("dq-dup-other-fields-group", "style"),
    Input("dq-dup-rules", "value"),
)
def toggle_dup_other_fields(enabled_rules):
    base_style = {"marginTop": "12px"}
    if "D4" not in (enabled_rules or []):
        base_style["display"] = "none"
    return base_style


@callback(
    Output("dq-dup-candidates-page", "data"),
    Input("dq-run-btn", "n_clicks"),
)
def reset_candidates_page(_n_clicks):
    """A fresh "Run DQ" invalidates whatever page the user was on -- e.g. a
    stale page 5 could land past the end of a now-shorter candidate list."""
    return 1


@callback(
    Output("dq-dup-candidates-page", "data", allow_duplicate=True),
    Input("dq-candidates-prev-btn", "n_clicks"),
    State("dq-dup-candidates-page", "data"),
    prevent_initial_call=True,
)
def candidates_prev_page(_n_clicks, page):
    return max(1, (page or 1) - 1)


@callback(
    Output("dq-dup-candidates-page", "data", allow_duplicate=True),
    Input("dq-candidates-next-btn", "n_clicks"),
    State("dq-dup-candidates-page", "data"),
    prevent_initial_call=True,
)
def candidates_next_page(_n_clicks, page):
    return (page or 1) + 1


@callback(
    Output("dq-duplicates-content", "children"),
    Output("dq-tab-duplicates", "label"),
    Output("dq-tab-duplicates", "style"),
    Input("url-params-store", "data"),
    Input("dq-run-btn", "n_clicks"),
    Input("dq-dup-candidates-page", "data"),
    State("dq-date-range", "start_date"),
    State("dq-date-range", "end_date"),
    State("dq-scope", "value"),
    State("dq-facility-filter", "value"),
    State("dq-program-filter", "value"),
    State("dq-dup-rules", "value"),
    State("dq-dup-other-fields", "value"),
    State("dq-dup-min-confidence", "value"),
)
def render_duplicates_tab(urlparams, run_clicks, candidates_page, start_date, end_date, districts, facilities,
                           program, enabled_rules, other_fields, min_confidence):
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]

    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None, "Duplicates", _TAB_STYLE

    if not run_clicks:
        return (
            _empty_state(
                "Click \"Run DQ\" to see results",
                "Set your filters above, then click Run DQ to compute this tab.",
            ),
            "Duplicates", _TAB_STYLE,
        )

    if not _data_source_available(data_route):
        return _data_source_health_banner(data_route, "Duplicates")

    if not program:
        return (
            _empty_state(
                "Select a programme",
                "Duplicate matching is scoped to one programme's patient roster at a time.",
            ),
            "Duplicates", _TAB_STYLE,
        )

    data_path = f"data/{data_route}/parquet"
    level = scope.get("level")
    user_districts = scope.get("districts") or []
    if isinstance(user_districts, str):
        user_districts = [user_districts]

    where = _selection_where(level, location, user_districts, districts, facilities, program, start_date, end_date)
    # Identifier integrity is checked across the whole scoped extract, not
    # just the selected programme -- same scope, no Program filter.
    scope_where = _selection_where(level, location, user_districts, districts, facilities, None, start_date, end_date)

    try:
        roster_df = DataStorage.query_duckdb(
            f"SELECT {PERSON_ID_} AS person_id, "
            f"MAX({LAST_NAME_}) AS family_name, MAX({FIRST_NAME_}) AS given_name, "
            f"MAX({GENDER_}) AS gender, MAX({BIRTHDATE_}) AS birthdate, "
            f"MAX({IDENTIFIER_}) AS identifier, MAX({VILLAGE_}) AS village, "
            f"MAX({TA_}) AS ta, MAX({HOME_DISTRICT_}) AS home_district, MAX({CELL_}) AS cell, "
            f"MAX({FACILITY_}) AS facility, COUNT(DISTINCT {ENCOUNTER_ID_}) AS encounter_count, "
            f"COUNT(DISTINCT ({PERSON_ID_}, {CONCEPT_NAME_})) AS obs_rows, "
            f"MIN({DATE_}) AS first_encounter, MAX({DATE_}) AS last_encounter "
            f"FROM '{data_path}' WHERE {where} GROUP BY {PERSON_ID_}"
        )
    except Exception:
        roster_df = pd.DataFrame()

    if roster_df.empty:
        return (
            _empty_state(
                "No records match the current filters",
                "Try widening the date range, clearing the facility filter, or choosing a different programme.",
            ),
            "Duplicates", _TAB_STYLE,
        )

    enabled_rules = enabled_rules or []
    groups, per_rule_counts = match_duplicates(roster_df, enabled_rules, other_fields=other_fields)

    patient_records = len(roster_df)
    surplus = sum(len(g["members"]) - 1 for g in groups)
    distinct_identities = patient_records - surplus
    records_involved = sum(len(g["members"]) for g in groups)
    duplicate_rate = (surplus / patient_records * 100) if patient_records else 0.0

    critical_groups = sum(
        1 for g in groups if any(DUP_RULES[r]["severity"] == "critical" for r in g["rules"])
    )

    prev_start, prev_end = _previous_period(start_date, end_date)
    prev_duplicate_rate = None
    if prev_start and enabled_rules:
        prev_where = _selection_where(level, location, user_districts, districts, facilities, program, prev_start, prev_end)
        try:
            prev_roster_df = DataStorage.query_duckdb(
                f"SELECT {PERSON_ID_} AS person_id, "
                f"MAX({LAST_NAME_}) AS family_name, MAX({FIRST_NAME_}) AS given_name, "
                f"MAX({GENDER_}) AS gender, MAX({BIRTHDATE_}) AS birthdate, "
                f"MAX({IDENTIFIER_}) AS identifier, MAX({VILLAGE_}) AS village, "
                f"MAX({TA_}) AS ta, MAX({HOME_DISTRICT_}) AS home_district, MAX({CELL_}) AS cell, "
                f"MAX({FACILITY_}) AS facility, COUNT(DISTINCT {ENCOUNTER_ID_}) AS encounter_count, "
                f"COUNT(DISTINCT ({PERSON_ID_}, {CONCEPT_NAME_})) AS obs_rows, "
                f"MIN({DATE_}) AS first_encounter, MAX({DATE_}) AS last_encounter "
                f"FROM '{data_path}' WHERE {prev_where} GROUP BY {PERSON_ID_}"
            )
            if not prev_roster_df.empty:
                prev_groups, _ = match_duplicates(prev_roster_df, enabled_rules, other_fields=other_fields)
                prev_surplus = sum(len(g["members"]) - 1 for g in prev_groups)
                prev_duplicate_rate = prev_surplus / len(prev_roster_df) * 100
        except Exception:
            prev_duplicate_rate = None

    summary_strip = html.Div(
        [
            _kpi_card("Patient records", f"{patient_records:,}"),
            _kpi_card("Duplicate Groups", f"{len(groups):,}"),
            _kpi_card("Critical severity", f"{critical_groups:,}"),
            _kpi_card("Distinct Patients if Merged", f"{distinct_identities:,}"),
            _kpi_card("Records Affected", f"{records_involved:,}"),
            _kpi_card(
                "Duplicate rate", f"{duplicate_rate:.1f}%",
                sub=_trend_sub(duplicate_rate, prev_duplicate_rate),
                rag=_rag_for_rate(duplicate_rate, good=2.0, warn=8.0, higher_is_better=False),
            ),
        ],
        className="dq-kpi-row",
    )

    per_rule_table = dash_table.DataTable(
        columns=[
            {"name": "Rule", "id": "rule"}, {"name": "Key", "id": "key"},
            {"name": "Match score", "id": "confidence"}, {"name": "Severity", "id": "severity"},
            {"name": "Groups", "id": "groups"}, {"name": "Records", "id": "records"},
        ],
        data=[
            {
                "rule": f"{rid} — {DUP_RULES[rid]['label']}",
                "key": _rule_fields_display(rid, other_fields),
                "confidence": f"{DUP_RULES[rid]['confidence']:.2f}",
                "severity": DUP_RULES[rid]["severity"],
                "groups": per_rule_counts[rid]["groups"],
                "records": per_rule_counts[rid]["records"],
            }
            for rid in DUP_RULE_ORDER
        ],
        page_size=6,
    )

    if records_involved:
        involved_ids = {pid for g in groups for pid in g["members"]}
        by_facility = (
            roster_df[roster_df["person_id"].isin(involved_ids)]
            .groupby("facility")["person_id"].nunique()
            .reset_index(name="records_involved")
            .sort_values("records_involved", ascending=False)
        )
        facility_table = dash_table.DataTable(
            columns=[{"name": "Facility", "id": "facility"}, {"name": "Records involved", "id": "records_involved"}],
            data=by_facility.to_dict("records"),
            page_size=10, sort_action="native",
        )
    else:
        facility_table = html.Div("No duplicate records to break down by facility.", className="dq-empty-state")

    try:
        ident_shared = DataStorage.query_duckdb(
            f"SELECT COUNT(*) AS n FROM (SELECT {IDENTIFIER_} FROM '{data_path}' "
            f"WHERE {scope_where} AND {_presence_expr(IDENTIFIER_)} "
            f"GROUP BY {IDENTIFIER_} HAVING COUNT(DISTINCT {PERSON_ID_}) > 1)"
        )["n"][0]
        ident_multi = DataStorage.query_duckdb(
            f"SELECT COUNT(*) AS n FROM (SELECT {PERSON_ID_} FROM '{data_path}' "
            f"WHERE {scope_where} AND {_presence_expr(IDENTIFIER_)} "
            f"GROUP BY {PERSON_ID_} HAVING COUNT(DISTINCT {IDENTIFIER_}) > 1)"
        )["n"][0]
        multi_program = DataStorage.query_duckdb(
            f"SELECT COUNT(*) AS n FROM (SELECT {PERSON_ID_} FROM '{data_path}' "
            f"WHERE {scope_where} GROUP BY {PERSON_ID_} HAVING COUNT(DISTINCT {PROGRAM_}) > 1)"
        )["n"][0]
    except Exception:
        ident_shared = ident_multi = multi_program = 0

    identity_panel = html.Div(
        [
            html.H4("Identifier integrity", className="dq-panel-title"),
            html.Div(
                "Checked across the whole scoped extract, not just the selected programme.",
                className="dq-panel-note",
            ),
            html.Div(
                [
                    _kpi_card("Identifiers shared by >1 person_id", f"{int(ident_shared):,}"),
                    _kpi_card("person_ids with >1 identifier", f"{int(ident_multi):,}"),
                    _kpi_card("Persons enrolled in >1 programme", f"{int(multi_program):,}"),
                ],
                className="dq-kpi-row",
            ),
        ],
        className="dq-panel",
    )

    min_confidence = min_confidence or 0
    candidate_groups = [g for g in groups if g["confidence"] >= min_confidence]
    candidates_pagination = None
    if not enabled_rules:
        candidates_panel = _empty_state(
            "No matching rule is switched on",
            "Turn on at least one rule above to compute duplicate groups.",
        )
    elif not candidate_groups:
        candidates_panel = _empty_state(
            "No candidate groups at this match score",
            "Lower the minimum match score slider, or switch on more rules.",
        )
    else:
        total_pages = math.ceil(len(candidate_groups) / _CANDIDATES_PAGE_SIZE)
        page = max(1, min(candidates_page or 1, total_pages))
        start = (page - 1) * _CANDIDATES_PAGE_SIZE
        page_groups = candidate_groups[start:start + _CANDIDATES_PAGE_SIZE]

        candidates_panel = html.Table(
            [
                html.Thead(html.Tr([
                    html.Th("SN"), html.Th("Person Attributes"), html.Th("Similar Records"),
                    html.Th("Match score"), html.Th("Rules"),
                ])),
                html.Tbody([
                    _candidate_row(sn, g, roster_df)
                    for sn, g in enumerate(page_groups, start=start + 1)
                ]),
            ],
            className="dq-candidates-table",
        )
        candidates_pagination = html.Div(
            [
                html.Button("Previous", id="dq-candidates-prev-btn", n_clicks=0,
                            disabled=page <= 1, className="dq-pagination-btn"),
                html.Span(f"Page {page} of {total_pages} ({len(candidate_groups):,} candidate groups)",
                          className="dq-pagination-info"),
                html.Button("Next", id="dq-candidates-next-btn", n_clicks=0,
                            disabled=page >= total_pages, className="dq-pagination-btn"),
            ],
            className="dq-pagination-row",
        )

    content = html.Div(
        [
            summary_strip,
            html.Div(
                [
                    html.H4("Per-rule breakdown", className="dq-panel-title"),
                    html.Div(
                        "Shows how many groups/records each rule alone would catch. A patient matched by more "
                        "than one rule is only counted once in Duplicate Groups/Patient records above -- these "
                        "per-rule counts aren't meant to sum to that total.",
                        className="dq-panel-note",
                    ),
                    per_rule_table,
                ],
                className="dq-panel",
            ),
            html.Div(
                [html.H4("Duplicates by facility", className="dq-panel-title"), facility_table],
                className="dq-panel",
            ),
            html.Div(
                [
                    html.H4("Candidate groups", className="dq-panel-title"),
                    html.Div(
                        "Read-only. Person Attributes reflects the record with the most "
                        "encounters (ties broken by observation rows); expand Similar Records "
                        "for a field-by-field comparison, where differing fields are highlighted.",
                        className="dq-panel-note",
                    ),
                    candidates_panel,
                    candidates_pagination,
                ],
                className="dq-panel",
            ),
            identity_panel,
        ]
    )
    label, style = _tab_label_and_style("Duplicates", len(candidate_groups))
    return content, label, style


@callback(
    Output("dq-completeness-encounters", "options"),
    Input("dq-program-filter", "value"),
    Input("url-params-store", "data"),
)
def load_completeness_encounter_options(program, urlparams):
    if not program:
        return []
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    data_path = f"data/{data_route}/parquet"
    safe_program = str(program).replace("'", "''")
    try:
        enc_df = DataStorage.query_duckdb(
            f"SELECT DISTINCT {ENCOUNTER_} FROM '{data_path}' "
            f"WHERE {PROGRAM_} = '{safe_program}' "
            f"AND {DATE_} >= (SELECT MAX({DATE_}) FROM '{data_path}') - INTERVAL 7 DAY "
            f"ORDER BY {ENCOUNTER_}"
        )
        encounters = enc_df[ENCOUNTER_].dropna().tolist()
    except Exception:
        encounters = []
    return [{"label": e, "value": e} for e in encounters]


@callback(
    Output("dq-completeness-demographics", "value"),
    Output("dq-completeness-encounters", "value"),
    Input("url-params-store", "data"),
    Input("dq-program-filter", "value"),
)
def load_dq_preferences(urlparams, program):
    """Restores this uuid's last saved Completeness rules for the currently
    selected programme -- encounters are mapped per-programme, so switching
    programmes reloads that programme's own mandatory encounter list. A uuid
    with no saved entry at all starts fully blank (not defaulted to every
    demographic field) so a first-time user doesn't see phantom "issues" on
    the Completeness tab before they've configured anything."""
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    uuid = (urlparams.get("uuid") or [None])[0]
    prefs = _load_dq_prefs(data_route, uuid, program)
    if prefs is None:
        return [], []
    return prefs["demographics"], prefs["encounters"]


@callback(
    Output("dq-prefs-save-status", "children"),
    Input("dq-completeness-demographics", "value"),
    Input("dq-completeness-encounters", "value"),
    State("url-params-store", "data"),
    State("dq-program-filter", "value"),
    prevent_initial_call=True,
)
def save_dq_preferences(demographics, encounters, urlparams, program):
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    uuid = (urlparams.get("uuid") or [None])[0]
    _save_dq_prefs(data_route, uuid, program, encounters, demographics)
    return ""


@callback(
    Output("dq-completeness-content", "children"),
    Output("dq-tab-completeness", "label"),
    Output("dq-tab-completeness", "style"),
    Input("url-params-store", "data"),
    Input("dq-run-btn", "n_clicks"),
    State("dq-date-range", "start_date"),
    State("dq-date-range", "end_date"),
    State("dq-scope", "value"),
    State("dq-facility-filter", "value"),
    State("dq-program-filter", "value"),
    State("dq-completeness-demographics", "value"),
    State("dq-completeness-encounters", "value"),
)
def render_completeness_tab(urlparams, run_clicks, start_date, end_date, districts, facilities, program,
                             mandatory_demographics, mandatory_encounters):
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]
    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None, "Completeness", _TAB_STYLE

    if not run_clicks:
        return (
            _empty_state(
                "Click \"Run DQ\" to see results",
                "Set your filters above, then click Run DQ to compute this tab.",
            ),
            "Completeness", _TAB_STYLE,
        )

    if not _data_source_available(data_route):
        return _data_source_health_banner(data_route, "Completeness")

    if not program:
        return (
            _empty_state(
                "Select a programme",
                "Completeness rules are evaluated against one programme's patient roster at a time.",
            ),
            "Completeness", _TAB_STYLE,
        )

    mandatory_demographics = mandatory_demographics or []
    mandatory_encounters = mandatory_encounters or []
    if not mandatory_demographics and not mandatory_encounters:
        return (
            _empty_state(
                "No completeness rule is set",
                "Pick at least one mandatory demographic field or encounter above.",
            ),
            "Completeness", _TAB_STYLE,
        )

    data_path = f"data/{data_route}/parquet"
    level = scope.get("level")
    user_districts = scope.get("districts") or []
    if isinstance(user_districts, str):
        user_districts = [user_districts]

    where = _selection_where(level, location, user_districts, districts, facilities, program, start_date, end_date)

    df, demo_flag_cols, enc_flag_cols = _evaluate_completeness(
        data_path, where, mandatory_demographics, mandatory_encounters
    )

    if df.empty:
        return (
            _empty_state(
                "No records match the current filters",
                "Try widening the date range, clearing the facility filter, or choosing a different programme.",
            ),
            "Completeness", _TAB_STYLE,
        )

    demo_label_map = dict(DEMOGRAPHIC_FIELD_OPTIONS)

    total_patients = len(df)
    complete_count = int(df["is_complete"].sum())
    incomplete_count = total_patients - complete_count
    completeness_rate = (complete_count / total_patients * 100) if total_patients else 0.0
    demo_rate = (df["demographics_complete"].mean() * 100) if demo_flag_cols else None
    enc_rate = (df["encounters_complete"].mean() * 100) if enc_flag_cols else None

    prev_start, prev_end = _previous_period(start_date, end_date)
    prev_completeness_rate = prev_demo_rate = prev_enc_rate = None
    if prev_start:
        prev_where = _selection_where(level, location, user_districts, districts, facilities, program, prev_start, prev_end)
        try:
            prev_df, _, _ = _evaluate_completeness(data_path, prev_where, mandatory_demographics, mandatory_encounters)
            if not prev_df.empty:
                prev_completeness_rate = prev_df["is_complete"].mean() * 100
                if demo_flag_cols:
                    prev_demo_rate = prev_df["demographics_complete"].mean() * 100
                if enc_flag_cols:
                    prev_enc_rate = prev_df["encounters_complete"].mean() * 100
        except Exception:
            pass

    # Reported separately, not just blended into the one combined rate --
    # a patient missing only a demographic field and one missing only an
    # encounter are different problems with different fixes, and burying
    # both inside one "incomplete" number hides which one actually needs
    # attention. Only shown for the dimension that was actually configured;
    # the unconfigured side is a no-op True for every patient and would
    # misleadingly read as "100% complete" otherwise.
    metrics_cards = [
        _kpi_card("Patients evaluated", f"{total_patients:,}"),
        _kpi_card("Complete records", f"{complete_count:,}"),
        _kpi_card("Incomplete records", f"{incomplete_count:,}"),
        _kpi_card(
            "Completeness rate", f"{completeness_rate:.1f}%",
            sub=_trend_sub(completeness_rate, prev_completeness_rate),
            rag=_rag_for_rate(completeness_rate),
        ),
    ]
    if demo_flag_cols:
        metrics_cards.append(_kpi_card(
            "Demographic completeness", f"{demo_rate:.1f}%",
            sub=_trend_sub(demo_rate, prev_demo_rate), rag=_rag_for_rate(demo_rate),
        ))
    if enc_flag_cols:
        metrics_cards.append(_kpi_card(
            "Encounter completeness", f"{enc_rate:.1f}%",
            sub=_trend_sub(enc_rate, prev_enc_rate), rag=_rag_for_rate(enc_rate),
        ))

    metrics_strip = html.Div(metrics_cards, className="dq-kpi-row")

    applicability_note = html.Div(
        "Every selected encounter is currently checked against every patient in scope, regardless of "
        "whether that encounter clinically applies to them -- e.g. marking an encounter mandatory for a "
        "whole programme when it's only expected for a subset of patients will overcount incomplete "
        "records. There's no per-patient applicability rule yet; read a low rate here as a prompt to "
        "check whether the selected rule genuinely applies to everyone in this scope, not as a confirmed "
        "defect count.",
        className="dq-panel-note",
    )

    facility_summary = (
        df.groupby("facility")
        .agg(Patients=("person_id", "count"), Complete=("is_complete", "sum"))
        .reset_index()
        .rename(columns={"facility": "Facility"})
    )
    facility_summary["Incomplete"] = facility_summary["Patients"] - facility_summary["Complete"]
    facility_summary["Completeness %"] = (facility_summary["Complete"] / facility_summary["Patients"] * 100).round(1)
    facility_summary = facility_summary.sort_values("Completeness %")

    facility_table = dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in ["Facility", "Patients", "Complete", "Incomplete", "Completeness %"]],
        data=facility_summary.to_dict("records"),
        page_size=15,
        sort_action="native",
    )

    def _missing(row, cols, flags, label_fn):
        missing = [label_fn(c) for c, f in zip(cols, flags) if row[f] == 0]
        return ", ".join(missing) if missing else "—"

    incomplete_df = df[~df["is_complete"]].copy()
    if not incomplete_df.empty:
        incomplete_df["Missing Demographics"] = incomplete_df.apply(
            lambda r: _missing(r, mandatory_demographics, demo_flag_cols, lambda c: demo_label_map.get(c, c)), axis=1
        )
        incomplete_df["Missing Encounters"] = incomplete_df.apply(
            lambda r: _missing(r, mandatory_encounters, enc_flag_cols, lambda e: e), axis=1
        )
        incomplete_df = incomplete_df.rename(columns={
            "identifier": "Identifier", "facility": "Facility",
            "given_name": "Given Name", "family_name": "Family Name",
        })
        display_cols = ["Facility","Identifier", "Given Name", "Family Name", "Missing Demographics", "Missing Encounters"]
        incomplete_table = dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in display_cols],
            data=incomplete_df[display_cols].to_dict("records"),
            page_size=15,
            sort_action="native",
        )
    else:
        incomplete_table = html.Div("No incomplete patient records at this scope.", className="dq-empty-state")

    content = html.Div(
        [
            metrics_strip,
            applicability_note,
            html.Div(
                [html.H4("Completeness by facility", className="dq-panel-title"), facility_table],
                className="dq-panel",
            ),
            html.Div(
                [
                    html.H4("Incomplete patient records", className="dq-panel-title"),
                    html.Div(
                        "Patients missing at least one mandatory demographic field or encounter.",
                        className="dq-panel-note",
                    ),
                    incomplete_table,
                ],
                className="dq-panel",
            ),
        ]
    )
    label, style = _tab_label_and_style("Completeness", incomplete_count)
    return content, label, style


_VALIDITY_SAMPLE_COLUMNS = [
    ("Rule", "Rule"), ("Severity", "Severity"), ("Person ID", "Person ID"), ("Facility", "Facility"),
    ("Concept", "Concept"), ("Drug", "Drug"), ("Units", "Units"), ("Value", "Value"), ("Obs Date", "Obs Date"),
    ("Birthdate", "Birthdate"), ("Gender", "Gender"), ("Programme", "Programme"),
]


@callback(
    Output("dq-validity-content", "children"),
    Output("dq-tab-validity", "label"),
    Output("dq-tab-validity", "style"),
    Input("url-params-store", "data"),
    Input("dq-run-btn", "n_clicks"),
    State("dq-date-range", "start_date"),
    State("dq-date-range", "end_date"),
    State("dq-scope", "value"),
    State("dq-facility-filter", "value"),
    State("dq-program-filter", "value"),
    State("dq-val-rules", "value"),
)
def render_validity_tab(urlparams, run_clicks, start_date, end_date, districts, facilities, program, enabled_rules):
    """Accuracy and consistency checks pushed down as SQL predicates against
    the raw extract -- unlike Duplicates/Completeness, not gated behind
    picking one programme, since an implausible birthdate or a vital sign
    outside a physiological range is a defect regardless of which
    programme recorded it.
    """
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]
    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None, "Validity and outliers", _TAB_STYLE

    if not run_clicks:
        return (
            _empty_state(
                "Click \"Run DQ\" to see results",
                "Set your filters above, then click Run DQ to compute this tab.",
            ),
            "Validity and outliers", _TAB_STYLE,
        )

    if not _data_source_available(data_route):
        return _data_source_health_banner(data_route, "Validity and outliers")

    data_path = f"data/{data_route}/parquet"
    level = scope.get("level")
    user_districts = scope.get("districts") or []
    if isinstance(user_districts, str):
        user_districts = [user_districts]

    where = _selection_where(level, location, user_districts, districts, facilities, program, start_date, end_date)
    predicates = validity_rule_predicates()
    enabled_rules = enabled_rules or []

    try:
        total_df = DataStorage.query_duckdb(f"SELECT COUNT(*) AS n FROM '{data_path}' WHERE {where}")
        total_rows = int(total_df["n"][0])
    except Exception:
        total_rows = 0

    if total_rows == 0:
        return (
            _empty_state(
                "No records match the current filters",
                "Try widening the date range, clearing the facility filter, or choosing a different programme.",
            ),
            "Validity and outliers", _TAB_STYLE,
        )

    per_rule_counts = {}
    for rid in VAL_RULE_ORDER:
        try:
            df = DataStorage.query_duckdb(
                f"SELECT COUNT(*) AS n FROM '{data_path}' WHERE {where} AND ({predicates[rid]})"
            )
            per_rule_counts[rid] = int(df["n"][0])
        except Exception:
            per_rule_counts[rid] = 0

    if enabled_rules:
        combined = " OR ".join(f"({predicates[r]})" for r in enabled_rules)
        try:
            distinct_df = DataStorage.query_duckdb(
                f"SELECT COUNT(*) AS n FROM '{data_path}' WHERE {where} AND ({combined})"
            )
            distinct_defects = int(distinct_df["n"][0])
        except Exception:
            distinct_defects = 0
    else:
        distinct_defects = 0

    defect_rate = (distinct_defects / total_rows * 100) if total_rows else 0.0
    rules_triggered = sum(1 for r in enabled_rules if per_rule_counts.get(r, 0) > 0)
    critical_defects = sum(
        per_rule_counts.get(r, 0) for r in enabled_rules if VAL_RULES[r]["severity"] == "critical"
    )

    prev_start, prev_end = _previous_period(start_date, end_date)
    prev_defect_rate = None
    if prev_start and enabled_rules:
        prev_where = _selection_where(level, location, user_districts, districts, facilities, program, prev_start, prev_end)
        combined = " OR ".join(f"({predicates[r]})" for r in enabled_rules)
        try:
            prev_total = DataStorage.query_duckdb(f"SELECT COUNT(*) AS n FROM '{data_path}' WHERE {prev_where}")["n"][0]
            if prev_total:
                prev_defects = DataStorage.query_duckdb(
                    f"SELECT COUNT(*) AS n FROM '{data_path}' WHERE {prev_where} AND ({combined})"
                )["n"][0]
                prev_defect_rate = float(prev_defects) / float(prev_total) * 100
        except Exception:
            prev_defect_rate = None

    summary_strip = html.Div(
        [
            _kpi_card("Rows evaluated", f"{total_rows:,}"),
            _kpi_card("Defective rows", f"{distinct_defects:,}"),
            _kpi_card("Critical severity", f"{critical_defects:,}"),
            _kpi_card(
                "Defect rate", f"{defect_rate:.2f}%",
                sub=_trend_sub(defect_rate, prev_defect_rate),
                rag=_rag_for_rate(defect_rate, good=1.0, warn=5.0, higher_is_better=False),
            ),
            _kpi_card("Rules triggered", f"{rules_triggered}/{len(enabled_rules)}" if enabled_rules else "—"),
        ],
        className="dq-kpi-row",
    )

    per_rule_table = dash_table.DataTable(
        columns=[
            {"name": "Rule", "id": "rule"}, {"name": "Category", "id": "category"},
            {"name": "Severity", "id": "severity"},
            {"name": "Description", "id": "description"}, {"name": "Defective rows", "id": "count"},
        ],
        data=[
            {
                "rule": f"{rid} — {VAL_RULES[rid]['label']}",
                "category": VAL_RULES[rid]["category"],
                "severity": VAL_RULES[rid]["severity"],
                "description": VAL_RULES[rid]["description"],
                "count": per_rule_counts[rid],
            }
            for rid in VAL_RULE_ORDER
        ],
        page_size=6,
    )

    sample_df = pd.DataFrame(columns=[c for c, _ in _VALIDITY_SAMPLE_COLUMNS])
    if enabled_rules:
        union_parts = []
        for rid in enabled_rules:
            union_parts.append(
                f"(SELECT '{rid}' AS \"Rule\", {PERSON_ID_} AS \"Person ID\", {FACILITY_} AS \"Facility\", "
                f"{CONCEPT_NAME_} AS \"Concept\", {DRUG_NAME_} AS \"Drug\", DrugUnits AS \"Units\", "
                f"{VALUE_NUMERIC_} AS \"Value\", "
                f"CAST({OBS_DATETIME_} AS VARCHAR) AS \"Obs Date\", CAST({BIRTHDATE_} AS VARCHAR) AS \"Birthdate\", "
                f"{GENDER_} AS \"Gender\", {PROGRAM_} AS \"Programme\" "
                f"FROM '{data_path}' WHERE {where} AND ({predicates[rid]}) LIMIT 20)"
            )
        try:
            sample_df = DataStorage.query_duckdb(" UNION ALL ".join(union_parts))
            sample_df["Severity"] = sample_df["Rule"].map(lambda rid: VAL_RULES[rid]["severity"])
        except Exception:
            sample_df = pd.DataFrame(columns=[c for c, _ in _VALIDITY_SAMPLE_COLUMNS])

    sample_panel = html.Div(
        [
            html.H4("Sample defective rows", className="dq-panel-title"),
            html.Div(
                "Up to 20 example rows per enabled rule -- not every defective row, just enough to spot-check.",
                className="dq-panel-note",
            ),
            dash_table.DataTable(
                columns=[{"name": n, "id": c} for c, n in _VALIDITY_SAMPLE_COLUMNS],
                data=sample_df.to_dict("records"),
                page_size=15,
                sort_action="native",
            ) if not sample_df.empty else html.Div(
                "No defective rows for the enabled checks." if enabled_rules else "No checks are switched on above.",
                className="dq-empty-state",
            ),
        ],
        className="dq-panel",
    )

    content = html.Div([
        summary_strip,
        html.Div([html.H4("Per-rule breakdown", className="dq-panel-title"), per_rule_table], className="dq-panel"),
        sample_panel,
    ])
    label, style = _tab_label_and_style("Validity and outliers", distinct_defects)
    return content, label, style


@callback(
    Output("dq-users-content", "children"),
    Output("dq-tab-users", "label"),
    Output("dq-tab-users", "style"),
    Input("url-params-store", "data"),
    Input("dq-run-btn", "n_clicks"),
)
def render_users_tab(urlparams, run_clicks):
    """Audits this route's own user roster (not patient records) --
    orphaned district/facility scope, duplicate uuids, missing role, bad
    user_level, and a loud flag when users_data.csv is absent entirely,
    since that silently degrades every user not in user_properties.json to
    unauthorized. Independent of the date/scope/facility/programme filter
    bar above -- none of that applies to a user roster.
    """
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]
    user_data = _load_user_registry(data_route)
    user_row, _scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None, "Users", _TAB_STYLE

    if not run_clicks:
        return (
            _empty_state(
                "Click \"Run DQ\" to see results",
                "Set your filters above, then click Run DQ to compute this tab.",
            ),
            "Users", _TAB_STYLE,
        )

    data_path = f"data/{data_route}/parquet"
    roster_df, users_csv_present = load_user_roster(data_route)

    source_available = _data_source_available(data_route)
    if source_available:
        try:
            dist_df = DataStorage.query_duckdb(f"SELECT DISTINCT {DISTRICT_} FROM '{data_path}'")
            valid_districts = set(dist_df[DISTRICT_].dropna().tolist())
        except Exception:
            valid_districts = set()
        try:
            fac_df = DataStorage.query_duckdb(f"SELECT DISTINCT {FACILITY_CODE_} FROM '{data_path}'")
            valid_facility_codes = set(fac_df[FACILITY_CODE_].dropna().tolist())
        except Exception:
            valid_facility_codes = set()
        try:
            fd_df = DataStorage.query_duckdb(
                f"SELECT DISTINCT {FACILITY_CODE_}, {DISTRICT_} FROM '{data_path}' "
                f"WHERE {FACILITY_CODE_} IS NOT NULL AND {DISTRICT_} IS NOT NULL"
            )
            facility_district_map = dict(zip(fd_df[FACILITY_CODE_], fd_df[DISTRICT_]))
        except Exception:
            facility_district_map = {}
    else:
        # No raw extract to check scope against -- None (not an empty set)
        # tells evaluate_users to skip U1/U2/U7 rather than flag every
        # assigned district/facility as orphaned against nothing.
        valid_districts = None
        valid_facility_codes = None
        facility_district_map = None

    evaluated = evaluate_users(roster_df, valid_districts, valid_facility_codes, facility_district_map)

    alert = None
    if not source_available:
        alert = html.Div(
            f"data/{data_route}/parquet doesn't exist, so district/facility assignments can't be checked "
            "against real data on this route -- U1 (Orphaned district), U2 (Orphaned facility code) and U7 "
            "(Facility/district mismatch) are skipped below, not clean. Other checks (duplicate uuid, "
            "missing role, invalid level, scope not set, conflicting entries) don't depend on the raw "
            "extract and still ran normally.",
            className="dq-status-message",
        )
    elif not users_csv_present:
        alert = html.Div(
            f"data/{data_route}/single_tables/users_data.csv does not exist. Every user not explicitly "
            "listed in user_properties.json is unauthorized on this route -- this is a structural gap, "
            "not a per-user issue.",
            className="dq-status-message",
        )

    if evaluated.empty:
        return (
            html.Div([alert] if alert else [html.Div("No users found for this route.", className="dq-empty-state")]),
            "Users", _TAB_STYLE,
        )

    total_users = len(evaluated)
    flagged = evaluated[evaluated["issues"].apply(bool)]
    per_rule_counts = {rid: int(evaluated[rid].sum()) for rid in USER_RULE_ORDER}
    critical_users = sum(
        1 for _, row in flagged.iterrows()
        if any(USER_RULES[rid]["severity"] == "critical" for rid in row["issues"])
    )

    summary_strip = html.Div(
        [
            _kpi_card("Users in roster", f"{total_users:,}"),
            _kpi_card("Users with an issue", f"{len(flagged):,}"),
            _kpi_card("Critical severity", f"{critical_users:,}"),
            _kpi_card("users_data.csv", "Missing" if not users_csv_present else "Present"),
        ],
        className="dq-kpi-row",
    )

    per_rule_table = dash_table.DataTable(
        columns=[
            {"name": "Rule", "id": "rule"}, {"name": "Severity", "id": "severity"},
            {"name": "Description", "id": "description"},
            {"name": "Users flagged", "id": "count"},
        ],
        data=[
            {
                "rule": f"{rid} — {USER_RULES[rid]['label']}",
                "severity": USER_RULES[rid]["severity"],
                "description": USER_RULES[rid]["description"],
                "count": per_rule_counts[rid],
            }
            for rid in USER_RULE_ORDER
        ],
        page_size=6,
    )

    flagged_display = flagged.copy()
    flagged_display["Issues"] = flagged_display["issues"].apply(lambda ids: ", ".join(ids))
    def _join_name(r):
        parts = [str(v) for v in (r.get("given_name"), r.get("family_name")) if pd.notna(v) and str(v).strip()]
        return " ".join(parts) or "—"

    def _facility_label(r):
        for v in (r.get("facility_name"), r.get("facility_code")):
            if pd.notna(v) and str(v).strip():
                return str(v)
        return "—"

    flagged_display["Name"] = flagged_display.apply(_join_name, axis=1)
    flagged_display["Facility"] = flagged_display.apply(_facility_label, axis=1)
    _severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    flagged_display["Severity"] = flagged_display["issues"].apply(
        lambda ids: min((USER_RULES[rid]["severity"] for rid in ids), key=lambda s: _severity_rank[s])
    )
    display_cols = ["Name", "username", "role", "user_level", "district", "Facility", "Severity", "uuid", "Issues"]
    display_names = ["Name", "Username", "Role", "Level", "District", "Facility", "Severity", "UUID", "Issues"]

    flagged_table = html.Div([
        dash_table.DataTable(
            columns=[{"name": n, "id": c} for c, n in zip(display_cols, display_names)],
            data=flagged_display[display_cols].to_dict("records"),
            page_size=15,
            sort_action="native",
        ),
        html.Div(
            "Name is resolved from the user's own person_id where users_data.csv provides one; "
            "Facility is the name configured directly in user_properties.json where set, otherwise "
            "looked up from locations_data.csv (that file is known to carry stale location codes, so "
            "treat a looked-up facility name as a lead to verify, not a fact). UUID is kept for exact "
            "lookup, not as the primary way to identify someone.",
            className="dq-panel-note",
        ),
    ]) if not flagged.empty else html.Div("No flagged users on this route.", className="dq-empty-state")

    panels = []
    if alert:
        panels.append(alert)
    panels.extend([
        summary_strip,
        html.Div([html.H4("Per-rule breakdown", className="dq-panel-title"), per_rule_table], className="dq-panel"),
        html.Div(
            [
                html.H4("Flagged users", className="dq-panel-title"),
                flagged_table,
            ],
            className="dq-panel",
        ),
    ])

    label, style = _tab_label_and_style("Users", len(flagged))
    return html.Div(panels), label, style
