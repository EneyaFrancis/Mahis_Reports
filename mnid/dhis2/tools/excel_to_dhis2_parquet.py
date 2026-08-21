"""
Convert Excel facility data (e.g. data/excel/NEST_BF_facilitites.xlsx)
into parquet dataset(s) formatted as if fetched/calculated from DHIS2.

Supports generating:
1. DHIS2 HMIS aggregated format (mnid/data/dhis2/aggregates/hmis_test.parquet and current.parquet)
2. MNID indicator aggregate format (data/mnid_aggregates/dhis2/indicator_aggregates.parquet)
3. Atomic normalized DHIS2 values (mnid/data/dhis2/normalized/<run_id>/atomic_values.parquet)
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

# Dynamic Project Root Resolution
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT_FILE = PROJECT_ROOT / "data" / "excel" / "NEST_BF_facilitites.xlsx"

# Mapping from Excel metric/concept names to DHIS2 indicator ID and MNID metadata
METRIC_TO_DHIS2 = {
    "Admissions": {
        "dhis2_id": "neonatal_admissions",
        "indicator_name": "Neonatal Admissions",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_overview_001",
        "category": "Newborn",
        "target": 100,
        "value_type": "count",
        "dx": "utECJ6sZdYO",
    },
    "Sepsis": {
        "dhis2_id": "rhd_mat_newborn_complications_sepsis",
        "indicator_name": "Neonatal sepsis among newborn admissions",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationsepsis",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "c2zR6Z68Ncf",
    },
    "Jaundice": {
        "dhis2_id": "rhd_mat_newborn_complications_othe",
        "indicator_name": "Newborn complications: Jaundice / Other",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationother",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "bW26n4wRzV5",
    },
    "RDS": {
        "dhis2_id": "rhd_mat_newborn_complications_asphyxia",
        "indicator_name": "Respiratory distress / Birth asphyxia",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationasphyxia",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "hA4cT9rD8yN",
    },
    "KMC": {
        "dhis2_id": "low_birthweight_newborns",
        "indicator_name": "KMC / Low birthweight newborns",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_nb_core_kmc",
        "category": "Newborn",
        "target": 80,
        "value_type": "count",
        "dx": "z7nL2kK9rM0",
    },
    "Prophy CPAP": {
        "dhis2_id": "resuscitation_intervention_recorded",
        "indicator_name": "Prophylactic CPAP / Resuscitation",
        "indicator_group": "Neonatal care",
        "mnid_id": "mnid_lab_prog_012",
        "category": "Labour",
        "target": 90,
        "value_type": "count",
        "dx": "d8J2xK8vM1s",
    },
    "Sympt CPAP": {
        "dhis2_id": "newborns_not_breathing_at_birth_receiving_bag_mask_ventilation",
        "indicator_name": "Symptomatic CPAP / BMV",
        "indicator_group": "Delivery and newborn care",
        "mnid_id": "mnid_lab_prog_012",
        "category": "Labour",
        "target": 90,
        "value_type": "count",
        "dx": "tN5uV8rQ1zO",
    },
    "Bilirubin Measurement": {
        "dhis2_id": "vitamin_k_at_birth",
        "indicator_name": "Bilirubin Measurement / Essential Care",
        "indicator_group": "Delivery and newborn care",
        "mnid_id": "mnid_pnc_prog_011",
        "category": "Newborn",
        "target": 95,
        "value_type": "count",
        "dx": "w2M8n4bV6zP",
    },
    "Phototherapty": {
        "dhis2_id": "rhd_mat_newborn_complications_weight_2500g",
        "indicator_name": "Phototherapy / Special Newborn Care",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationweight2500g",
        "category": "Newborn",
        "target": 80,
        "value_type": "count",
        "dx": "m3B9n5vC7xQ",
    },
    "Antibiotics": {
        "dhis2_id": "signal_parenteral_antibiotics",
        "indicator_name": "Administration of parenteral antibiotics",
        "indicator_group": "Obstetric complications and signal functions",
        "mnid_id": "mnid_lab_core_signalantibiotics",
        "category": "Labour",
        "target": 80,
        "value_type": "count",
        "dx": "k1L7n3mP9vS",
    },
    "Hypo on Admin": {
        "dhis2_id": "rhd_mat_newborn_complications_prematurity",
        "indicator_name": "Hypoglycaemia / Prematurity complication",
        "indicator_group": "Newborn complications at birth (2026-08 breakdown)",
        "mnid_id": "mnid_nb_core_complicationprematurity",
        "category": "Newborn",
        "target": 0,
        "value_type": "count",
        "dx": "x9K3b7nV1zR",
    },
    "Hypo during Stay": {
        "dhis2_id": "babies_with_postnatal_complications",
        "indicator_name": "Postnatal complication during stay",
        "indicator_group": "Postnatal care",
        "mnid_id": "mnid_pnc_overview_003",
        "category": "PNC",
        "target": 0,
        "value_type": "count",
        "dx": "j5M2b8vN4zL",
    },
}

# Facility name resolution to DHIS2 crosswalk
FACILITY_NAME_MAP = {
    "Bwaila District Hospital": "Bwaila Hospital",
    "Embangweni Hospital": "Embangweni Mission Hospital",
    "Kamuzu Central Hospital": "Kamuzu Central Hospital",
    "Mzimba District Hospital": "Mzimba District Hospital",
    "Mzuzu Central Hospital": "Mzuzu Central Hospital",
    "Nkhoma Mission Hospital": "Nkhoma Mission Hospital",
    "Queen Elizabeth Central Hospital": "Queen Elizabeth Central Hospital",
}


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


def convert_excel_to_dhis2_parquet(
    input_file: Path | str = DEFAULT_INPUT_FILE,
    output_aggregate_dir: Path | str | None = None,
    output_mnid_aggregate_dir: Path | str | None = None,
    sync_run_id: str | None = None,
) -> dict[str, Any]:
    """
    Convert the Excel file into DHIS2 parquet representations and atomically save them.
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
    mnid_records: list[dict[str, Any]] = []
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
            "target": 80,
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

        # Record for MNID publish indicator_aggregates.parquet
        mnid_row = {
            "indicator_id": dhis2_meta["mnid_id"],
            "indicator_label": dhis2_meta["indicator_name"],
            "category": dhis2_meta["category"],
            "target": dhis2_meta["target"],
            "facility_code": fac_info["facility_code"],
            "district": fac_info["district"],
            "grain": "monthly",
            "period_start": period_start,
            "numerator": val,
            "denominator": val,
            "pct": 100.0 if val else 0.0,
        }
        mnid_records.append(mnid_row)

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

    # Atomic Parquet outputs
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    mnid_aggregate_dir.mkdir(parents=True, exist_ok=True)

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

    mnid_parquet_path = mnid_aggregate_dir / "indicator_aggregates.parquet"
    atomic_parquet(mnid_parquet_path, mnid_records)

    mnid_meta_path = mnid_aggregate_dir / "meta.json"
    atomic_json(mnid_meta_path, {
        "generated_at": now_iso,
        "rows": len(mnid_records),
        "indicators": len({r["indicator_id"] for r in mnid_records}),
        "grains": ["monthly"],
        "data_source": "dhis2",
        "use_demo_data": False,
        "last_run_status": "ok",
        "sync_run_id": run_id,
        "mapping_version": "2026-08-04",
        "period_start": start_period,
        "period_end": end_period,
        "skipped_unmapped_dhis2_indicators": 0,
    })

    normalized_dir = settings.normalized_data_dir / run_id
    normalized_dir.mkdir(parents=True, exist_ok=True)
    atomic_parquet(normalized_dir / "atomic_values.parquet", atomic_records)

    summary = {
        "status": "success",
        "sync_run_id": run_id,
        "records_parsed": len(clean_df),
        "hmis_records": len(hmis_records),
        "mnid_records": len(mnid_records),
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
        description="Convert Excel facility data into DHIS2-formatted Parquet datasets"
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
        )
        print(json.dumps(res, indent=2))
        return 0
    except Exception as exc:
        _LOG.exception("Excel to DHIS2 Parquet conversion failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
