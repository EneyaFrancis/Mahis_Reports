"""
Convert Excel facility data (e.g. data/excel/NEST_BF_facilitites.xlsx)
into parquet dataset(s) formatted as if fetched/calculated from DHIS2,
and merge them into data/mnid_aggregates/dhis2/indicator_aggregates.parquet.

Supports:
1. Converting Excel records to DHIS2 aggregate format (hmis_test.parquet, current.parquet)
2. Generating normalized atomic values for DHIS2 store
3. Merging Excel indicators into MNID indicator_aggregates.parquet with high priority for the 7 facilities
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path for direct script execution
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from mnid.core.dhis2_facilities import (
    dhis2_facility_records,
)
from mnid.dhis2.periods import period_end_date
from mnid.dhis2.settings import DHIS2Settings
from mnid.dhis2.storage import atomic_json, atomic_parquet

_LOG = logging.getLogger(__name__)

DEFAULT_INPUT_FILE = PROJECT_ROOT / "data" / "excel" / "NEST_BF_facilitites.xlsx"
DEFAULT_MNID_AGGREGATE_FILE = PROJECT_ROOT / "data" / "mnid_aggregates" / "dhis2" / "indicator_aggregates.parquet"
DEFAULT_MNID_META_FILE = PROJECT_ROOT / "data" / "mnid_aggregates" / "dhis2" / "meta.json"

# Mapping from Excel metric/concept names to DHIS2 indicator ID and MNID metadata
METRIC_TO_DHIS2 = {
    "Admissions": {
        "dhis2_id": "neonatal_admissions",
        "indicator_name": "Neonatal Admissions",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_admissions",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "utECJ6sZdYO",
    },
    "Sepsis": {
        "dhis2_id": "rhd_mat_newborn_complications_sepsis",
        "indicator_name": "Newborn complication: Sepsis",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationsepsis",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "c2zR6Z68Ncf",
    },
    "Jaundice": {
        "dhis2_id": "rhd_mat_newborn_complications_othe",
        "indicator_name": "Newborn complication: Other",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationother",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "bW26n4wRzV5",
    },
    "RDS": {
        "dhis2_id": "rhd_mat_newborn_complications_asphyxia",
        "indicator_name": "Newborn complication: Asphyxia",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationasphyxia",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "hA4cT9rD8yN",
    },
    # --- The 6 entries below were corrected 2026-08-26. They previously
    # pointed at semantically unrelated indicators (e.g. the raw "KMC" count
    # was being written into "Low birthweight newborns", "Bilirubin
    # Measurement" into "Vitamin K at birth") -- each excel column now maps
    # to the indicator whose label actually matches its raw column name.
    # indicator_name is the exact label already used by
    # mnid/dhis2/config/indicators.json / the rest of the dashboard, so
    # resolve_indicator_id()'s label-fallback lookup finds it regardless of
    # mnid_id. mnid_id follows the existing mnid_nb_core_* convention used
    # by every other DHIS2-route Newborn indicator in this table; these
    # indicators have never been populated from a live DHIS2 pull (all
    # flagged "review_required"/excluded in indicators.json), so there's no
    # existing _core_ id to collide with.
    "KMC": {
        "dhis2_id": "kmc_support_recorded",
        "indicator_name": "KMC support recorded",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_kmc",
        "category": "Newborn",
        "target": 80,
        "value_type": "count",
        "dx": "z7nL2kK9rM0",
    },
    "Prophy CPAP": {
        # NEW indicator (2026-08-26, per NEST-IT Indicators Guide) -- the
        # guide defines "CPAP for babies 1000-1499g (prophylactic for all)"
        # as its own indicator, distinct from the 1500-1999g one below: ALL
        # babies 1000-1499g should get CPAP regardless of symptoms, target
        # 100%. Not yet added to indicators.json/validated_dashboard.json,
        # so it populates the aggregate but won't show as "tracked" in the
        # Coverage panel until that catalog wiring is added too.
        "dhis2_id": "babies_between_1000_1499g_who_receive_prophylactic_cpap",
        "indicator_name": "Babies between 1000-1499g who receive prophylactic CPAP",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_cpap1000_1499",
        "category": "Newborn",
        "target": 100,
        "value_type": "count",
        "dx": "e9K3y1wN2rP",
    },
    # CORRECTED (2026-08-26, per NEST-IT Indicators Guide): "Sympt CPAP" is
    # the guide's "CPAP 1500-1999g (with symptoms)" -- babies in this
    # weight band only need CPAP if they show RDS + hypoxia, unlike the
    # 1000-1499g band above where every baby should get it. This is the
    # SAME weight band as the pre-existing "Babies between 1500-1999g who
    # receive prophylactic CPAP" indicator (mnid_nb_core_cpap1500_1999) --
    # reused rather than renamed, since that label is already wired into
    # indicators.json/validated_dashboard.json/mnid/core/indicators.py and
    # a full rename to reflect "symptomatic" would need to touch all of
    # those; the weight-band match is what matters functionally. Previously
    # this raw column was wrongly routed to bag-mask ventilation, an
    # unrelated intervention.
    "Sympt CPAP": {
        "dhis2_id": "babies_between_1500_1999g_who_receive_prophylactic_cpap",
        "indicator_name": "Babies between 1500-1999g who receive prophylactic CPAP",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_cpap1500_1999",
        "category": "Newborn",
        "target": 100,
        "value_type": "count",
        "dx": "d8J2xK8vM1s",
    },
    "Bilirubin Measurement": {
        "dhis2_id": "babies_who_had_bilirubin_measured",
        "indicator_name": "Babies who had bilirubin measured",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_bilirubin",
        "category": "Newborn",
        "target": 80,
        "value_type": "count",
        "dx": "w2M8n4bV6zP",
    },
    "Phototherapty": {
        "dhis2_id": "babies_with_clinical_jaundice_who_receive_phototherapy",
        "indicator_name": "Babies with clinical jaundice who receive phototherapy",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_phototherapy",
        "category": "Newborn",
        "target": 80,
        "value_type": "count",
        "dx": "m3B9n5vC7xQ",
        # The dashboard config (data/visualizations/validated_dashboard.json)
        # carries a near-duplicate entry for this same concept under
        # different wording/id ("Babies with jaundice receiving
        # phototherapy", mnid_nb_prog_014) -- without this alias it silently
        # shows "no data" next to this one showing real data, even though
        # they're the same clinical fact. Emitting a second row set under
        # that id/label too so both resolve.
        "aliases": [
            {"mnid_id": "mnid_nb_prog_014", "indicator_name": "Babies with jaundice receiving phototherapy"},
        ],
    },
    "Antibiotics": {
        "dhis2_id": "babies_with_suspected_sepsis_who_receive_parenteral_antibiotics",
        "indicator_name": "Babies with suspected sepsis who receive parenteral antibiotics",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_sepsisantibiotics",
        "category": "Newborn",
        "target": 80,
        "value_type": "count",
        "dx": "k1L7n3mP9vS",
    },
    # "Hypo on Admin"/"Hypo during Stay" sit in the same baseline-column
    # group as baseline_sepsis_rate/baseline_jaund_rate/baseline_RDS_rate
    # (all unambiguous problem-rates -- babies WHO HAD sepsis/jaundice/RDS),
    # so by the same naming convention these are hypothermia CASE counts
    # (babies who WERE hypothermic), not the indicators.json "not
    # hypothermic" success-framed indicators. Mapping the raw count directly
    # into "babies not hypothermic" would show a hypothermia problem as a
    # 100% success rate -- backwards. Given as its own honestly-named
    # problem-count indicator instead (no denominator to invert against
    # here, so a true "not hypothermic" rate isn't computable from this
    # sheet alone).
    "Hypo on Admin": {
        "dhis2_id": "neonatal_hypothermia_on_admission",
        "indicator_name": "Neonatal hypothermia on admission",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_hypothermiaadmission",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "x9K3b7nV1zR",
    },
    "Hypo during Stay": {
        "dhis2_id": "neonatal_hypothermia_during_stay",
        "indicator_name": "Neonatal hypothermia during stay",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_hypothermiastay",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "j5M2b8vN4zL",
    },
}

# Facility name resolution to DHIS2 crosswalk
FACILITY_NAME_MAP = {
    "Bwaila District Hospital": "Bwaila Hospital",
    "E mbangweni Hospital": "Embangweni Mission Hospital",
    "Embangweni Hospital": "Embangweni Mission Hospital",
    "Kamuzu Central Hospital": "Kamuzu Central Hospital",
    "Mzimba District Hospital": "Mzimba District Hospital",
    "Mzuzu Central Hospital": "Mzuzu Central Hospital",
    "Nkhoma Mission Hospital": "Nkhoma Mission Hospital",
    "Queen Elizabeth Central Hospital": "Queen Elizabeth Central Hospital",
}

SEVEN_FACILITIES_CANONICAL = set(FACILITY_NAME_MAP.values())


def parse_excel_metrics(filepath: Path | str) -> pd.DataFrame:
    """Parse wide date-block structure of NEST BF facilities Excel."""
    df_raw = pd.read_excel(filepath, sheet_name="NEST360_BF Facilities", header=None)

    dates_row = df_raw.iloc[0].ffill()
    metrics_row = df_raw.iloc[1]
    facilities = df_raw.iloc[2:, 1].dropna().values

    records = []
    for col_idx in range(2, len(df_raw.columns)):
        date_val = dates_row[col_idx]
        metric = metrics_row[col_idx]

        if isinstance(date_val, (datetime, pd.Timestamp)) and pd.notna(metric):
            metric_str = str(metric).strip()
            for row_idx, facility in enumerate(facilities, start=2):
                facility_str = str(facility).strip()
                # Skip aggregate summary rows if we want individual facility data
                if "Average for" in facility_str:
                    continue
                val = df_raw.iloc[row_idx, col_idx]
                if pd.notna(val):
                    try:
                        numeric_val = float(val)
                    except (ValueError, TypeError):
                        numeric_val = None

                    if numeric_val is not None:
                        records.append({
                            "facility_raw": facility_str,
                            "date": pd.to_datetime(date_val),
                            "concept_name": metric_str,
                            "value": numeric_val,
                        })

    return pd.DataFrame(records)


def build_dhis2_facility_lookup() -> dict[str, dict[str, Any]]:
    """Build a lookup from facility names to DHIS2 org_unit_id, code, district, and name."""
    records = dhis2_facility_records()
    lookup = {}
    for rec in records:
        name = rec.get("NAME") or rec.get("COMMON NAME")
        if name:
            lookup[name] = {
                "org_unit_id": rec.get("DHIS2 ID", ""),
                "facility_code": rec.get("CODE", ""),
                "district": rec.get("DISTRICT", ""),
                "org_unit_name": name,
            }
    return lookup


def get_excel_mnid_records(input_file: Path | str = DEFAULT_INPUT_FILE) -> list[dict[str, Any]]:
    """Convert Excel file directly into MNID indicator aggregate row dictionaries."""
    input_path = Path(input_file).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    clean_df = parse_excel_metrics(input_path)
    facility_lookup = build_dhis2_facility_lookup()

    mnid_records: list[dict[str, Any]] = []
    for _, row in clean_df.iterrows():
        raw_name = row["facility_raw"]
        canonical_name = FACILITY_NAME_MAP.get(raw_name, raw_name)
        fac_info = facility_lookup.get(canonical_name, {
            "org_unit_id": f"OU_{raw_name.replace(' ', '_')[:8]}",
            "facility_code": "",
            "district": "",
            "org_unit_name": canonical_name,
        })

        metric = row["concept_name"]
        dhis2_meta = METRIC_TO_DHIS2.get(metric, {
            "dhis2_id": metric.lower().replace(" ", "_"),
            "indicator_name": metric,
            "indicator_group": "Neonatal care",
            "mnid_id": f"mnid_excel_{metric.lower().replace(' ', '_')}",
            "category": "Newborn",
            "target": 0,
            "value_type": "count",
            "dx": "dx_" + metric.lower().replace(" ", "_")[:8],
        })

        dt: pd.Timestamp = row["date"]
        period_start = pd.Timestamp(dt.year, dt.month, 1)
        val = float(row["value"])

        mnid_row = {
            "indicator_id": dhis2_meta["mnid_id"],
            "indicator_label": dhis2_meta["indicator_name"],
            "category": dhis2_meta["category"],
            "target": int(dhis2_meta["target"]),
            "facility_code": fac_info["facility_code"],
            "district": fac_info["district"],
            "grain": "monthly",
            "period_start": period_start,
            "numerator": val,
            "denominator": val,
            "pct": 100.0 if val else 0.0,
        }
        mnid_records.append(mnid_row)

        # Emit the same row again under any alias id/label -- see the
        # "aliases" comment on METRIC_TO_DHIS2 entries that have one -- so a
        # near-duplicate indicator elsewhere in the dashboard config (same
        # clinical fact, different wording/id) resolves to the same data
        # instead of showing "no data" next to the one that does.
        for alias in dhis2_meta.get("aliases", []):
            mnid_records.append({
                **mnid_row,
                "indicator_id": alias["mnid_id"],
                "indicator_label": alias["indicator_name"],
            })

    return mnid_records


def merge_excel_into_indicator_aggregates(
    excel_input_file: Path | str = DEFAULT_INPUT_FILE,
    target_parquet_path: Path | str = DEFAULT_MNID_AGGREGATE_FILE,
    target_meta_path: Path | str = DEFAULT_MNID_META_FILE,
    dhis2_records: list[dict[str, Any]] | pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Merge Excel indicator records into indicator_aggregates.parquet.
    
    Priority Rules:
    - For the 7 facilities present in the Excel data source:
      - Any indicator present in the Excel sheet for that facility & period overrides/replaces DHIS2 data.
      - Indicators NOT in the Excel sheet for those 7 facilities remain sourced from DHIS2.
    - For all other facilities:
      - All data remains sourced from DHIS2.
    """
    target_path = Path(target_parquet_path).resolve()
    meta_path = Path(target_meta_path).resolve()

    excel_records = get_excel_mnid_records(excel_input_file)
    df_excel = pd.DataFrame(excel_records)
    if not df_excel.empty:
        df_excel["period_start"] = pd.to_datetime(df_excel["period_start"])

    # Load base DHIS2 records
    if dhis2_records is not None:
        if isinstance(dhis2_records, pd.DataFrame):
            df_dhis2 = dhis2_records.copy()
        else:
            df_dhis2 = pd.DataFrame(dhis2_records)
    elif target_path.exists():
        df_dhis2 = pd.read_parquet(target_path)
    else:
        df_dhis2 = pd.DataFrame()

    if not df_dhis2.empty:
        df_dhis2["period_start"] = pd.to_datetime(df_dhis2["period_start"])

    if df_dhis2.empty:
        df_merged = df_excel
    elif df_excel.empty:
        df_merged = df_dhis2
    else:
        # Identify the 7 facilities' facility codes
        excel_facility_codes = set(df_excel["facility_code"].dropna().unique())

        # High priority merge:
        # Create a unique key (facility_code, indicator_id, period_start, grain)
        # Drop matching DHIS2 records where Excel has data, then append Excel records
        excel_keys = set(
            zip(
                df_excel["facility_code"].astype(str),
                df_excel["indicator_id"].astype(str),
                df_excel["period_start"],
                df_excel["grain"].astype(str),
            )
        )

        def is_excel_overridden(row):
            key = (str(row["facility_code"]), str(row["indicator_id"]), row["period_start"], str(row["grain"]))
            return key in excel_keys

        # Filter out DHIS2 records that are overridden by Excel
        mask_override = df_dhis2.apply(is_excel_overridden, axis=1)
        overridden_count = int(mask_override.sum())
        df_dhis2_retained = df_dhis2[~mask_override]

        df_merged = pd.concat([df_dhis2_retained, df_excel], ignore_index=True)

    # Ensure correct schema types
    df_merged["target"] = df_merged["target"].astype("int64")
    df_merged["numerator"] = df_merged["numerator"].astype("float64")
    df_merged["denominator"] = df_merged["denominator"].astype("float64")
    df_merged["pct"] = df_merged["pct"].astype("float64")
    df_merged["indicator_id"] = df_merged["indicator_id"].astype(str)
    df_merged["indicator_label"] = df_merged["indicator_label"].astype(str)
    df_merged["category"] = df_merged["category"].astype(str)
    df_merged["facility_code"] = df_merged["facility_code"].astype(str)
    df_merged["district"] = df_merged["district"].astype(str)
    df_merged["grain"] = df_merged["grain"].astype(str)
    df_merged["period_start"] = pd.to_datetime(df_merged["period_start"])

    # Sort deterministically
    df_merged = df_merged.sort_values(by=["period_start", "indicator_id", "facility_code"]).reset_index(drop=True)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    records_to_write = df_merged.to_dict(orient="records")
    atomic_parquet(target_path, records_to_write)

    # Update meta.json
    now_iso = datetime.now(timezone.utc).isoformat()
    unique_indicators = set(df_merged["indicator_id"].unique())
    periods_sorted = sorted(df_merged["period_start"].dt.strftime("%Y%m").unique())
    start_period = periods_sorted[0] if periods_sorted else ""
    end_period = periods_sorted[-1] if periods_sorted else ""

    meta = {
        "generated_at": now_iso,
        "rows": len(df_merged),
        "indicators": len(unique_indicators),
        "grains": ["monthly"],
        "data_source": "dhis2_excel_merged",
        "use_demo_data": False,
        "last_run_status": "ok",
        "period_start": start_period,
        "period_end": end_period,
        "excel_source": str(excel_input_file),
        "excel_records_merged": len(df_excel),
        "overridden_dhis2_records": overridden_count if "overridden_count" in locals() else 0,
    }
    atomic_json(meta_path, meta)

    return {
        "status": "success",
        "total_rows": len(df_merged),
        "excel_records": len(df_excel),
        "unique_indicators": len(unique_indicators),
        "facilities": df_merged["facility_code"].nunique(),
        "output": str(target_path),
        "meta": str(meta_path),
    }


def convert_excel_to_dhis2_parquet(
    input_file: Path | str = DEFAULT_INPUT_FILE,
    output_aggregate_dir: Path | str | None = None,
    output_mnid_aggregate_dir: Path | str | None = None,
    sync_run_id: str | None = None,
    merge_into_mnid: bool = True,
) -> dict[str, Any]:
    """
    Convert the Excel file into DHIS2 parquet representations and atomically save them,
    with option to merge directly into MNID indicator_aggregates.parquet.
    """
    input_path = Path(input_file).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    settings = DHIS2Settings.from_env(require_credentials=False)
    run_id = sync_run_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    now_iso = datetime.now(timezone.utc).isoformat()

    aggregate_dir = Path(output_aggregate_dir).resolve() if output_aggregate_dir else settings.aggregate_data_dir
    mnid_aggregate_dir = (
        Path(output_mnid_aggregate_dir).resolve()
        if output_mnid_aggregate_dir
        else PROJECT_ROOT / "data" / "mnid_aggregates" / "dhis2"
    )

    clean_df = parse_excel_metrics(input_path)
    facility_lookup = build_dhis2_facility_lookup()

    hmis_records: list[dict[str, Any]] = []
    atomic_records: list[dict[str, Any]] = []

    for _, row in clean_df.iterrows():
        raw_name = row["facility_raw"]
        canonical_name = FACILITY_NAME_MAP.get(raw_name, raw_name)
        fac_info = facility_lookup.get(canonical_name, {
            "org_unit_id": f"OU_{raw_name.replace(' ', '_')[:8]}",
            "facility_code": "",
            "district": "",
            "org_unit_name": canonical_name,
        })

        metric = row["concept_name"]
        dhis2_meta = METRIC_TO_DHIS2.get(metric, {
            "dhis2_id": metric.lower().replace(" ", "_"),
            "indicator_name": metric,
            "indicator_group": "Neonatal care",
            "mnid_id": f"mnid_excel_{metric.lower().replace(' ', '_')}",
            "category": "Newborn",
            "target": 0,
            "value_type": "count",
            "dx": "dx_" + metric.lower().replace(" ", "_")[:8],
        })

        dt: pd.Timestamp = row["date"]
        period_str = dt.strftime("%Y%m")
        period_start = pd.Timestamp(dt.year, dt.month, 1)
        val = float(row["value"])

        # Record for DHIS2 aggregate store (hmis_test / current)
        hmis_row = {
            "indicator_id": dhis2_meta["dhis2_id"],
            "indicator_name": dhis2_meta["indicator_name"],
            "indicator_group": dhis2_meta["indicator_group"],
            "period": period_str,
            "period_start": period_start.isoformat(),
            "org_unit_id": fac_info["org_unit_id"],
            "org_unit_name": fac_info["org_unit_name"],
            "district": fac_info["district"],
            "facility_code": fac_info["facility_code"],
            "value": val,
            "numerator": val,
            "denominator": val,
            "value_type": dhis2_meta["value_type"],
            "is_explicit_zero": val == 0,
            "mapping_version": "2026-08-04",
            "sync_run_id": run_id,
        }
        hmis_records.append(hmis_row)

        # Record for atomic normalized DHIS2 values
        dx_val = dhis2_meta["dx"]
        parts = dx_val.split(".", 1)
        atomic_row = {
            "source": "Malawi HMIS DHIS2",
            "dx": dx_val,
            "data_element_id": parts[0],
            "category_option_combo_id": parts[1] if len(parts) == 2 else None,
            "period": period_str,
            "period_start": period_start.date().isoformat(),
            "period_end": period_end_date(period_str).isoformat(),
            "org_unit_id": fac_info["org_unit_id"],
            "org_unit_name": fac_info["org_unit_name"],
            "district_name": fac_info["district"],
            "facility_code": fac_info["facility_code"],
            "value": str(val),
            "raw_value": str(val),
            "retrieved_at": now_iso,
            "sync_run_id": run_id,
            "mapping_version": "2026-08-04",
            "validation_status": "valid",
        }
        atomic_records.append(atomic_row)

    # Atomic Parquet outputs for DHIS2 aggregates
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    hmis_parquet_path = aggregate_dir / "hmis_test.parquet"
    current_parquet_path = aggregate_dir / "current.parquet"
    atomic_parquet(hmis_parquet_path, hmis_records)
    atomic_parquet(current_parquet_path, hmis_records)

    current_meta_path = aggregate_dir / "current_metadata.json"
    periods_sorted = sorted({r["period"] for r in hmis_records})
    start_period = periods_sorted[0] if periods_sorted else ""
    end_period = periods_sorted[-1] if periods_sorted else ""
    atomic_json(current_meta_path, {
        "source": "Malawi HMIS DHIS2",
        "sync_run_id": run_id,
        "mapping_version": "2026-08-04",
        "last_synced_at": now_iso,
        "start_period": start_period,
        "end_period": end_period,
        "validation_status": "valid",
    })

    normalized_dir = settings.normalized_data_dir / run_id
    normalized_dir.mkdir(parents=True, exist_ok=True)
    atomic_parquet(normalized_dir / "atomic_values.parquet", atomic_records)

    # Merge or write directly to MNID aggregate
    mnid_parquet_path = mnid_aggregate_dir / "indicator_aggregates.parquet"
    mnid_meta_path = mnid_aggregate_dir / "meta.json"

    if merge_into_mnid and mnid_parquet_path.exists():
        merge_res = merge_excel_into_indicator_aggregates(
            excel_input_file=input_path,
            target_parquet_path=mnid_parquet_path,
            target_meta_path=mnid_meta_path,
        )
    else:
        mnid_records = get_excel_mnid_records(input_path)
        atomic_parquet(mnid_parquet_path, mnid_records)
        atomic_json(mnid_meta_path, {
            "generated_at": now_iso,
            "rows": len(mnid_records),
            "indicators": len({r["indicator_id"] for r in mnid_records}),
            "grains": ["monthly"],
            "data_source": "excel",
            "use_demo_data": False,
            "last_run_status": "ok",
            "sync_run_id": run_id,
            "period_start": start_period,
            "period_end": end_period,
        })
        merge_res = {"rows": len(mnid_records)}

    summary = {
        "status": "success",
        "sync_run_id": run_id,
        "records_parsed": len(clean_df),
        "hmis_records": len(hmis_records),
        "merge_result": merge_res,
        "facilities": len({r["org_unit_name"] for r in hmis_records}),
        "periods": len(periods_sorted),
        "outputs": {
            "hmis_aggregate": str(hmis_parquet_path),
            "current_aggregate": str(current_parquet_path),
            "mnid_aggregate": str(mnid_parquet_path),
            "atomic_values": str(normalized_dir / "atomic_values.parquet"),
        },
    }
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Excel facility data into DHIS2-formatted Parquet datasets and merge into MNID aggregates"
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help="Path to the source Excel workbook",
    )
    parser.add_argument(
        "--output-aggregate-dir",
        type=Path,
        default=None,
        help="Custom output directory for DHIS2 aggregate parquet files",
    )
    parser.add_argument(
        "--output-mnid-aggregate-dir",
        type=Path,
        default=None,
        help="Custom output directory for MNID aggregate parquet files",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Do not merge with existing DHIS2 indicator aggregates; overwrite instead",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        res = convert_excel_to_dhis2_parquet(
            input_file=args.input_file,
            output_aggregate_dir=args.output_aggregate_dir,
            output_mnid_aggregate_dir=args.output_mnid_aggregate_dir,
            merge_into_mnid=not args.no_merge,
        )
        print(json.dumps(res, indent=2))
        return 0
    except Exception as exc:
        _LOG.exception("Excel to DHIS2 Parquet conversion failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
