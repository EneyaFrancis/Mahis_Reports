"""Row-level validity/accuracy/consistency checks for the Data Quality
page's Validity and outliers tab.

Unlike Duplicates (person-level bucket matching over a pre-fetched roster),
each rule here flags an individual observation row directly, so every rule
is expressed as a SQL boolean predicate the page ANDs onto its own
scope/date/facility WHERE clause and pushes down to DuckDB.
"""
from config import BIRTHDATE_, OBS_DATETIME_, GENDER_, PROGRAM_, VALUE_NUMERIC_, CONCEPT_NAME_

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
}
RULE_ORDER = ["V1", "V2", "V3", "V4", "V5", "V6", "V13"]


def _quoted(values):
    return ", ".join(f"'{v}'" for v in values)


def rule_predicates() -> dict:
    """{rule_id: SQL boolean expression}, one per RULE_ORDER entry. Each
    expression is safe to AND onto the page's own WHERE clause -- all
    literals here are static (no user input), so no escaping is needed."""
    vitals_clause = " OR ".join(
        f"({CONCEPT_NAME_} = '{concept}' AND {VALUE_NUMERIC_} IS NOT NULL "
        f"AND ({VALUE_NUMERIC_} < {lo} OR {VALUE_NUMERIC_} > {hi}))"
        for concept, lo, hi in VITAL_BOUNDS
    )
    return {
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
