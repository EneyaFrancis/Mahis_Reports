"""Pluggable Health Facility Assessment (HFA) Excel-to-Parquet conversion pipeline.

Converts the static HFA indicators dataset workbook and analysis variables definition
workbook into optimized Parquet and JSON metadata for high-performance dashboard rendering.

Outputs generated:
  - data/readiness/hfa_data.parquet
  - data/readiness/hfa_indicators.json
  - data/readiness/readiness_conversion_report.json

Usage:
  python -m mnid.tools.convert_readiness_data
  python mnid/tools/convert_readiness_data.py --data "data/excel/..." --meta "data/excel/..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import numpy as np


def _file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _normalize_name(name: str) -> str:
    """Normalize a facility name for robust crosswalk matching."""
    if not name or pd.isna(name):
        return ""
    s = str(name).strip()
    s = re.sub(r"\(.*?\)", "", s).strip().lower()
    # Normalize punctuation and common typos
    s = s.replace(".", "").replace("’", "").replace("'", "").replace("`", "")
    s = s.replace("diamphwi", "diamphwe")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _simplify_name(name: str) -> str:
    s = _normalize_name(name)
    for term in ["health centre", "hospital", "rural", "urban", "community", "mission", "dispensary", "1", "2"]:
        s = s.replace(term, "")
    return re.sub(r"\s+", " ", s).strip()


def build_facility_crosswalk(dhis2_crosswalk_path: Path, levels_path: Path) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    """Build a lookup map matching HFA facility names to DHIS2 CODE, NAME, DISTRICT, TYPE, and LEVEL."""
    crosswalk_records = []
    if dhis2_crosswalk_path.exists():
        try:
            with open(dhis2_crosswalk_path, "r", encoding="utf-8") as f:
                crosswalk_records = json.load(f) or []
        except Exception as e:
            print(f"Warning: Failed to load {dhis2_crosswalk_path}: {e}")

    levels_records = []
    if levels_path.exists():
        try:
            with open(levels_path, "r", encoding="utf-8") as f:
                levels_records = json.load(f) or []
        except Exception as e:
            print(f"Warning: Failed to load {levels_path}: {e}")

    levels_by_code = {
        str(r.get("CODE")): r for r in levels_records if r.get("CODE")
    }

    all_records = list(crosswalk_records)
    known_codes = {r.get("CODE") for r in crosswalk_records if r.get("CODE")}
    for lr in levels_records:
        if lr.get("CODE") and lr.get("CODE") not in known_codes:
            all_records.append(lr)

    lookup_exact: dict[tuple[str, str], dict] = {}
    lookup_fallback: dict[str, dict] = {}

    for rec in all_records:
        code = rec.get("CODE")
        if not code:
            continue
        level_info = levels_by_code.get(code, {})
        dist = rec.get("DISTRICT") or level_info.get("DISTRICT") or ""
        norm_dist = _normalize_name(dist)

        full_rec = {
            "facility_code": code,
            "facility_name": rec.get("NAME") or rec.get("COMMON NAME") or level_info.get("NAME") or code,
            "district": dist,
            "facility_type": level_info.get("TYPE") or rec.get("TYPE") or "Health Centre",
            "facility_level": level_info.get("FACILITY LEVEL") or rec.get("FACILITY LEVEL") or ("Tertiary" if "Central" in str(level_info.get("TYPE", "") or rec.get("TYPE", "")) else ("Secondary" if "District" in str(level_info.get("TYPE", "") or rec.get("TYPE", "")) else "Primary")),
            "dhis2_id": rec.get("DHIS2 ID") or "",
        }

        name_keys = [
            _normalize_name(rec.get("NAME", "")),
            _normalize_name(rec.get("COMMON NAME", "")),
            _normalize_name(level_info.get("NAME", "")),
            _normalize_name(level_info.get("COMMON NAME", "")),
        ]
        for k in name_keys:
            if k:
                if norm_dist:
                    lookup_exact[(k, norm_dist)] = full_rec
                if k not in lookup_fallback:
                    lookup_fallback[k] = full_rec

    return lookup_exact, lookup_fallback


def resolve_facility(orig_name: str, district_hint: str, lookup: tuple[dict, dict] | dict) -> dict | None:
    """Resolve an HFA facility string to the master crosswalk record."""
    if isinstance(lookup, tuple):
        lookup_exact, lookup_fallback = lookup
    else:
        lookup_exact, lookup_fallback = {}, lookup

    norm_name = _normalize_name(orig_name)
    norm_dist = _normalize_name(district_hint)

    # 1. Exact match by name and district
    if norm_dist and (norm_name, norm_dist) in lookup_exact:
        return lookup_exact[(norm_name, norm_dist)]

    # 2. Simplified name match with district
    simp = _simplify_name(orig_name)
    if norm_dist:
        for (k_name, k_dist), rec in lookup_exact.items():
            if norm_dist == k_dist and _simplify_name(k_name) == simp:
                return rec

    # 3. Fallback to name-only match
    if norm_name in lookup_fallback:
        return lookup_fallback[norm_name]

    for k_name, rec in lookup_fallback.items():
        if _simplify_name(k_name) == simp:
            return rec

    return None


def convert_readiness_workbooks(
    data_path: Path,
    meta_path: Path,
    output_dir: Path,
    dhis2_path: Path,
    levels_path: Path,
) -> dict:
    """Parse both workbooks and output parquet, json metadata, and validation audit report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "readiness_conversion_report.json"
    indicators_json_path = output_dir / "hfa_indicators.json"
    parquet_path = output_dir / "hfa_data.parquet"

    start_time = datetime.now(timezone.utc)
    print(f"Reading metadata workbook: {meta_path}")
    xl_meta = pd.ExcelFile(meta_path)
    
    print(f"Reading data workbook: {data_path}")
    df_data = pd.read_excel(data_path)

    facility_lookup = build_facility_crosswalk(dhis2_path, levels_path)

    # 1. Parse Metadata Sheets
    sheets = [s for s in xl_meta.sheet_names if s.strip().lower() != "guide"]
    metadata_by_sheet = {}
    all_required_variables = set()
    indicators_list = []
    sheet_stats = {}

    domain_mapping = {
        "Eqt - Maternity (AVL)": {"domain": "Equipment", "sub_domain": "Maternity Available", "unit": "Maternity", "aspect": "Availability"},
        "Eqt - Maternity (FC)": {"domain": "Equipment", "sub_domain": "Maternity Functional", "unit": "Maternity", "aspect": "Functionality"},
        "Eqt - Newborn (AVL)": {"domain": "Equipment", "sub_domain": "Newborn Available", "unit": "Newborn", "aspect": "Availability"},
        "Eqt - Newborn (FC)": {"domain": "Equipment", "sub_domain": "Newborn Functional", "unit": "Newborn", "aspect": "Functionality"},
        "Meds - Maternity (AVL)": {"domain": "Medicines", "sub_domain": "Maternity Available", "unit": "Maternity", "aspect": "Availability"},
        "Meds - Maternity (SO)": {"domain": "Medicines", "sub_domain": "Maternity Stockouts", "unit": "Maternity", "aspect": "Stockout"},
        "Meds - Newborn (AVL)": {"domain": "Medicines", "sub_domain": "Newborn Available", "unit": "Newborn", "aspect": "Availability"},
        "Meds - Newborn (SO)": {"domain": "Medicines", "sub_domain": "Newborn Stockouts", "unit": "Newborn", "aspect": "Stockout"},
        "SF": {"domain": "Signal Functions", "sub_domain": "Signal Functions", "unit": "All", "aspect": "Performance"},
        "INF - Maternity": {"domain": "Infrastructure", "sub_domain": "Maternity Infrastructure", "unit": "Maternity", "aspect": "Infrastructure"},
        "INF - Newborn": {"domain": "Infrastructure", "sub_domain": "Newborn Infrastructure", "unit": "Newborn", "aspect": "Infrastructure"},
        "Facility systems": {"domain": "Facility Systems", "sub_domain": "Systems and Governance", "unit": "Facility", "aspect": "Systems"},
    }

    warnings = []
    errors = []

    for sheet_name in sheets:
        sheet_df = pd.read_excel(meta_path, sheet_name=sheet_name)
        sheet_indicators = []
        domain_info = domain_mapping.get(sheet_name, {
            "domain": sheet_name, "sub_domain": sheet_name, "unit": "General", "aspect": "General"
        })

        for row_idx, row in sheet_df.iterrows():
            if pd.isna(row.get("variable")):
                continue
            var = str(row["variable"]).strip()
            label = str(row.get("label", var)).strip()
            section_header = str(row.get("section_header", "")).strip() if pd.notna(row.get("section_header")) else ""
            stat = str(row.get("statistic", "n_percent")).strip()
            resp = str(row["response"]).strip() if pd.notna(row.get("response")) else None
            cem_only = str(row.get("cemonc_only", "")).strip().lower() == "yes"
            denom_var = str(row["denominator_variable"]).strip() if pd.notna(row.get("denominator_variable")) else None
            denom_resp = str(row["denominator_response"]).strip() if pd.notna(row.get("denominator_response")) else None
            flag_over100 = str(row.get("flag_over100", "")).strip().lower() == "yes"

            all_required_variables.add(var)
            if denom_var:
                all_required_variables.add(denom_var)

            if var not in df_data.columns:
                warnings.append(f"Sheet '{sheet_name}', Row {row_idx+1}: Variable '{var}' not found in data workbook.")

            ind_obj = {
                "sheet": sheet_name,
                "domain": domain_info["domain"],
                "sub_domain": domain_info["sub_domain"],
                "unit": domain_info["unit"],
                "aspect": domain_info["aspect"],
                "section_header": section_header,
                "label": label,
                "variable": var,
                "statistic": stat,
                "response": resp,
                "cemonc_only": cem_only,
                "denominator_variable": denom_var,
                "denominator_response": denom_resp,
                "flag_over100": flag_over100,
            }
            sheet_indicators.append(ind_obj)
            indicators_list.append(ind_obj)

        metadata_by_sheet[sheet_name] = sheet_indicators
        sheet_stats[sheet_name] = len(sheet_indicators)

    # 2. Map and Clean Facilities in Data
    fac_name_col = "or_id_facid"
    fac_id_col = "id_recidfac"
    district_col = "sd_oth_id_distr_mw"

    mapped_rows = []
    unmapped_facilities = []

    for idx, row in df_data.iterrows():
        orig_fac_name = str(row.get(fac_name_col, "")).strip()
        orig_district = str(row.get(district_col, "")).strip()
        rec_id = row.get(fac_id_col)

        resolved = resolve_facility(orig_fac_name, orig_district, facility_lookup)
        if resolved:
            fac_code = resolved["facility_code"]
            fac_name = resolved["facility_name"]
            dist = resolved["district"] or orig_district
            f_type = resolved["facility_type"]
            f_level = resolved["facility_level"]
        else:
            fac_code = f"HFA_{rec_id}" if pd.notna(rec_id) else f"HFA_ROW_{idx}"
            fac_name = orig_fac_name
            dist = orig_district
            f_type = "Health Centre"
            f_level = "Primary"
            unmapped_facilities.append({"row": idx, "facility_name": orig_fac_name, "district": orig_district, "id_recidfac": rec_id})

        row_dict = row.to_dict()
        row_dict["facility_code"] = fac_code
        row_dict["facility_name"] = fac_name
        row_dict["district"] = dist
        row_dict["facility_type"] = f_type
        row_dict["facility_level"] = f_level
        mapped_rows.append(row_dict)

    df_clean = pd.DataFrame(mapped_rows)

    # Reorder key columns to the front
    lead_cols = ["facility_code", "facility_name", "district", "facility_type", "facility_level", "id_recidfac", "or_id_facid"]
    other_cols = [c for c in df_clean.columns if c not in lead_cols]
    df_clean = df_clean[lead_cols + other_cols]

    # Clean data types
    for col in other_cols:
        try:
            non_null = df_clean[col].dropna()
            if not non_null.empty:
                _ = pd.to_numeric(non_null, errors="raise")
                df_clean[col] = pd.to_numeric(df_clean[col], errors="coerce")
        except Exception:
            df_clean[col] = df_clean[col].astype(str).replace("nan", np.nan).replace("None", np.nan)

    # 3. Write Atomic Artifacts
    # A. JSON Indicators
    tmp_json = indicators_json_path.with_suffix(".json.tmp")
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": "1.0",
            "sheets": sheets,
            "domain_mapping": domain_mapping,
            "indicators_by_sheet": metadata_by_sheet,
            "all_indicators": indicators_list,
        }, f, indent=2)
    tmp_json.replace(indicators_json_path)

    # B. Parquet Data
    tmp_parquet = parquet_path.with_suffix(".parquet.tmp")
    df_clean.to_parquet(tmp_parquet, index=False, engine="pyarrow")
    tmp_parquet.replace(parquet_path)

    # 4. Generate Audit Report
    end_time = datetime.now(timezone.utc)
    duration_sec = round((end_time - start_time).total_seconds(), 2)

    report = {
        "status": "success" if not errors else "failed",
        "generated_at": end_time.isoformat(),
        "duration_seconds": duration_sec,
        "inputs": {
            "data_workbook": {
                "path": str(data_path),
                "sha256": _file_hash(data_path),
                "size_bytes": data_path.stat().st_size,
                "rows": len(df_data),
                "columns": len(df_data.columns),
            },
            "meta_workbook": {
                "path": str(meta_path),
                "sha256": _file_hash(meta_path),
                "size_bytes": meta_path.stat().st_size,
                "sheets_count": len(sheets),
                "indicators_count": len(indicators_list),
            },
        },
        "outputs": {
            "parquet": {
                "path": str(parquet_path),
                "sha256": _file_hash(parquet_path),
                "size_bytes": parquet_path.stat().st_size,
                "rows": len(df_clean),
                "columns": len(df_clean.columns),
            },
            "indicators_json": {
                "path": str(indicators_json_path),
                "sha256": _file_hash(indicators_json_path),
                "size_bytes": indicators_json_path.stat().st_size,
            },
        },
        "facilities_summary": {
            "total_hfa_facilities": len(df_data),
            "mapped_to_dhis2_code": len(df_data) - len(unmapped_facilities),
            "unmapped_count": len(unmapped_facilities),
            "unmapped_facilities": unmapped_facilities,
        },
        "indicators_summary": {
            "total_indicators": len(indicators_list),
            "by_sheet": sheet_stats,
        },
        "validation": {
            "warnings_count": len(warnings),
            "warnings": warnings,
            "errors_count": len(errors),
            "errors": errors,
        },
    }

    tmp_report = report_path.with_suffix(".json.tmp")
    with open(tmp_report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    tmp_report.replace(report_path)

    print("\n" + "=" * 60)
    print("HFA READINESS CONVERSION COMPLETE")
    print("=" * 60)
    print(f"Facilities: {len(df_clean)} (Mapped: {len(df_data) - len(unmapped_facilities)} / {len(df_data)})")
    print(f"Total Indicators: {len(indicators_list)} across {len(sheets)} sheets")
    print(f"Parquet Output: {parquet_path} ({parquet_path.stat().st_size / 1024:.1f} KB)")
    print(f"Metadata Output: {indicators_json_path} ({indicators_json_path.stat().st_size / 1024:.1f} KB)")
    print(f"Report Output: {report_path}")
    print("=" * 60)

    return report


def main():
    root_dir = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Convert HFA Excel workbooks to Parquet & JSON metadata.")
    parser.add_argument(
        "--data",
        type=Path,
        default=root_dir / "data" / "excel" / "Malawi HFA summary indicators for dashboard dataset_10Sept2026.xlsx",
        help="Path to HFA facility dataset Excel workbook",
    )
    parser.add_argument(
        "--meta",
        type=Path,
        default=root_dir / "data" / "excel" / "Analysis variables BF HFA dashboard MW 10Sept.xlsx",
        help="Path to HFA analysis variables definition Excel workbook",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root_dir / "data" / "readiness",
        help="Directory to save output files",
    )
    parser.add_argument(
        "--dhis2-crosswalk",
        type=Path,
        default=root_dir / "data" / "geo" / "facilities_dhis2.json",
        help="Path to facilities_dhis2.json crosswalk",
    )
    parser.add_argument(
        "--levels",
        type=Path,
        default=root_dir / "data" / "geo" / "facilities_levels.json",
        help="Path to facilities_levels.json",
    )

    args = parser.parse_args()

    if not args.data.exists():
        print(f"Error: Data file {args.data} does not exist.", file=sys.stderr)
        sys.exit(1)
    if not args.meta.exists():
        print(f"Error: Meta file {args.meta} does not exist.", file=sys.stderr)
        sys.exit(1)

    report = convert_readiness_workbooks(
        data_path=args.data,
        meta_path=args.meta,
        output_dir=args.output_dir,
        dhis2_path=args.dhis2_crosswalk,
        levels_path=args.levels,
    )
    if report["validation"]["errors_count"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
