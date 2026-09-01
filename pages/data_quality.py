import dash
from dash import html, dcc, dash_table, Input, Output, State, callback
from dash.exceptions import PreventUpdate
import pandas as pd
import plotly.graph_objects as go

from data_storage import DataStorage
from config import (
    PROGRAM_, FACILITY_, DISTRICT_, DATE_, PERSON_ID_, ENCOUNTER_ID_, OBS_DATETIME_,
    CONCEPT_NAME_,
    IDENTIFIER_, FIRST_NAME_, LAST_NAME_, GENDER_, HOME_DISTRICT_, TA_, VILLAGE_,
    BIRTHDATE_, CELL_,
)
from pages.home import _resolve_user_scope, _scope_where_parts, _load_user_registry
from mnid.core.constants import BG, BORDER, TEXT
from dq.theme import BRAND, BRAND_TINT
import dq.theme  # noqa: F401 -- registers the "dq" Plotly template
from dq.checks.duplicates import (
    RULES as DUP_RULES, RULE_ORDER as DUP_RULE_ORDER, FIELD_OPTIONS as DUP_FIELD_OPTIONS,
    match_duplicates,
)

dash.register_page(__name__, path="/data_quality", title="Data Quality")

# Identity/Demographics core fields used for the Overview facility scorecard's
# field-completeness proxy. The Completeness tab lets the user pick any
# column set; this is a fixed, smaller stand-in so Overview has a real number
# today without duplicating that tab's column-picker logic.
CORE_FIELDS = [IDENTIFIER_, FIRST_NAME_, LAST_NAME_, GENDER_, HOME_DISTRICT_, TA_, VILLAGE_]

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


def _kpi_card(label, value, sub=None):
    children = [
        html.Div(label, className="dq-kpi-label"),
        html.Div(value, className="dq-kpi-value"),
    ]
    if sub:
        children.append(html.Div(sub, className="dq-kpi-sub"))
    return html.Div(children, className="dq-kpi-card")


def _empty_state(title, body):
    return html.Div(
        [html.Div(title, className="dq-empty-state-title"), html.Div(body)],
        className="dq-empty-state",
    )


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
                    ],
                ),
                # html.Div(
                #     className="dq-header-action-col",
                #     children=[
                #         html.Button("Run DQ", id="dq-run-btn", n_clicks=0, className="dq-run-btn"),
                #     ],
                # ),
            ],
        ),
        html.Div(id="dq-alert"),
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
                            label="Duplicates", value="duplicates",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(
                                className="results-card",
                                children=[
                                    html.Div(
                                        className="dq-panel",
                                        children=[
                                            html.H4("Matching rules", className="dq-panel-title"),
                                            dcc.Checklist(
                                                id="dq-dup-rules",
                                                options=[
                                                    {
                                                        "label": f"{rid} — {DUP_RULES[rid]['label']} "
                                                                 f"({DUP_RULES[rid]['confidence']:.2f})",
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
                                                "Minimum confidence to show",
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
                                    html.Div(id="dq-duplicates-content"),
                                ],
                            ),
                        ),
                        dcc.Tab(
                            label="Completeness", value="completeness",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(id="dq-completeness-content", className="card-2"),
                        ),
                        dcc.Tab(
                            label="Validity and outliers", value="validity",
                            style=_TAB_STYLE, selected_style=_TAB_SELECTED_STYLE,
                            children=html.Div(id="dq-validity-content", className="card-2"),
                        ),
                    ],
                ),
            ],
        ),
    ],
)


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
        # Already ceilinged to a single facility -- Scope is disabled and
        # dq-facility-filter is already fixed by initialize_data_quality_filters.
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
    Input("dq-date-range", "start_date"),
    Input("dq-date-range", "end_date"),
    Input("dq-scope", "value"),
    Input("dq-facility-filter", "value"),
    Input("dq-program-filter", "value"),
)
def render_overview_tab(urlparams, start_date, end_date, districts, facilities, program):
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]

    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None

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
            _kpi_card("Patients with complete data", "—", "Needs a completeness definition"),
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
        fac_df["Field completeness %"] = "-"

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
                "Field completeness and % of Patients Completing Workflow are placeholders "
                "pending a definition; Duplicate groups matches this facility's own roster "
                "the same way the Duplicates tab does.",
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


def _group_details(group, roster_df):
    members_df = roster_df[roster_df["person_id"].isin(group["members"])].copy()
    best = members_df.loc[members_df["encounter_count"].idxmax()]
    summary = (
        f"Confidence {group['confidence']:.2f} · {len(group['members'])} records · "
        f"rules {', '.join(group['rules']) or 'none'}"
    )
    keep_line = html.Div(
        f"A merge would keep person_id {best['person_id']} ({int(best['encounter_count'])} encounters) "
        f"and retire {len(members_df) - 1} record(s). Read-only -- no merge is performed.",
        className="dq-panel-note",
    )
    return html.Details(
        [html.Summary(summary, className="dq-group-summary"), _comparison_table(members_df), keep_line],
        className="dq-group-details",
    )


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
    Output("dq-duplicates-content", "children"),
    Input("url-params-store", "data"),
    Input("dq-date-range", "start_date"),
    Input("dq-date-range", "end_date"),
    Input("dq-scope", "value"),
    Input("dq-facility-filter", "value"),
    Input("dq-program-filter", "value"),
    Input("dq-dup-rules", "value"),
    Input("dq-dup-other-fields", "value"),
    Input("dq-dup-min-confidence", "value"),
)
def render_duplicates_tab(urlparams, start_date, end_date, districts, facilities, program, enabled_rules, other_fields, min_confidence):
    urlparams = urlparams or {}
    data_route = urlparams.get("route", ["default"])[0]
    location = (urlparams.get("Location") or urlparams.get("?Location") or [None])[0]

    user_data = _load_user_registry(data_route)
    user_row, scope = _resolve_user_scope(urlparams, user_data)
    if user_row is None or not location:
        return None

    if not program:
        return _empty_state(
            "Select a programme",
            "Duplicate matching is scoped to one programme's patient roster at a time.",
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
            f"MIN({DATE_}) AS first_encounter, MAX({DATE_}) AS last_encounter "
            f"FROM '{data_path}' WHERE {where} GROUP BY {PERSON_ID_}"
        )
    except Exception:
        roster_df = pd.DataFrame()

    if roster_df.empty:
        return _empty_state(
            "No records match the current filters",
            "Try widening the date range, clearing the facility filter, or choosing a different programme.",
        )

    enabled_rules = enabled_rules or []
    groups, per_rule_counts = match_duplicates(roster_df, enabled_rules, other_fields=other_fields)

    patient_records = len(roster_df)
    surplus = sum(len(g["members"]) - 1 for g in groups)
    distinct_identities = patient_records - surplus
    records_involved = sum(len(g["members"]) for g in groups)
    duplicate_rate = (surplus / patient_records * 100) if patient_records else 0.0

    summary_strip = html.Div(
        [
            _kpi_card("Patient records", f"{patient_records:,}"),
            _kpi_card("Duplicate Groups", f"{len(groups):,}"),
            _kpi_card("Distinct Patients if Merged", f"{distinct_identities:,}"),
            _kpi_card("Records Affected", f"{records_involved:,}"),
            _kpi_card("Duplicate rate", f"{duplicate_rate:.1f}%", "surplus ÷ patient records"),
        ],
        className="dq-kpi-row",
    )

    per_rule_table = dash_table.DataTable(
        columns=[
            {"name": "Rule", "id": "rule"}, {"name": "Key", "id": "key"},
            {"name": "Confidence", "id": "confidence"},
            {"name": "Groups", "id": "groups"}, {"name": "Records", "id": "records"},
        ],
        data=[
            {
                "rule": f"{rid} — {DUP_RULES[rid]['label']}",
                "key": _rule_fields_display(rid, other_fields),
                "confidence": f"{DUP_RULES[rid]['confidence']:.2f}",
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
    if not enabled_rules:
        candidates_panel = _empty_state(
            "No matching rule is switched on",
            "Turn on at least one rule above to compute duplicate groups.",
        )
    elif not candidate_groups:
        candidates_panel = _empty_state(
            "No candidate groups at this confidence",
            "Lower the minimum-confidence slider, or switch on more rules.",
        )
    else:
        candidates_panel = html.Div(
            [_group_details(g, roster_df) for g in candidate_groups],
            className="dq-group-list",
        )

    return html.Div(
        [
            summary_strip,
            html.Div(
                [html.H4("Per-rule breakdown", className="dq-panel-title"), per_rule_table],
                className="dq-panel",
            ),
            html.Div(
                [html.H4("Duplicates by facility", className="dq-panel-title"), facility_table],
                className="dq-panel",
            ),
            identity_panel,
            html.Div(
                [
                    html.H4("Candidate groups", className="dq-panel-title"),
                    html.Div(
                        "Read-only. Each group expands into a field-by-field comparison; "
                        "differing fields are highlighted.",
                        className="dq-panel-note",
                    ),
                    candidates_panel,
                ],
                className="dq-panel",
            ),
        ]
    )
