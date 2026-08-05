"""
Publish DHIS2-calculated indicators into MNID's aggregate store, under the same
schema/location mnid/aggregation/engine.py already produces for MAHIS routes
(data/mnid_aggregates/<route>/indicator_aggregates.parquet), so the existing
mnid/aggregation/store.py::get_aggregate() reads either source unmodified.

Reads credentials from config.py (bridged into the env vars mnid.dhis2.settings
expects) rather than requiring them to be exported by the caller's shell -- see
config.MNH_DHIS2_* for where those live.

Run once (or on a schedule): python -m mnid.dhis2.mnid_publish
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .calculations import calculate_indicators
from .client import DHIS2Client, parse_analytics_response
from .mappings import atomic_dx_values
from .periods import monthly_periods, period_start_date
from .sample_sync import _chunks, _selected_mapping
from .settings import DHIS2Settings
from .storage import atomic_json, atomic_parquet

_LOG = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# DHIS2 native indicator id -> MNID indicator id. Built from an exact label match
# between mnid/dhis2/config/indicators.json and mnid/aggregation/engine.py's loaded
# indicator set (see plan/session notes) -- 22 map onto pre-existing MNID
# indicators; 3 (Total/Fresh/Macerated births) were added as new MNID entries
# specifically for this (see scripts/add_dhis2_only_indicators.py).
DHIS2_TO_MNID_ID = {
    'live_births': 'mnid_lab_overview_004',
    'total_births': 'mnid_lab_core_totalbirths',
    'fresh_stillbirths': 'mnid_lab_core_freshstillbirths',
    'macerated_stillbirths': 'mnid_lab_core_maceratedstillbirths',
    'maternal_deaths': 'mnid_pnc_overview_004',
    'neonatal_deaths': 'mnid_nb_overview_002',
    'stillbirths': 'mnid_lab_overview_005',
    'anc_visits': 'mnid_anc_overview_001',
    # 'blood_pressure_measured' is intentionally mapped to a pre-eclampsia
    # screen-result flag ("RHD ANC (Pre-)Eclampsia Yes", sNlbaVoobzq), not a
    # literal "BP was taken" counter -- confirmed no dedicated BP-taken data
    # element exists anywhere in this DHIS2 instance (checked its own
    # dataElements API directly). Kept as this proxy per explicit product
    # decision: assessing for (pre-)eclampsia inherently requires taking
    # blood pressure, so a positive/negative eclampsia screen result implies
    # BP was checked for that visit. Caveat that still applies: this counts
    # visits with a *recorded eclampsia-screen outcome*, which is likely a
    # narrower population than "every visit where BP was physically taken" --
    # treat the resulting percentage as a floor, not an exact count.
    'blood_pressure_measured': 'mnid_anc_prog_007',
    'tested_for_hiv': 'mnid_anc_prog_006',
    'screened_for_syphilis': 'mnid_anc_core_002',
    'at_least_4_anc_contacts': 'mnid_anc_prog_008',
    'tetanus_doses_2': 'mnid_anc_prog_004',
    'new_anc_registrations': 'mnid_anc_moh_002',
    'started_anc_in_first_trimester_0_12_weeks': 'mnid_anc_moh_011',
    'received_120_fefo_tablets': 'mnid_anc_moh_015',
    'received_itn_during_anc': 'mnid_anc_moh_016',
    'uterotonic_given_after_birth': 'mnid_lab_core_001',
    'newborns_not_breathing_at_birth_receiving_bag_mask_ventilation': 'mnid_lab_prog_012',
    'vitamin_k_at_birth': 'mnid_pnc_prog_011',
    'facility_deliveries': 'mnid_lab_prog_003',
    'delivered_at_this_facility': 'mnid_lab_moh_001',
    'delivered_at_home_or_in_transit': 'mnid_lab_moh_002',
    'delivered_by_skilled_attendant': 'mnid_lab_moh_005',
    'normal_vaginal_delivery': 'mnid_lab_moh_006',

    # Added when mnid/dhis2/sample_sync.py's INDICATOR_GROUPS grew from 25 to
    # all 52 indicators.json entries -- these 27 were being pulled and
    # calculated from DHIS2 on every sync but silently dropped
    # (skipped_unmapped) before ever reaching MNID. 6 match an existing MNID
    # indicator by label/concept; the remaining 21 (EmONC signal functions,
    # obstetric complications, postnatal-care checks) have no MAHIS-side
    # counterpart, so they get new MNID ids below, same pattern as the
    # original 3 DHIS2-only births indicators.
    'screened_for_anaemia': 'mnid_anc_core_001',
    'women_with_imminent_preterm_birth_receiving_acs': 'mnid_lab_prog_009',
    'early_initiation_of_breastfeeding_within_1_hour_of_birth': 'mnid_pnc_prog_007',
    'pre_eclampsia_eclampsia_receiving_magnesium_sulphate': 'mnid_lab_prog_013',
    'signal_caesarean_section': 'mnid_lab_prog_006',
    'obstetric_complication_maternal_sepsis': 'mnid_lab_core_004',

    'obstetric_complication_pph': 'mnid_lab_core_pph',
    'obstetric_complication_eclampsia': 'mnid_lab_core_eclampsia',
    'obstetric_complication_obstructed_labour': 'mnid_lab_core_obstructedlabour',
    # 2026-08 workbook refresh -- same clean data_element_operand pattern
    # (RS3PtRniRns.<coc>) as its 3 siblings above, not the ambiguous bare
    # data_element total that got labour_complications excluded.
    'obstetric_complication_ruptured_uterus': 'mnid_lab_core_ruptureduterus',
    # Same 2026-08 refresh -- these 5 are the individual data elements that
    # 'neonatal_complications_at_birth' (below) already sums into one total;
    # publishing them separately too breaks that total down by complication
    # type (asphyxia/prematurity/sepsis/low birth weight/other) at no extra
    # DHIS2 request cost, since the parent sum already pulls the same 5 dx.
    'rhd_mat_newborn_complications_asphyxia': 'mnid_nb_core_complicationasphyxia',
    'rhd_mat_newborn_complications_prematurity': 'mnid_nb_core_complicationprematurity',
    'rhd_mat_newborn_complications_sepsis': 'mnid_nb_core_complicationsepsis',
    'rhd_mat_newborn_complications_weight_2500g': 'mnid_nb_core_complicationweight2500g',
    'rhd_mat_newborn_complications_othe': 'mnid_nb_core_complicationother',
    'signal_parenteral_antibiotics': 'mnid_lab_core_signalantibiotics',
    'signal_anticonvulsants_mgso4': 'mnid_lab_core_signalmgso4',
    'signal_oxytocics': 'mnid_lab_core_signaloxytocics',
    'signal_manual_placenta_removal': 'mnid_lab_core_signalplacenta',
    'signal_mva_retained_products': 'mnid_lab_core_signalmva',
    'signal_assisted_vaginal_delivery': 'mnid_lab_core_signalassisted',
    'signal_blood_transfusion': 'mnid_lab_core_signalbloodtransfusion',
    'mothers_with_postnatal_complications': 'mnid_pnc_core_motherscomplications',
    'babies_with_postnatal_complications': 'mnid_nb_core_babiescomplications',
    'mothers_checked_within_7_days': 'mnid_pnc_core_mocheck7d',
    'babies_checked_within_7_days': 'mnid_nb_core_babycheck7d',
    'mothers_checked_at_6_weeks': 'mnid_pnc_core_mocheck6wk',
    'babies_checked_at_6_weeks': 'mnid_nb_core_babycheck6wk',
    'immediate_postpartum_family_planning': 'mnid_pnc_core_fp',
    'hiv_positive_postnatal_mothers': 'mnid_pnc_core_hivpositive',
    'hiv_exposed_babies_on_art_prophylaxis': 'mnid_nb_core_hivartprophylaxis',
    'babies_who_received_bcg': 'mnid_nb_core_bcg',
    'babies_who_received_polio_0': 'mnid_nb_core_polio0',

    # 9 of the 15 indicators newly mapped in the 2026-07 workbook refresh
    # (mnid/dhis2/config/source/Data Mapping - MNH dashboard - DHIS2.xlsx).
    # 6 more were mapped in that refresh but are deliberately NOT wired in
    # here:
    # - iKMC Initiated / KMC support recorded share one DHIS2 id
    #   (zANGFtNoUEt) and Exposed babies on ART prophylaxis shares Live
    #   Births' id (iBBnHx1Uf50) - all three need data-team confirmation of
    #   which mapping is actually correct.
    # - Data Completeness % sums 12 already-computed DHIS2 reporting-rate
    #   values, which calculate_indicators() can only sum as raw counts -
    #   meaningless without hand-authored treatment this pipeline doesn't do.
    # - Labour Complications (RS3PtRniRns, no category-option-combo) and HIV
    #   positive on ART in Labour (MoximPvXuj6) were published once (2026-07-28)
    #   and found to return ~100-105% of total_births - a data element total
    #   across ALL its category option combos (the 4 sibling "Obstetric
    #   complication: X" indicators already use specific COCs on this same
    #   base id: RS3PtRniRns.<coc>) rather than the specific numerator the
    #   workbook's label claims, so the published values were removed from
    #   the aggregate. Needs the correct COC (or a real per-status data
    #   element for HIV-on-ART) from the data team before re-publishing.
    # All 6 stay in indicators.json (for visibility) but are absent here and
    # from sample_sync.py's INDICATOR_GROUPS, so none of them are ever
    # requested from DHIS2 or published, regardless of their "enabled" flag.
    'neonatal_admissions': 'mnid_nb_core_admissions',
    'neonatal_complications_at_birth': 'mnid_nb_core_complicationsatbirth',
    'resuscitation_intervention_recorded': 'mnid_nb_core_resuscitation',
    'low_birthweight_newborns': 'mnid_nb_core_lowbirthweight',
    'birth_asphyxia_among_newborn_admissions': 'mnid_nb_core_asphyxia',
    'neonatal_sepsis_among_newborn_admissions': 'mnid_nb_core_sepsis',
    'clients_referred_to_another_facility': 'mnid_lab_core_referred',
    'pnc_baby_checked_within_48hrs': 'mnid_nb_core_babycheck48h',
    'pnc_mother_checked_within_48hrs': 'mnid_pnc_core_mocheck48h',
}

# (label, category, target) for each MNID id above -- category drives which
# MNID tab (ANC/Labour/PNC/Newborn) an indicator appears under; the 3 DHIS2-only
# ones have no MAHIS definition to pull this from, so it's given here directly.
MNID_META = {
    'mnid_lab_overview_004': ('Live Births', 'Labour', 95),
    'mnid_lab_core_totalbirths': ('Total Births', 'Labour', 0),
    'mnid_lab_core_freshstillbirths': ('Fresh Stillbirths', 'Labour', 0),
    'mnid_lab_core_maceratedstillbirths': ('Macerated Stillbirths', 'Labour', 0),
    'mnid_pnc_overview_004': ('Maternal Deaths', 'PNC', 1),
    'mnid_nb_overview_002': ('Neonatal Deaths', 'Newborn', 5),
    'mnid_lab_overview_005': ('Stillbirths', 'Labour', 5),
    'mnid_anc_overview_001': ('ANC Visits', 'ANC', 80),
    'mnid_anc_prog_007': ('Blood pressure measured', 'ANC', 80),
    'mnid_anc_prog_006': ('Tested for HIV', 'ANC', 80),
    'mnid_anc_core_002': ('Screened for syphilis', 'ANC', 80),
    'mnid_anc_prog_008': ('At least 4 ANC contacts', 'ANC', 80),
    'mnid_anc_prog_004': ('Tetanus doses (2+)', 'ANC', 80),
    'mnid_anc_moh_002': ('New ANC registrations', 'ANC', 80),
    'mnid_anc_moh_011': ('Started ANC in first trimester (0-12 weeks)', 'ANC', 40),
    'mnid_anc_moh_015': ('Received 120+ FeFo tablets', 'ANC', 60),
    'mnid_anc_moh_016': ('Received ITN during ANC', 'ANC', 80),
    'mnid_lab_core_001': ('Uterotonic given after birth', 'Labour', 80),
    'mnid_lab_prog_012': ('Newborns not breathing at birth receiving bag-mask ventilation', 'Labour', 80),
    'mnid_pnc_prog_011': ('Vitamin K at birth', 'PNC', 80),
    'mnid_lab_prog_003': ('Facility deliveries', 'Labour', 80),
    'mnid_lab_moh_001': ('Delivered at this facility', 'Labour', 80),
    'mnid_lab_moh_002': ('Delivered at home or in transit', 'Labour', 5),
    'mnid_lab_moh_005': ('Delivered by skilled attendant', 'Labour', 80),
    'mnid_lab_moh_006': ('Normal vaginal delivery', 'Labour', 60),

    # 6 of the 27 newly-selected DHIS2 indicators (see DHIS2_TO_MNID_ID) match
    # an existing MNID indicator -- label/category/target copied from that
    # indicator's own MAHIS-mode definition in validated_dashboard.json so the
    # two sources describe the same thing consistently.
    'mnid_anc_core_001': ('Screened for anaemia', 'ANC', 80),
    'mnid_lab_prog_009': ('Women with imminent preterm birth receiving ACs', 'Labour', 80),
    'mnid_pnc_prog_007': ('Early initiation of breastfeeding within 1 hour of birth', 'PNC', 80),
    'mnid_lab_prog_013': ('Pre-eclampsia/eclampsia receiving magnesium sulphate', 'Labour', 80),
    'mnid_lab_prog_006': ('Overall caesarean section rate', 'Labour', 15),
    'mnid_lab_core_004': ('Maternal sepsis rate', 'Labour', 10),

    # 21 with no MAHIS-side counterpart (EmONC signal functions, obstetric
    # complications, postnatal-care checks) -- new MNID entries, no MAHIS
    # numerator_filters/denominator_filters defined, same pattern as the
    # original 3 births indicators. Complication/signal-function rates get
    # target=0 (monitor, no fixed threshold); genuine coverage checks get a
    # standard MOH-style target.
    'mnid_lab_core_pph': ('Obstetric complication: PPH', 'Labour', 0),
    'mnid_lab_core_eclampsia': ('Obstetric complication: Eclampsia', 'Labour', 0),
    'mnid_lab_core_obstructedlabour': ('Obstetric complication: Obstructed labour', 'Labour', 0),
    'mnid_lab_core_ruptureduterus': ('Obstetric complication: Ruptured uterus', 'Labour', 0),
    # 5 breakdown components of 'Neonatal Complications at Birth' -- same
    # target=0 monitor convention as the count above, no workbook-stated rate.
    'mnid_nb_core_complicationasphyxia': ('Newborn complication: Asphyxia', 'Newborn', 0),
    'mnid_nb_core_complicationprematurity': ('Newborn complication: Prematurity', 'Newborn', 0),
    'mnid_nb_core_complicationsepsis': ('Newborn complication: Sepsis', 'Newborn', 0),
    'mnid_nb_core_complicationweight2500g': ('Newborn complication: Weight < 2500g', 'Newborn', 0),
    'mnid_nb_core_complicationother': ('Newborn complication: Other', 'Newborn', 0),
    'mnid_lab_core_signalantibiotics': ('Signal: Parenteral antibiotics', 'Labour', 0),
    'mnid_lab_core_signalmgso4': ('Signal: Anticonvulsants (MgSO4)', 'Labour', 0),
    'mnid_lab_core_signaloxytocics': ('Signal: Oxytocics', 'Labour', 0),
    'mnid_lab_core_signalplacenta': ('Signal: Manual placenta removal', 'Labour', 0),
    'mnid_lab_core_signalmva': ('Signal: MVA / retained products', 'Labour', 0),
    'mnid_lab_core_signalassisted': ('Signal: Assisted vaginal delivery', 'Labour', 0),
    'mnid_lab_core_signalbloodtransfusion': ('Signal: Blood transfusion', 'Labour', 0),
    'mnid_pnc_core_motherscomplications': ('Mothers with postnatal complications', 'PNC', 0),
    'mnid_nb_core_babiescomplications': ('Babies with postnatal complications', 'Newborn', 0),
    'mnid_pnc_core_mocheck7d': ('Mothers checked within 7 days', 'PNC', 80),
    'mnid_nb_core_babycheck7d': ('Babies checked within 7 days', 'Newborn', 80),
    'mnid_pnc_core_mocheck6wk': ('Mothers checked at 6 weeks', 'PNC', 80),
    'mnid_nb_core_babycheck6wk': ('Babies checked at 6 weeks', 'Newborn', 80),
    'mnid_pnc_core_fp': ('Immediate postpartum family planning', 'PNC', 60),
    'mnid_pnc_core_hivpositive': ('HIV positive postnatal mothers', 'PNC', 0),
    'mnid_nb_core_hivartprophylaxis': ('HIV exposed babies on ART prophylaxis', 'Newborn', 90),
    'mnid_nb_core_bcg': ('Babies who received BCG', 'Newborn', 90),
    'mnid_nb_core_polio0': ('Babies who received Polio 0', 'Newborn', 90),

    # 2026-07 workbook refresh (9 wired ids, see DHIS2_TO_MNID_ID above) --
    # plain event/complication counts get target=0 (monitor, no fixed
    # threshold), same convention as the obstetric-complication and signal-
    # function counts above; the two 48-hour PNC checks get target=80,
    # matching their 7-day/6-week siblings immediately above.
    'mnid_nb_core_admissions': ('Neonatal Admissions', 'Newborn', 0),
    'mnid_nb_core_complicationsatbirth': ('Neonatal Complications at Birth', 'Newborn', 0),
    'mnid_nb_core_resuscitation': ('Resuscitation intervention recorded', 'Newborn', 0),
    'mnid_nb_core_lowbirthweight': ('Low birthweight newborns', 'Newborn', 0),
    'mnid_nb_core_asphyxia': ('Birth asphyxia among newborn admissions', 'Newborn', 0),
    'mnid_nb_core_sepsis': ('Neonatal sepsis among newborn admissions', 'Newborn', 0),
    'mnid_lab_core_referred': ('Clients referred to another facility', 'Labour', 0),
    'mnid_nb_core_babycheck48h': ('PNC baby checked within 48hrs', 'Newborn', 80),
    'mnid_pnc_core_mocheck48h': ('PNC Mother checked within 48hrs', 'PNC', 80),
}

# Every one of the 25 indicators above is value_type='count' in DHIS2's own
# config (mnid/dhis2/config/indicators.json) -- none use operation='percentage',
# so calculate_indicators() never populates a real numerator/denominator for
# any of them. Without this map, every single indicator falls through to the
# count-only branch below (numerator=denominator=count, pct=100%), which reads
# as "100% coverage" for things like Stillbirths -- meaningless. Pairs each
# count with the DHIS2-native id of the appropriate denominator indicator
# already pulled in the same sync, using standard MNH indicator conventions
# (birth-outcome indicators over total births, newborn-care indicators over
# live births, ANC-service indicators over ANC registrations). The 3 indicators
# absent from this map (total_births, new_anc_registrations, anc_visits) are
# themselves the "whole" with no better denominator in this dataset, so they
# keep the count-only convention (100% = fully reported, not a rate).
PCT_DENOMINATOR = {
    'live_births': 'total_births',
    'fresh_stillbirths': 'total_births',
    'macerated_stillbirths': 'total_births',
    'stillbirths': 'total_births',
    'maternal_deaths': 'live_births',
    'neonatal_deaths': 'live_births',
    'blood_pressure_measured': 'new_anc_registrations',
    'tested_for_hiv': 'new_anc_registrations',
    'screened_for_syphilis': 'new_anc_registrations',
    'at_least_4_anc_contacts': 'new_anc_registrations',
    'tetanus_doses_2': 'new_anc_registrations',
    'started_anc_in_first_trimester_0_12_weeks': 'new_anc_registrations',
    'received_120_fefo_tablets': 'new_anc_registrations',
    'received_itn_during_anc': 'new_anc_registrations',
    'uterotonic_given_after_birth': 'total_births',
    'newborns_not_breathing_at_birth_receiving_bag_mask_ventilation': 'live_births',
    'vitamin_k_at_birth': 'live_births',
    'facility_deliveries': 'total_births',
    'delivered_at_this_facility': 'total_births',
    'delivered_at_home_or_in_transit': 'total_births',
    'delivered_by_skilled_attendant': 'total_births',
    'normal_vaginal_delivery': 'total_births',

    # 'women_with_imminent_preterm_birth_receiving_acs' and
    # 'pre_eclampsia_eclampsia_receiving_magnesium_sulphate' are deliberately
    # absent here -- both are value_type='percentage'/operation='percentage'
    # in DHIS2's own config, so calculate_indicators() already gives them a
    # real numerator/denominator; the percentage branch below handles them.
    'screened_for_anaemia': 'new_anc_registrations',
    'early_initiation_of_breastfeeding_within_1_hour_of_birth': 'live_births',
    'signal_caesarean_section': 'total_births',
    'obstetric_complication_maternal_sepsis': 'total_births',
    'obstetric_complication_pph': 'total_births',
    'obstetric_complication_eclampsia': 'total_births',
    'obstetric_complication_obstructed_labour': 'total_births',
    'obstetric_complication_ruptured_uterus': 'total_births',
    'signal_parenteral_antibiotics': 'total_births',
    'signal_anticonvulsants_mgso4': 'total_births',
    'signal_oxytocics': 'total_births',
    'signal_manual_placenta_removal': 'total_births',
    'signal_mva_retained_products': 'total_births',
    'signal_assisted_vaginal_delivery': 'total_births',
    'signal_blood_transfusion': 'total_births',
    'mothers_with_postnatal_complications': 'live_births',
    'babies_with_postnatal_complications': 'live_births',
    'mothers_checked_within_7_days': 'live_births',
    'babies_checked_within_7_days': 'live_births',
    'mothers_checked_at_6_weeks': 'live_births',
    'babies_checked_at_6_weeks': 'live_births',
    'immediate_postpartum_family_planning': 'live_births',
    'hiv_positive_postnatal_mothers': 'live_births',
    # HIV-exposed babies are, by definition, babies born to HIV+ mothers --
    # using that count as the denominator (rather than all live births) keeps
    # this a true prophylaxis-coverage rate among the exposed population.
    'hiv_exposed_babies_on_art_prophylaxis': 'hiv_positive_postnatal_mothers',
    'babies_who_received_bcg': 'live_births',
    'babies_who_received_polio_0': 'live_births',

    # 2026-07 workbook refresh -- the two 48-hour PNC checks follow their
    # 7-day/6-week siblings above (rate over live_births). The two "among
    # newborn admissions" indicators name their own denominator in the
    # indicator label itself, so pairing them with the new
    # neonatal_admissions count (rather than live_births) is what the name
    # actually means, not an assumption. The other 7 new ids (Neonatal
    # Admissions, Labour Complications, Neonatal Complications at Birth,
    # Resuscitation intervention recorded, Low birthweight newborns, HIV
    # positive on ART in Labour, Clients referred) have no workbook-stated
    # rate relationship, so they stay pure counts like total_births.
    'pnc_baby_checked_within_48hrs': 'live_births',
    'pnc_mother_checked_within_48hrs': 'live_births',
    'birth_asphyxia_among_newborn_admissions': 'neonatal_admissions',
    'neonatal_sepsis_among_newborn_admissions': 'neonatal_admissions',
}

DHIS2_ROUTE = 'dhis2'

# DHIS2's org-unit hierarchy names districts "<Name>-DHO" and reports the four
# central/referral hospitals as their own top-level units instead of folding
# them into the district they physically sit in. MAHIS's district dropdown
# (what scope_meta['selected_districts'] and every other MNID view actually
# filter against) uses plain names, never splits a district, and has its own
# distinct "Mzuzu Central" entry - so writing DHIS2's raw district string here
# would make every district filter silently match nothing once this aggregate
# is read back through the same store.py code path MAHIS routes use.
_DHIS2_DISTRICT_TO_MAHIS = {
    'Kamuzu Central Hospital': 'Lilongwe',
    'Mzuzu Central Hospital': 'Mzuzu Central',
    'Queen Elizabeth Central Hospital': 'Blantyre',
    'Zomba Central Hospital': 'Zomba',
    'Mzimba-North-DHO': 'Mzimba',
    'Mzimba-South-DHO': 'Mzimba',
    'Nkhata-Bay-DHO': 'Nkhata Bay',
}


def _mahis_district(dhis2_district: str) -> str:
    value = str(dhis2_district or '').strip()
    if value in _DHIS2_DISTRICT_TO_MAHIS:
        return _DHIS2_DISTRICT_TO_MAHIS[value]
    if value.endswith('-DHO'):
        return value[:-len('-DHO')]
    return value


_facility_code_to_district_cache: dict[str, str] | None = None


def _facility_code_to_district() -> dict[str, str]:
    global _facility_code_to_district_cache
    if _facility_code_to_district_cache is None:
        from mnid.core.dhis2_facilities import dhis2_code_to_district
        _facility_code_to_district_cache = dhis2_code_to_district()
    return _facility_code_to_district_cache


def _write_facility_coverage_report(selected_units: list[dict]) -> None:
    """Write which org units this sync actually returned data for, and
    whether each one resolved to a named (crosswalk) facility_code.

    Ground truth for the recurring "why are so many facilities blank/zero"
    question -- rather than guessing at it turn after turn, every publish
    run now leaves data/mnid_aggregates/dhis2/facility_coverage_report.json
    showing exactly which reporting org units matched the curated 67-facility
    list (data/geo/facilities_dhis2.json) and which didn't, so an unmatched
    org unit can be looked up by name/district and either added to the
    crosswalk or confirmed as a facility outside its current 67-facility
    scope -- instead of silently disappearing as a blank facility_code.
    """
    matched = [u for u in selected_units if u.get('local_facility_code')]
    unmatched = [u for u in selected_units if not u.get('local_facility_code')]
    report = {
        'generated_at': datetime.utcnow().isoformat(),
        'org_units_seen': len(selected_units),
        'matched_crosswalk_facility_code': len(matched),
        'unmatched_no_facility_code': len(unmatched),
        'matched': sorted(
            [
                {
                    'org_unit_id': u.get('org_unit_id'),
                    'name': u.get('name'),
                    'district': u.get('district'),
                    'local_facility_code': u.get('local_facility_code'),
                }
                for u in matched
            ],
            key=lambda r: (r['district'] or '', r['name'] or ''),
        ),
        'unmatched': sorted(
            [
                {
                    'org_unit_id': u.get('org_unit_id'),
                    'name': u.get('name'),
                    'district': u.get('district'),
                }
                for u in unmatched
            ],
            key=lambda r: (r['district'] or '', r['name'] or ''),
        ),
    }
    out_path = _PROJECT_ROOT / 'data' / 'mnid_aggregates' / DHIS2_ROUTE / 'facility_coverage_report.json'
    try:
        atomic_json(out_path, report)
    except Exception as exc:
        _LOG.warning('Failed to write facility coverage report: %s', exc)


def _bridge_credentials_from_config() -> None:
    """Populate MNH_DHIS2_* env vars from config.py if not already set in the
    environment. Keeps mnid.dhis2.settings env-only (per its own security
    posture) while letting config.py be this app's actual source of truth.
    """
    import config as cfg
    os.environ.setdefault('MNH_DHIS2_BASE_URL', getattr(cfg, 'MNH_DHIS2_BASE_URL', ''))
    os.environ.setdefault('MNH_DHIS2_USERNAME', getattr(cfg, 'MNH_DHIS2_USERNAME', ''))
    os.environ.setdefault('MNH_DHIS2_PASSWORD', getattr(cfg, 'MNH_DHIS2_PASSWORD', ''))


def publish_mnid_aggregate() -> dict:
    """Pull the working 25-indicator DHIS2 sample and publish it under MNID
    indicator ids into data/mnid_aggregates/dhis2/indicator_aggregates.parquet.
    """
    _bridge_credentials_from_config()
    settings = DHIS2Settings.from_env(require_credentials=True)
    periods = monthly_periods(settings.start_period, settings.end_period)
    mapping = _selected_mapping()
    requested_dx = atomic_dx_values(mapping)
    run_id = uuid.uuid4().hex

    # A bare 'ou:LEVEL-4' wildcard resolves relative to the API user's own
    # org-unit scope in DHIS2, not the whole system -- confirmed by direct
    # query, that returned only 5 org units total for this account (Central
    # Hospital sub-departments), even though all 67 crosswalk facilities have
    # real data for the same period, same indicators. Requesting the 67
    # crosswalk facilities' DHIS2 IDs explicitly bypasses that scoping
    # entirely. See dhis2_known_org_unit_ids()'s docstring for the full story.
    from mnid.core.dhis2_facilities import dhis2_known_org_unit_ids
    org_units = dhis2_known_org_unit_ids()

    values, rejected = [], []
    with DHIS2Client(settings) as client:
        for request_number, dx_batch in enumerate(_chunks(requested_dx, settings.dx_batch_size), 1):
            payload = client.analytics(
                dx_batch, periods, org_units,
                sync_run_id=run_id, request_id=f'mnid-publish-{request_number:04d}',
            )
            parsed, batch_rejected = parse_analytics_response(payload)
            values.extend(parsed)
            rejected.extend(batch_rejected)
    if rejected:
        raise RuntimeError(f'Publish response contained {len(rejected)} rejected rows')

    org_units_path = Path(__file__).resolve().parent / 'config' / 'organisation_units.json'
    units = json.loads(org_units_path.read_text(encoding='utf-8'))['organisation_units']
    # NOTE: not filtered to level=='facility' -- the 3 Central Hospitals sit
    # at level 3 (their own top-level grouping, not a District Health
    # Office), and their DHIS2 IDs are requested explicitly above via
    # dhis2_known_org_unit_ids(), so they need to resolve here too regardless
    # of level.
    by_id = {u['org_unit_id']: u for u in units if u.get('org_unit_id')}
    seen_unit_ids = sorted({v.org_unit_id for v in values})
    from mnid.core.dhis2_facilities import dhis2_resolve_ancestor_facility_code
    selected_units = []
    for uid in seen_unit_ids:
        unit = dict(by_id.get(uid) or {'org_unit_id': uid, 'name': uid, 'district': None, 'local_facility_code': None})
        if not unit.get('local_facility_code'):
            # Our own structure is 67 named facilities under 3 districts --
            # whatever DHIS2 reports this org unit as (a department, ward, or
            # other sub-unit below a named facility, not just the level-3
            # Central Hospital case above), walk up its parent chain to the
            # nearest ancestor that IS one of our named facilities, so data
            # attaches to that facility instead of vanishing with a blank
            # facility_code. Handles any future sub-unit DHIS2 introduces,
            # not just the ones seen and hand-mapped so far.
            unit['local_facility_code'] = dhis2_resolve_ancestor_facility_code(uid)
        selected_units.append(unit)

    _write_facility_coverage_report(selected_units)

    atomic = {(v.dx, v.period, v.org_unit_id): Decimal(v.raw_value) for v in values}
    calculated = calculate_indicators(mapping, atomic, periods, selected_units)

    # (dhis2_indicator_id, period, org_unit_id) -> value, so PCT_DENOMINATOR
    # lookups can pull the paired denominator indicator's value for the same
    # period/org unit without a second pass over the DHIS2 API.
    value_by_key = {
        (r['indicator_id'], r['period'], r['org_unit_id']): r['value'] for r in calculated
    }

    rows = []
    skipped_unmapped = 0
    for record in calculated:
        if record['value'] is None:
            continue
        dhis2_id = record['indicator_id']
        mnid_id = DHIS2_TO_MNID_ID.get(dhis2_id)
        if mnid_id is None:
            skipped_unmapped += 1
            continue
        label, category, target = MNID_META[mnid_id]
        value = float(record['value'])
        denom_dhis2_id = PCT_DENOMINATOR.get(dhis2_id)
        if denom_dhis2_id:
            denom_value = value_by_key.get((denom_dhis2_id, record['period'], record['org_unit_id']))
            numerator = int(round(value))
            if denom_value:
                denominator = int(round(float(denom_value)))
                pct = round(min(numerator / denominator * 100, 100.0), 1) if denominator else 0.0
            else:
                # denominator indicator has no data for this period/org unit --
                # no-data, not a fabricated 100%.
                denominator = 0
                pct = 0.0
        elif record.get('value_type') == 'percentage' and record.get('numerator') is not None and record.get('denominator'):
            # true numerator/denominator pair (e.g. a screening rate)
            numerator = int(record['numerator'])
            denominator = int(record['denominator'])
            pct = round(min(numerator / denominator * 100, 100.0), 1) if denominator else 0.0
        else:
            # count-only indicator with no sensible in-dataset denominator (e.g.
            # Total Births, the base of most ratios above): the count *is* the
            # numerator, denominator=numerator so it reads as "fully reported"
            # rather than a fabricated rate.
            numerator = int(round(value))
            denominator = numerator
            pct = 100.0 if numerator else 0.0
        facility_code = record.get('facility_code') or ''
        # The crosswalk (data/geo/facilities_dhis2.json) is our own facility
        # structure's authoritative district for a named facility -- prefer
        # it whenever facility_code resolved, rather than deriving district
        # from DHIS2's own org-unit hierarchy naming (_mahis_district below),
        # which disagreed for Mzuzu Central Hospital: DHIS2 models it as its
        # own top-level unit named "Mzuzu Central Hospital", which
        # _mahis_district read as district "Mzuzu Central" -- not a real,
        # selectable district -- while the crosswalk correctly has it under
        # Mzimba. Falls back to _mahis_district only for the (should be rare/
        # none, per dhis2_known_org_unit_ids()) case of an org unit that
        # returned data without resolving to one of the 67 named facilities.
        crosswalk_district = _facility_code_to_district().get(facility_code) if facility_code else None
        district = crosswalk_district or _mahis_district(record.get('district'))
        rows.append({
            'indicator_id': mnid_id,
            'indicator_label': label,
            'category': category,
            'target': target,
            'facility_code': facility_code,
            'district': district,
            'grain': 'monthly',
            'period_start': pd.Timestamp(period_start_date(record['period'])),
            'numerator': numerator,
            'denominator': denominator,
            'pct': pct,
        })

    out_dir = _PROJECT_ROOT / 'data' / 'mnid_aggregates' / DHIS2_ROUTE
    parquet_out = out_dir / 'indicator_aggregates.parquet'
    meta_out = out_dir / 'meta.json'

    # atomic_parquet/atomic_json (mnid/dhis2/storage.py) write to a temp file and
    # os.replace() into place -- matches the atomic-publish guarantee the rest of
    # this package relies on; a failed/partial run can't corrupt the last good file.
    atomic_parquet(parquet_out, rows)

    unique_indicators = {r['indicator_id'] for r in rows}
    unique_periods = {r['period_start'] for r in rows}
    unique_facility_codes = {r['facility_code'] for r in rows if r['facility_code']}
    meta = {
        'generated_at': datetime.utcnow().isoformat(),
        'rows': len(rows),
        'indicators': len(unique_indicators),
        'grains': ['monthly'],
        'data_source': 'dhis2',
        'use_demo_data': False,
        'last_run_status': 'ok',
        'sync_run_id': run_id,
        'mapping_version': mapping.get('mapping_version'),
        'period_start': settings.start_period,
        'period_end': settings.end_period,
        'skipped_unmapped_dhis2_indicators': skipped_unmapped,
    }
    atomic_json(meta_out, meta)

    return {
        'rows': len(rows),
        'mnid_indicators': len(unique_indicators),
        'periods': len(unique_periods),
        'facilities_with_code': len(unique_facility_codes),
        'output': str(parquet_out),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = publish_mnid_aggregate()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
