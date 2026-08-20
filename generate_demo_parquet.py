"""
Generate realistic day-by-day MNH clinical data for data/default/parquet/.

Simulates how real facilities actually enter data -- a small, varying number
of patients per facility per day (not a batch of 30-40 patients dropped on
one random day of the month) -- across real facilities drawn from
data/geo/facilities_levels.json (the national facility registry), using the
exact concept_name/obs_value_coded vocabulary MNID's indicator masks expect
(mnid/views/executive_views.py, mnid/core/data_utils.py's mnid_* flag
columns), so Country Profile / Run Charts / the heatmap have real day-level
variation to show, not 11 total matching rows spread across 10 months.

Run: python generate_demo_parquet.py
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

random.seed(42)
np.random.seed(42)

OUT_DIR = Path("data/default/parquet")
OUT_DIR.mkdir(parents=True, exist_ok=True)

GENERATION_DAYS = 60  # ~2 months, ending today -- re-running this later stays current

# Geography: real facilities from the national registry 
_FACILITIES_REGISTRY_PATH = Path("data/geo/facilities_levels.json")


def _load_real_facilities(target_districts: int = 14, max_secondary_per_district: int = 2,
                          max_primary_per_district: int = 2) -> list[dict]:
    """A representative subset of real facilities: every Tertiary (Central)
    hospital nationally, plus up to a couple of Secondary/Primary facilities
    each in the districts with the most registry entries -- broad geographic
    spread without generating for all 295 facilities."""
    registry = json.loads(_FACILITIES_REGISTRY_PATH.read_text(encoding="utf-8"))
    by_district: dict[str, list[dict]] = {}
    for r in registry:
        by_district.setdefault(r["DISTRICT"], []).append(r)

    chosen: dict[str, dict] = {}
    for r in registry:
        if r["FACILITY LEVEL"] == "Tertiary":
            chosen[r["CODE"]] = r

    top_districts = sorted(by_district, key=lambda d: -len(by_district[d]))[:target_districts]
    for d in top_districts:
        recs = sorted(by_district[d], key=lambda r: r["CODE"])
        secondary = [r for r in recs if r["FACILITY LEVEL"] == "Secondary"][:max_secondary_per_district]
        primary = [r for r in recs if r["FACILITY LEVEL"] == "Primary"][:max_primary_per_district]
        for r in secondary + primary:
            chosen[r["CODE"]] = r

    return sorted(chosen.values(), key=lambda r: (r["DISTRICT"], r["CODE"]))


FACILITIES = _load_real_facilities()


def _build_window(today: date, days: int) -> list[date]:
    start = today - timedelta(days=days - 1)
    return [start + timedelta(days=i) for i in range(days)]


WINDOW = _build_window(date.today(), GENERATION_DAYS)

# Average daily patient count per facility level per service area. Real
# volume, not demo-batch volume -- a Tertiary (Central Hospital) sees far
# more than a Primary health centre in a day.
_LEVEL_DAILY_MEAN = {
    "Tertiary":  {"anc": 6.0, "labour": 4.0, "pnc": 3.0, "newborn": 3.0},
    "Secondary": {"anc": 3.0, "labour": 2.0, "pnc": 1.5, "newborn": 1.5},
    "Primary":   {"anc": 1.2, "labour": 0.4, "pnc": 0.3, "newborn": 0.3},
}


def _daily_count(level: str, service: str, day: date) -> int:
    mean = _LEVEL_DAILY_MEAN.get(level, _LEVEL_DAILY_MEAN["Primary"])[service]
    # Scheduled/routine visits taper off on weekends; births and newborn
    # admissions aren't scheduled, so no weekend dampening for those.
    if service == "anc" and day.weekday() >= 5:
        mean *= 0.35
    elif service == "pnc" and day.weekday() >= 5:
        mean *= 0.5
    return int(np.random.poisson(mean)) if mean > 0 else 0


# Average coverage target ± per-facility jitter
JITTER = 0.09

_id_seq = [0]


def _next_id() -> int:
    _id_seq[0] += 1
    return _id_seq[0]


_FIRST_NAMES = ["Chimwemwe", "Thandiwe", "Mphatso", "Tadala", "Chisomo", "Grace",
                "Precious", "Yamikani", "Blessings", "Esther", "Memory", "Patuma"]
_LAST_NAMES = ["Banda", "Phiri", "Mvula", "Nyirenda", "Chirwa", "Kamanga",
               "Gondwe", "Mbewe", "Chiumia", "Kachigunda", "Zulu", "Tembo"]


def _birthdate_for(age: int, reference: date) -> date:
    # Spread within the birth year so AgeDays isn't identical for everyone
    # who happens to share an Age value.
    days_into_year = random.randint(0, 364)
    try:
        return date(reference.year - age, 1, 1) + timedelta(days=days_into_year)
    except ValueError:
        return date(reference.year - age, 1, 1)


def _age_group(age: int) -> str:
    if age < 5:
        return "Under 5"
    if age < 15:
        return "5-14"
    if age < 25:
        return "15-24"
    if age < 35:
        return "25-34"
    if age < 50:
        return "35-49"
    return "50+"


def _sample_n(population: list, rate: float) -> set:
    if not population:
        return set()
    n = max(1, round(len(population) * rate))
    return set(random.sample(population, min(n, len(population))))


def _make_row(
    pid, enc_id, visit_date: date, program, service_area, facility, fac_code,
    district, encounter, new_revisit, age, gender,
    concept=None, obs_coded=None, value=None, value_n=None,
) -> dict:
    birthdate = _birthdate_for(age, visit_date)
    return {
        "person_id": pid,
        "encounter_id": enc_id,
        "given_name": random.choice(_FIRST_NAMES),
        "family_name": random.choice(_LAST_NAMES),
        "Gender": gender,
        "birthdate": birthdate,
        "AgeDays": float((visit_date - birthdate).days),
        "Age": float(age),
        "Age_Group": _age_group(age),
        "Date": visit_date.isoformat(),
        "Source_Program": "",
        "Program": program,
        "Reporting_Program": "",
        "Service_Area": service_area,
        "Facility": facility,
        "Facility_CODE": fac_code,
        "User": "MAHIS_DHO",
        "District": district,
        "Encounter": encounter,
        "Home_district": district,
        "TA": "",
        "Village": "",
        "visit_days": 1,
        "obs_value_coded": obs_coded or "",
        "concept_name": concept or "",
        "Value": value or "",
        "ValueN": value_n,
        "DrugName": "",
        "Value_name": None,
        "Order_Name": "",
        "new_revisit": new_revisit,
    }


all_rows: list[dict] = []


def _obs(template: dict, concept: str, obs_coded=None, value_n=None, encounter: str | None = None) -> None:
    row = dict(template)
    row["concept_name"] = concept or ""
    row["obs_value_coded"] = obs_coded or ""
    row["ValueN"] = value_n
    if encounter is not None:
        row["Encounter"] = encounter
    all_rows.append(row)


# Per-indicator base coverage rates (deliberately spread across the red/amber/green
# bands instead of one shared `rate` for every concept, which previously made every
# indicator converge on ~28-31%). Each facility gets a small jitter applied uniformly
# across all of these, so the *ranking* of indicators stays stable per facility while
# the absolute numbers still vary facility-to-facility.
RATES = {
    "anemia": 0.82, "hiv_test": 0.55, "bp": 0.42, "syphilis": 0.24, "urine": 0.91,
    "ga": 0.68, "tetanus": 0.50, "preg_planned": 0.36, "danger_signs": 0.78,
    "lab_core": 0.46, "vitk_lab": 0.88, "breastfeeding_lab": 0.73,
    "cortico": 0.24, "caesarean": 0.34, "kmc_mgmt": 0.58,
    "pnc_core": 0.63, "mother_alive": 0.95, "baby_alive": 0.92,
    "bcg": 0.70, "hiv_pos": 0.18, "lbw": 0.32,
    "nb_core": 0.80, "ikmc": 0.62, "resus": 0.40, "thermal_ok": 0.86,
    "vitk_nb": 0.90, "eligible_resus_given": 0.48,
    "pulse_ox": 0.72, "phototherapy": 0.65,
    "hepatitis_b": 0.45, "sp_3plus": 0.35, "mms_180": 0.25,
    "fefo_120": 0.55, "itn": 0.50, "uterine_scar": 0.08,
    "first_trimester": 0.40, "hiv_positive_rate": 0.12, "hiv_art_coverage": 0.85,
    "tdv_2plus": 0.55,
}


def _r(key: str, jitter: float) -> float:
    return max(0.02, min(0.98, RATES[key] + jitter))


SCENARIOS = [
    {
        "name": "baseline",
        "live_birth": 0.90, "fresh_stillbirth": 0.05, "macerated_stillbirth": 0.05,
        "maternal_death": 0.04, "neonatal_death": 0.08,
        "obstetric_weights": [0.10, 0.07, 0.08, 0.10, 0.65],
        "newborn_comp_weights": [0.12, 0.12, 0.10, 0.66],
        "maternal_sepsis": 0.10, "pph": 0.13, "ruptured_uterus": 0.04,
        "birth_asphyxia": 0.12, "neonatal_sepsis": 0.11,
    },
    {
        "name": "stillbirth_spike",
        "live_birth": 0.78, "fresh_stillbirth": 0.13, "macerated_stillbirth": 0.09,
        "maternal_death": 0.05, "neonatal_death": 0.10,
        "obstetric_weights": [0.12, 0.10, 0.11, 0.13, 0.54],
        "newborn_comp_weights": [0.18, 0.14, 0.11, 0.57],
        "maternal_sepsis": 0.12, "pph": 0.16, "ruptured_uterus": 0.07,
        "birth_asphyxia": 0.18, "neonatal_sepsis": 0.12,
    },
    {
        "name": "sepsis_pressure",
        "live_birth": 0.88, "fresh_stillbirth": 0.06, "macerated_stillbirth": 0.06,
        "maternal_death": 0.06, "neonatal_death": 0.16,
        "obstetric_weights": [0.11, 0.08, 0.08, 0.10, 0.63],
        "newborn_comp_weights": [0.10, 0.15, 0.24, 0.51],
        "maternal_sepsis": 0.20, "pph": 0.14, "ruptured_uterus": 0.05,
        "birth_asphyxia": 0.13, "neonatal_sepsis": 0.26,
    },
    {
        "name": "recovery",
        "live_birth": 0.94, "fresh_stillbirth": 0.03, "macerated_stillbirth": 0.03,
        "maternal_death": 0.02, "neonatal_death": 0.05,
        "obstetric_weights": [0.07, 0.04, 0.05, 0.07, 0.77],
        "newborn_comp_weights": [0.08, 0.08, 0.07, 0.77],
        "maternal_sepsis": 0.05, "pph": 0.08, "ruptured_uterus": 0.02,
        "birth_asphyxia": 0.08, "neonatal_sepsis": 0.08,
    },
]


def _scenario_for(fac_code: str, tag: str) -> dict:
    # Rotate by month (tag=YYYYMM) so a scenario holds for a whole month --
    # more realistic than flipping the underlying clinical picture daily.
    digits = "".join(ch for ch in fac_code if ch.isdigit()) or "0"
    idx = (int(digits) + int(tag[-2:]) + int(tag[:4])) % len(SCENARIOS)
    return SCENARIOS[idx]


for fac in FACILITIES:
    fac_code = fac["CODE"]
    fac_name = fac["NAME"]
    district = fac["DISTRICT"]
    level = fac["FACILITY LEVEL"]
    fac_jitter = random.uniform(-JITTER, JITTER)

    for day in WINDOW:
        tag = f"{day.year}{day.month:02d}"
        scenario = _scenario_for(fac_code, tag)

        # ANC
        anc_pids = [_next_id() for _ in range(_daily_count(level, "anc", day))]
        anc_sets = {key: _sample_n(anc_pids, _r(key, fac_jitter)) for key in [
            "anemia", "hiv_test", "bp", "syphilis", "urine", "ga", "tetanus",
            "preg_planned", "danger_signs",
            "hepatitis_b", "sp_3plus", "mms_180", "fefo_120", "itn",
            "uterine_scar", "first_trimester", "tdv_2plus",
        ]}

        for pid in anc_pids:
            enc_id = _next_id()
            age = random.randint(15, 41)
            nr = "new" if random.random() < 0.3 else "revisit"
            tmpl = _make_row(
                pid, enc_id, day,
                "ANC PROGRAM", "ANC PROGRAM",
                fac_name, fac_code, district,
                "ANC visit", nr, age, "Female",
            )
            all_rows.append(tmpl)  # base denominator row

            if pid in anc_sets["anemia"]:
                _obs(tmpl, "Anemia screening", "Yes")
            if pid in anc_sets["bp"]:
                _obs(tmpl, "Systolic blood pressure", value_n=float(random.randint(100, 140)))
                _obs(tmpl, "Diastolic blood pressure", value_n=float(random.randint(60, 90)))
            if pid in anc_sets["syphilis"]:
                _obs(tmpl, "Syphilis Test Result", "Negative")
            if pid in anc_sets["urine"]:
                _obs(tmpl, "Urine test status", "urine test conducted")
            if pid in anc_sets["ga"]:
                _obs(tmpl, "Gestational age recorded", "GA by ultrasound")
            if pid in anc_sets["tetanus"]:
                _obs(tmpl, "Number of tetanus doses", "two doses")
            if pid in anc_sets["preg_planned"]:
                _obs(tmpl, "Pregnancy planned", "Yes")
            if pid in anc_sets["danger_signs"]:
                _obs(tmpl, "Danger signs present", "Yes")
            if pid in anc_sets["hepatitis_b"]:
                _obs(tmpl, "Hepatitis B Status", "Negative")
            if pid in anc_sets["sp_3plus"]:
                _obs(tmpl, "SP doses", "3 doses")
            if pid in anc_sets["mms_180"]:
                _obs(tmpl, "MMS tablets", value_n=180.0)
            if pid in anc_sets["fefo_120"]:
                _obs(tmpl, "FeFo tablets", value_n=130.0)
            if pid in anc_sets["itn"]:
                _obs(tmpl, "ITN given", "Yes")
            if pid in anc_sets["uterine_scar"]:
                _obs(tmpl, "Previous uterine scars", "Yes")
            if pid in anc_sets["tdv_2plus"]:
                _obs(tmpl, "Number of tetanus doses", "two doses")
            if pid in anc_sets["hiv_test"]:
                hiv_positive = random.random() < _r("hiv_positive_rate", fac_jitter)
                _obs(tmpl, "HIV Test", "Positive" if hiv_positive else "Negative")
                if hiv_positive:
                    on_art = random.random() < _r("hiv_art_coverage", fac_jitter)
                    _obs(tmpl, "ART started", "Yes" if on_art else "No")
            if pid in anc_sets["first_trimester"]:
                _obs(tmpl, "Gestation in weeks", value_n=float(random.randint(6, 12)))

        # Labour
        lab_pids = [_next_id() for _ in range(_daily_count(level, "labour", day))]
        num_lab = _sample_n(lab_pids, _r("lab_core", fac_jitter))
        vitk_lab_num = _sample_n(lab_pids, _r("vitk_lab", fac_jitter))
        breastfeeding_num = _sample_n(lab_pids, _r("breastfeeding_lab", fac_jitter))

        cortico_denom = _sample_n(lab_pids, 0.6)
        cortico_num = _sample_n(list(cortico_denom), _r("cortico", fac_jitter))
        mod_denom = _sample_n(lab_pids, 0.6)
        caesarean_num = _sample_n(list(mod_denom), _r("caesarean", fac_jitter))
        mgmt_denom = _sample_n(lab_pids, 0.6)
        kmc_num = _sample_n(list(mgmt_denom), _r("kmc_mgmt", fac_jitter))

        for pid in lab_pids:
            enc_id = _next_id()
            age = random.randint(16, 43)
            enc = random.choice([
                "Labour assessment",
                "Labour assessment",
                "Labour and delivery visit",
            ])
            tmpl = _make_row(
                pid, enc_id, day,
                "LABOUR AND DELIVERY PROGRAM", "LABOUR AND DELIVERY PROGRAM",
                fac_name, fac_code, district,
                enc, "new", age, "Female",
            )
            all_rows.append(tmpl)

            outcome = random.choices(
                ["Live birth", "Fresh still birth", "Macerated still birth"],
                weights=[scenario["live_birth"], scenario["fresh_stillbirth"], scenario["macerated_stillbirth"]],
                k=1,
            )[0]
            obstetric_comp = random.choices(
                ["PPH", "Eclampsia", "Obstructed labour", "Preterm labour", "None"],
                weights=scenario["obstetric_weights"], k=1,
            )[0]
            newborn_comp = random.choices(
                ["Birth asphyxia", "Prematurity", "Sepsis", "None"],
                weights=scenario["newborn_comp_weights"], k=1,
            )[0]

            _delivery_location = (
                "This facility" if random.random() > 0.12
                else random.choice(["Home", "Referral facility", "Community"])
            )
            _obs(tmpl, "Place of delivery", _delivery_location)
            _obs(tmpl, "Outcome of the delivery", outcome)
            _obs(tmpl, "Obstetric complications", obstetric_comp)
            _obs(tmpl, "Newborn baby complications", newborn_comp)
            _obs(tmpl, "Estimated blood loss", value_n=float(random.randint(150, 700)))

            if pid in num_lab:
                if random.random() < scenario["maternal_sepsis"] or obstetric_comp == "None":
                    _obs(tmpl, "Maternal sepsis", "Yes" if random.random() < scenario["maternal_sepsis"] else "No")
                if random.random() < scenario["pph"] or obstetric_comp == "PPH":
                    _obs(tmpl, "PPH", "Yes")
                    _obs(tmpl, "Oxytocin 10 iu given", "Yes")
                    if random.random() < 0.72:
                        _obs(tmpl, "1g Tranexamic Acid IV slow push over 10 minutes", "Yes")
                    if random.random() < 0.55:
                        _obs(tmpl, "Misoprostol 800 micrograms", "Yes")
                if random.random() < scenario["ruptured_uterus"]:
                    _obs(tmpl, "Obstetric complications", "Ruptured uterus")

            _obs(tmpl, "Vitamin K given", "Yes" if pid in vitk_lab_num else "No")
            _obs(tmpl, "Breast feeding", "Yes" if pid in breastfeeding_num else "No")

            if pid in cortico_denom:
                _obs(tmpl, "Antenatal corticosteroids given", "Yes" if pid in cortico_num else "No")

            if pid in mod_denom:
                _obs(
                    tmpl, "Mode of delivery",
                    "Caesarean section" if pid in caesarean_num else "Normal vaginal delivery",
                )

            _obs(tmpl, "Skilled birth attendant", "Yes" if random.random() < 0.88 else "No")
            if random.random() < 0.35:
                hiv_pos = random.random() < 0.10
                _obs(tmpl, "HIV Test", "Positive" if hiv_pos else "Negative")
                if hiv_pos:
                    _obs(tmpl, "ART started", "Yes" if random.random() < 0.85 else "No")
            if random.random() < 0.15:
                _obs(tmpl, "Referred to another facility", "Yes")
                _obs(tmpl, "Referral reason", random.choice(["PPH", "Eclampsia", "Obstructed labour", "Sepsis"]))
            if random.random() < scenario["maternal_death"]:
                _obs(tmpl, "Maternal death", "Yes")
                _obs(tmpl, "Maternal death cause", random.choice(["PPH", "Sepsis", "Eclampsia", "Obstructed labour"]))
            if pid in num_lab and random.random() < 0.60:
                _obs(tmpl, "ARV prophylaxis given to baby", "Yes" if random.random() < 0.30 else "No")

            if random.random() < 0.75:
                _obs(tmpl, "Parenteral antibiotics given", "Yes")
            if random.random() < 0.05:
                _obs(tmpl, "Manual removal of placenta", "Yes")
            if random.random() < 0.03:
                _obs(tmpl, "Manual vacuum aspiration", "Yes")
            if pid in mod_denom and random.random() < 0.04:
                if random.random() < 0.50:
                    _obs(tmpl, "Mode of delivery", "Assisted vaginal delivery")
            if random.random() < 0.50:
                _obs(tmpl, "Neonatal resuscitation provided", "Yes")
            if random.random() < 0.04:
                _obs(tmpl, "Blood transfusion given", "Yes")

            if pid in mgmt_denom:
                _obs(
                    tmpl, "Management given to newborn",
                    "KMC" if pid in kmc_num else (
                        "Antibiotics" if newborn_comp == "Sepsis"
                        else "Resuscitation" if newborn_comp == "Birth asphyxia"
                        else "Routine care"
                    ),
                )

        #  PNC
        pnc_pids = [_next_id() for _ in range(_daily_count(level, "pnc", day))]
        num_pnc = _sample_n(pnc_pids, _r("pnc_core", fac_jitter))

        mother_denom = _sample_n(pnc_pids, 0.85)
        mother_alive = _sample_n(list(mother_denom), max(0.02, 1 - scenario["maternal_death"]))
        baby_denom = _sample_n(pnc_pids, 0.85)
        baby_alive = _sample_n(list(baby_denom), max(0.02, 1 - scenario["neonatal_death"]))
        immun_denom = _sample_n(pnc_pids, 0.75)
        bcg_num = _sample_n(list(immun_denom), _r("bcg", fac_jitter))
        hiv_denom = _sample_n(pnc_pids, 0.70)
        hiv_pos = _sample_n(list(hiv_denom), _r("hiv_pos", fac_jitter))
        prematurity_denom = _sample_n(pnc_pids, 0.50)
        lbw_num = _sample_n(list(prematurity_denom), _r("lbw", fac_jitter))
        fp_denom = _sample_n(pnc_pids, 0.50)
        fp_num = _sample_n(list(fp_denom), 0.35)
        check_7days_num = _sample_n(pnc_pids, 0.40)
        check_6weeks_num = _sample_n(pnc_pids, 0.20)
        ebf_num = _sample_n(pnc_pids, 0.55)
        admitted_denom = _sample_n(pnc_pids, 0.70)

        for pid in pnc_pids:
            enc_id = _next_id()
            age = random.randint(15, 40)
            tmpl = _make_row(
                pid, enc_id, day,
                "PNC PROGRAM", "PNC PROGRAM",
                fac_name, fac_code, district,
                "Pnc visit", "new", age, "Female",
            )
            all_rows.append(tmpl)

            if pid in num_pnc:
                _obs(tmpl, "Postnatal check period", "Up to 48 hrs or before discharge")

            if pid in mother_denom:
                _obs(tmpl, "Status of the mother", "Alive" if pid in mother_alive else "Deceased")
            if pid in baby_denom:
                _obs(tmpl, "Status of baby", "Alive" if pid in baby_alive else "Deceased")
            if pid in immun_denom:
                imm_val = "BCG" if pid in bcg_num else random.choice(["OPV", "Polio 0"])
                _obs(tmpl, "Immunisation given", imm_val)
            if pid in hiv_denom:
                _obs(tmpl, "Mother HIV Status", "Positive" if pid in hiv_pos else "Negative")
            if pid in prematurity_denom:
                is_lbw = pid in lbw_num
                _obs(tmpl, "Prematurity/Kangaroo", "Low birth weight" if is_lbw else "Normal weight")
                if is_lbw and random.random() < 0.30:
                    _obs(tmpl, "Management given to newborn", "KMC")

            if pid in admitted_denom:
                _obs(tmpl, "Admission status", "Admitted")
                _obs(tmpl, "Baby admission", "Admitted")
            if pid in num_pnc and random.random() < 0.12:
                _obs(tmpl, "Postnatal complications", random.choice(["PPH", "Infection", "Wound sepsis"]))
            if pid in num_pnc and random.random() < 0.08:
                _obs(tmpl, "Newborn baby complications", random.choice(["Sepsis", "Jaundice", "Birth asphyxia"]))
            if pid in hiv_pos and random.random() < 0.50:
                _obs(tmpl, "HIV exposed baby", "Yes")
                if random.random() < 0.80:
                    _obs(tmpl, "ARV prophylaxis given to baby", "Yes")
            if pid in check_7days_num:
                _obs(tmpl, "Postnatal check period", "Within 7 days")
            if pid in check_6weeks_num:
                _obs(tmpl, "Postnatal check period", "At 6 weeks")
            if pid in fp_num:
                _obs(tmpl, "Family planning counselling", "Yes")
            if pid in ebf_num:
                _obs(tmpl, "Exclusive breastfeeding counselling", "Yes")

        #  Newborn
        nb_pids = [_next_id() for _ in range(_daily_count(level, "newborn", day))]
        num_nb = _sample_n(nb_pids, _r("nb_core", fac_jitter))

        ikmc_denom = _sample_n(nb_pids, 0.55)
        ikmc_num = _sample_n(list(ikmc_denom), _r("ikmc", fac_jitter))
        resus_denom = _sample_n(nb_pids, 0.45)
        resus_num = _sample_n(list(resus_denom), _r("resus", fac_jitter))
        thermal_denom = _sample_n(nb_pids, 0.65)
        thermal_num = _sample_n(list(thermal_denom), _r("thermal_ok", fac_jitter))
        vitk_denom = _sample_n(nb_pids, 0.65)
        vitk_num = _sample_n(list(vitk_denom), _r("vitk_nb", fac_jitter))
        eligible_denom = _sample_n(nb_pids, 0.45)
        eligible_resus = _sample_n(list(eligible_denom), _r("eligible_resus_given", fac_jitter))
        cpap_1000_pids = _sample_n(nb_pids, 0.12)
        cpap_1500_pids = _sample_n(nb_pids, 0.15)

        pulse_ox_denom = _sample_n(nb_pids, 0.75)
        pulse_ox_num = _sample_n(list(pulse_ox_denom), _r("pulse_ox", fac_jitter))
        jaundice_pids = _sample_n(nb_pids, 0.15)
        bili_denom = _sample_n(list(jaundice_pids), 0.80)
        photo_denom = _sample_n(list(jaundice_pids), _r("phototherapy", fac_jitter))
        glucose_denom = _sample_n(nb_pids, 0.60)
        sepsis_pids: set = set()

        for pid in nb_pids:
            enc_id = _next_id()
            tmpl = _make_row(
                pid, enc_id, day,
                "NEONATAL PROGRAM", "NEONATAL PROGRAM",
                fac_name, fac_code, district,
                "Neonatal enrolment", "new", 0, "Female",
            )
            all_rows.append(tmpl)

            if pid in num_nb:
                bw = (
                    random.uniform(1000, 1499) if pid in cpap_1000_pids
                    else random.uniform(1500, 1999) if pid in cpap_1500_pids
                    else random.uniform(2000, 3800)
                )
                _obs(tmpl, "Birth weight", value_n=round(bw, 1))
                _obs(tmpl, "Gestation in weeks", value_n=float(random.randint(28, 42)))
                _obs(tmpl, "thermal care", "Yes")
                _obs(tmpl, "Admission outcome", "Died" if random.random() < scenario["neonatal_death"] else "Discharged")
                if pid in cpap_1000_pids or pid in cpap_1500_pids:
                    _obs(tmpl, "CPAP support", "Bubble CPAP")
                if random.random() < scenario["birth_asphyxia"]:
                    _obs(tmpl, "Birth asphyxia suspected", "Yes")
                if random.random() < scenario["neonatal_sepsis"]:
                    _obs(tmpl, "Neonatal Sepsis - Early Onset", "Yes")
                    sepsis_pids.add(pid)

            if pid in pulse_ox_denom:
                _obs(tmpl, "Pulse oximeter used at admission", "Yes" if pid in pulse_ox_num else "No")

            if pid in jaundice_pids:
                _obs(tmpl, "Clinical jaundice", "Yes")
                if pid in bili_denom:
                    _bili = round(random.uniform(8, 22), 1)
                    _obs(tmpl, "Total Serum Bilirubin", str(_bili), value_n=_bili)
                if pid in photo_denom:
                    _obs(tmpl, "Phototherapy given", "Yes")

            if pid in glucose_denom:
                _obs(tmpl, "Blood glucose", value_n=round(random.uniform(2.2, 6.5), 1))

            if pid in ikmc_denom:
                _obs(tmpl, "iKMC initiated", "Yes" if pid in ikmc_num else "No")
            if pid in resus_denom:
                _obs(tmpl, "Neonatal resuscitation provided", "Yes" if pid in resus_num else "No")
            if pid in thermal_denom:
                _obs(tmpl, "Thermal status on admission", "Not hypothermic" if pid in thermal_num else "Hypothermic")
            if pid in vitk_denom:
                _obs(tmpl, "Vitamin K given", "Yes" if pid in vitk_num else "No")
            if pid in eligible_denom:
                _obs(tmpl, "Eligible for neonatal resuscitation", "Yes")
                _obs(tmpl, "Neonatal resuscitation provided", "Yes" if pid in eligible_resus else "No")
            if pid in sepsis_pids or random.random() < scenario["neonatal_sepsis"] * 0.3:
                _obs(tmpl, "Parenteral antibiotics given", "Yes")

    # Operational readiness (facility-level, once per month)
    seen_months = sorted({(d.year, d.month) for d in WINDOW})
    for year, month in seen_months:
        month_days = [d for d in WINDOW if d.year == year and d.month == month]
        ready_day = random.choice(month_days)
        ready_pid, ready_enc = _next_id(), _next_id()
        ready_tmpl = _make_row(
            ready_pid, ready_enc, ready_day,
            "ANC PROGRAM", "ANC PROGRAM",
            fac_name, fac_code, district,
            "ANC visit", "new", 30, "Female",
        )
        all_rows.append(ready_tmpl)
        _obs(ready_tmpl, "Essential medicine availability",
             "All available" if random.random() < 0.74 else random.choice(["Partially available", "Stocked out"]))
        _obs(ready_tmpl, "EmONC competency assessed",
             "Assessed" if random.random() < 0.81 else "Not assessed")
        _obs(ready_tmpl, "Record completeness",
             "Complete" if random.random() < 0.88 else "Incomplete")
        _obs(ready_tmpl, "Data entered within 7 days",
             "Yes" if random.random() < 0.83 else "No")


# Compile and save 
df = pd.DataFrame(all_rows)
df["Date"] = pd.to_datetime(df["Date"])
df["birthdate"] = pd.to_datetime(df["birthdate"])

# Force string columns so parquet stores them as utf-8, matching the real
# data/default/parquet schema exactly (person_id/encounter_id stay integer,
# Date/birthdate stay datetime, Age/AgeDays/ValueN stay float).
STR_COLS = [
    "given_name", "family_name", "Gender", "Source_Program", "Program",
    "Reporting_Program", "Service_Area", "Facility", "Facility_CODE", "User",
    "District", "Encounter", "Home_district", "TA", "Village",
    "obs_value_coded", "concept_name", "Value", "DrugName", "Order_Name",
    "new_revisit", "Age_Group",
]
for col in STR_COLS:
    if col in df.columns:
        df[col] = df[col].astype(object)

saved = 0
for (year, month), chunk in df.groupby([df["Date"].dt.year, df["Date"].dt.month]):
    tag = f"{year}{month:02d}"
    path = OUT_DIR / f"data_{tag}.parquet"
    chunk.to_parquet(path, index=False)
    print(f"  {path.name}: {len(chunk):,} rows  |  "
          f"{chunk['District'].nunique()} districts  |  "
          f"{chunk['Facility'].nunique()} facilities")
    saved += 1

print(f"\nWrote {saved} parquet files to {OUT_DIR}/")
print(f"Total rows: {len(df):,}")
print(f"Districts: {df['District'].nunique()}  Facilities: {df['Facility'].nunique()}")
print(f"Date range: {df['Date'].min().date()} -> {df['Date'].max().date()}")
print(f"MCH rows: {len(df[df['Program'].str.contains('ANC|LABOUR|PNC|NEONATAL', case=False, na=False)]):,}")
