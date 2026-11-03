"""Row-level validity/accuracy/consistency checks for the Data Quality
page's Validity and outliers tab.

Unlike Duplicates (person-level bucket matching over a pre-fetched roster),
each rule here flags an individual observation row directly, so every rule
is expressed as a SQL boolean predicate the page ANDs onto its own
scope/date/facility WHERE clause and pushes down to DuckDB.
"""
from config import (
    BIRTHDATE_, OBS_DATETIME_, GENDER_, PROGRAM_, VALUE_NUMERIC_, CONCEPT_NAME_,
    ENCOUNTER_ID_, VALUE_, OBS_VALUE_CODED_, VALUE_DATETIME_,
)

_DRUG_UNITS_COL = "DrugUnits"

# Units where a single dispensing event above this count is implausible for
# any regimen -- confirmed against the real extract: "tab(s)" has a median
# of 90 and a 75th percentile of 93, with one row at 30000 (clearly a
# mis-entry, not a real dispensing). Scoped to tablet-style units only,
# where the count is unambiguous -- other DrugUnits values (ml, mg, IU,
# "unit(s)") don't have a pharmacologically confident bound without a real
# per-drug dosing reference this system doesn't have locally.
IMPLAUSIBLE_TABLET_COUNT = 500
_TABLET_UNITS = ["tab(s)", "tablet", "tabs", "tab (s)"]

# (concept_name, plausible_min, plausible_max) -- a numeric observation for
# this concept outside its range is flagged as implausible. Bounds are
# generous physiological limits (not clinical targets), chosen so only
# genuinely impossible or mis-keyed values (a decimal-point slip, a stray
# 0) get caught, not borderline-but-real readings. Concept names and value
# volumes confirmed against the real August 2026 MAHIS extract.
VITAL_BOUNDS = [
    ("Weight", 0.5, 300),
    ("Weight (grams)", 300, 7000),
    ("Baby birthweight", 300, 7000),
    ("Height (cm)", 20, 250),
    ("Temperature", 25, 45),
    ("Pulse", 20, 250),
    ("Pulse Rate", 20, 250),
    ("Respiratory rate", 5, 100),
    ("Systolic", 40, 300),
    ("Diastolic", 20, 200),
    ("SAO2", 40, 100),
    ("Fetal heart rate", 60, 220),
    ("Fetal rate", 60, 220),
    ("MUAC", 5, 60),
]

# ANC/Labour/PNC are about the pregnant or postpartum person, so a Male
# patient under one of these is a genuine mismatch -- Neonatal is
# deliberately excluded, since a newborn patient can be either sex.
MATERNAL_PROGRAMS = ["ANC PROGRAM", "LABOUR AND DELIVERY PROGRAM", "PNC PROGRAM"]

RULES = {
    "V1": {
        "label": "Birthdate in the future",
        "category": "Accuracy",
        "severity": "high",
        "description": "Recorded date of birth is after today.",
    },
    "V2": {
        "label": "Implausible age (over 120 years)",
        "category": "Accuracy",
        "severity": "medium",
        "description": "Birthdate implies an age over 120 years.",
    },
    "V3": {
        "label": "Observation date in the future",
        "category": "Accuracy",
        "severity": "high",
        "description": "The observation's recorded date/time is after today.",
    },
    "V4": {
        "label": "Vital sign outside plausible range",
        "category": "Accuracy",
        "severity": "critical",
        "description": "A numeric vital sign (weight, height, temperature, pulse, blood "
                        "pressure, birthweight, etc.) falls outside what's physiologically possible.",
    },
    "V5": {
        "label": "Observation before birth",
        "category": "Consistency",
        "severity": "high",
        "description": "The observation's date/time falls before the person's own recorded birthdate.",
    },
    "V6": {
        "label": "Gender/programme mismatch",
        "severity": "medium",
        "category": "Consistency",
        "description": "A patient recorded as Male under a maternal programme (ANC, Labour "
                        "and Delivery, or PNC).",
    },
    "V13": {
        "label": "Implausible medication dosage",
        "category": "Accuracy",
        "severity": "critical",
        "description": "A tablet-based drug dispensing amount is over "
                        f"{IMPLAUSIBLE_TABLET_COUNT} tablets in one record -- no regimen "
                        "plausibly needs that many at once.",
    },
    "V14": {
        "label": "Multiple-birth completeness (DQ06)",
        "category": "Consistency",
        "severity": "high",
        "description": "Multiple gestation is recorded as Yes for a delivery encounter, but fewer "
                        "than two baby-outcome records exist for it.",
    },
    "V15": {
        "label": "Obstetric-history consistency (DQ13)",
        "category": "Consistency",
        "severity": "medium",
        "description": "Gravida or Parity is negative, or Parity exceeds Gravida, for the same "
                        "obstetric-history encounter.",
    },
    "V16": {
        "label": "Conditional referral completeness (DQ15)",
        "category": "Completeness",
        "severity": "high",
        "description": "A referral facility is recorded for an encounter with no accompanying "
                        "date of referral.",
    },
    "V17": {
        "label": "Birth/death outcome consistency (DQ16)",
        "category": "Consistency",
        "severity": "critical",
        "description": "For a singleton delivery, the per-baby birth outcome contradicts the "
                        "encounter's own Live Births/Stillbirths tally.",
    },
    "V18": {
        "label": "Delivery mode cross-field consistency (DQ14)",
        "category": "Consistency",
        "severity": "high",
        "description": "An assisted delivery type (forceps/vacuum/other) is recorded for an "
                        "encounter whose Mode of delivery says a non-assisted (spontaneous) delivery.",
    },
}
RULE_ORDER = ["V1", "V2", "V3", "V4", "V5", "V6", "V13", "V14", "V15", "V16", "V17", "V18"]

# Stillbirth-type Outcome of baby at birth values -- matched with ILIKE
# '%stillbirth%' in the predicate below, covers both "Fresh stillbirth" and
# "Macerated stillbirth" without enumerating each wording.
_LIVE_BIRTH_OUTCOMES = ["Live full term", "Live preterm"]

# Mode of delivery values that explicitly mean "not assisted" -- confirmed
# against the real extract: every encounter with an Assisted delivery type
# (Forceps/Vacuum/Other) recorded currently has Mode of delivery = 'SVD',
# never an assisted-sounding value (this system's Mode of delivery answer
# list has no assisted option at all), so flagging that specific pairing
# surfaces a real, if systemic, cross-field conflict rather than noise.
_NON_ASSISTED_DELIVERY_MODES = ["SVD", "Spontaneous Vertex Delivery"]


def _quoted(values):
    return ", ".join(f"'{v}'" for v in values)


def rule_predicates(data_path: str | None = None) -> dict:
    """{rule_id: SQL boolean expression}, one per RULE_ORDER entry. Each
    expression is safe to AND onto the page's own WHERE clause -- all
    literals here are static (no user input), so no escaping is needed.

    V14-V18 compare different concept_name rows for the same encounter
    (this table is one row per observation, not one row per encounter), so
    unlike V1-V6/V13 they can't be expressed as a condition on the row's
    own columns alone -- each is `encounter_id IN (<subquery over
    data_path>)` instead. data_path is required if any of V14-V18 are
    evaluated; omit it only when restricting rule_predicates() to V1-V6/V13.
    """
    vitals_clause = " OR ".join(
        f"({CONCEPT_NAME_} = '{concept}' AND {VALUE_NUMERIC_} IS NOT NULL "
        f"AND ({VALUE_NUMERIC_} < {lo} OR {VALUE_NUMERIC_} > {hi}))"
        for concept, lo, hi in VITAL_BOUNDS
    )
    predicates = {
        "V1": f"{BIRTHDATE_} IS NOT NULL AND {BIRTHDATE_} > CURRENT_DATE",
        "V2": f"{BIRTHDATE_} IS NOT NULL AND {BIRTHDATE_} < CURRENT_DATE - INTERVAL 120 YEAR",
        "V3": f"{OBS_DATETIME_} IS NOT NULL AND {OBS_DATETIME_} > CURRENT_TIMESTAMP",
        "V4": vitals_clause,
        "V5": f"{OBS_DATETIME_} IS NOT NULL AND {BIRTHDATE_} IS NOT NULL AND {OBS_DATETIME_} < {BIRTHDATE_}",
        "V6": f"{GENDER_} = 'Male' AND {PROGRAM_} IN ({_quoted(MATERNAL_PROGRAMS)})",
        "V13": (
            f"{_DRUG_UNITS_COL} IN ({_quoted(_TABLET_UNITS)}) AND {VALUE_NUMERIC_} IS NOT NULL "
            f"AND {VALUE_NUMERIC_} > {IMPLAUSIBLE_TABLET_COUNT}"
        ),
    }
    if data_path is None:
        return predicates

    predicates["V14"] = f"""
        {ENCOUNTER_ID_} IN (
            SELECT mg.{ENCOUNTER_ID_}
            FROM (
                SELECT DISTINCT {ENCOUNTER_ID_} FROM '{data_path}'
                WHERE {CONCEPT_NAME_} = 'Multiple gestation'
                AND COALESCE({VALUE_}, {OBS_VALUE_CODED_}) = 'Yes'
            ) mg
            LEFT JOIN (
                SELECT {ENCOUNTER_ID_}, COUNT(DISTINCT obs_group_id) AS n_groups
                FROM '{data_path}' WHERE {CONCEPT_NAME_} = 'Outcome of baby at birth'
                GROUP BY {ENCOUNTER_ID_}
            ) bg ON mg.{ENCOUNTER_ID_} = bg.{ENCOUNTER_ID_}
            WHERE COALESCE(bg.n_groups, 0) < 2
        )
    """
    predicates["V15"] = f"""
        {ENCOUNTER_ID_} IN (
            SELECT g.{ENCOUNTER_ID_}
            FROM (
                SELECT DISTINCT {ENCOUNTER_ID_}, {VALUE_NUMERIC_} AS gravida FROM '{data_path}'
                WHERE {CONCEPT_NAME_} = 'Gravida' AND {VALUE_NUMERIC_} IS NOT NULL
            ) g
            JOIN (
                SELECT DISTINCT {ENCOUNTER_ID_}, {VALUE_NUMERIC_} AS parity FROM '{data_path}'
                WHERE {CONCEPT_NAME_} = 'Parity' AND {VALUE_NUMERIC_} IS NOT NULL
            ) p ON g.{ENCOUNTER_ID_} = p.{ENCOUNTER_ID_}
            WHERE p.parity > g.gravida OR g.gravida < 0 OR p.parity < 0
        )
    """
    predicates["V16"] = f"""
        {ENCOUNTER_ID_} IN (
            SELECT f.{ENCOUNTER_ID_}
            FROM (
                SELECT DISTINCT {ENCOUNTER_ID_} FROM '{data_path}'
                WHERE {CONCEPT_NAME_} = 'Referral facility'
                AND COALESCE({VALUE_}, {OBS_VALUE_CODED_}) IS NOT NULL
            ) f
            LEFT JOIN (
                SELECT DISTINCT {ENCOUNTER_ID_} FROM '{data_path}'
                WHERE {CONCEPT_NAME_} = 'Date of referral'
                AND ({VALUE_} IS NOT NULL OR {VALUE_DATETIME_} IS NOT NULL)
            ) d ON f.{ENCOUNTER_ID_} = d.{ENCOUNTER_ID_}
            WHERE d.{ENCOUNTER_ID_} IS NULL
        )
    """
    predicates["V17"] = f"""
        {ENCOUNTER_ID_} IN (
            SELECT s.{ENCOUNTER_ID_}
            FROM (
                SELECT {ENCOUNTER_ID_},
                    MAX(CASE WHEN {CONCEPT_NAME_} = 'Live Births' THEN {VALUE_NUMERIC_} END) AS live_tally,
                    MAX(CASE WHEN {CONCEPT_NAME_} = 'Stillbirths' THEN {VALUE_NUMERIC_} END) AS stillbirth_tally
                FROM '{data_path}' WHERE {CONCEPT_NAME_} IN ('Live Births', 'Stillbirths')
                GROUP BY {ENCOUNTER_ID_}
            ) s
            JOIN (
                SELECT DISTINCT {ENCOUNTER_ID_}, COALESCE({VALUE_}, {OBS_VALUE_CODED_}) AS outcome_val
                FROM '{data_path}' WHERE {CONCEPT_NAME_} = 'Outcome of baby at birth'
            ) o ON s.{ENCOUNTER_ID_} = o.{ENCOUNTER_ID_}
            WHERE s.live_tally IS NOT NULL AND s.stillbirth_tally IS NOT NULL
            AND (s.live_tally + s.stillbirth_tally) = 1
            AND (
                (s.live_tally = 1 AND o.outcome_val ILIKE '%stillbirth%')
                OR (s.stillbirth_tally = 1 AND o.outcome_val IN ({_quoted(_LIVE_BIRTH_OUTCOMES)}))
            )
        )
    """
    predicates["V18"] = f"""
        {ENCOUNTER_ID_} IN (
            SELECT a.{ENCOUNTER_ID_}
            FROM (
                SELECT DISTINCT {ENCOUNTER_ID_} FROM '{data_path}'
                WHERE {CONCEPT_NAME_} = 'Assisted delivery type'
            ) a
            JOIN (
                SELECT DISTINCT {ENCOUNTER_ID_}, COALESCE({VALUE_}, {OBS_VALUE_CODED_}) AS mode
                FROM '{data_path}' WHERE {CONCEPT_NAME_} = 'Mode of delivery'
            ) m ON a.{ENCOUNTER_ID_} = m.{ENCOUNTER_ID_}
            WHERE m.mode IN ({_quoted(_NON_ASSISTED_DELIVERY_MODES)})
        )
    """
    return predicates
