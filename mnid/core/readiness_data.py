"""Operational Readiness Data Provider.

Loads and queries the compiled Health Facility Assessment (HFA) Parquet dataset
and JSON indicator metadata. Computes domain-specific readiness percentages,
denominators, and median-IQRs dynamically for single-facility detail views
and multi-facility comparison matrices.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
import pandas as pd
import numpy as np


_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "readiness"
_PARQUET_PATH = _DATA_DIR / "hfa_data.parquet"
_INDICATORS_PATH = _DATA_DIR / "hfa_indicators.json"


@lru_cache(maxsize=1)
def get_readiness_data() -> pd.DataFrame | None:
    """Load the HFA readiness Parquet dataset from disk (cached in memory)."""
    if not _PARQUET_PATH.exists():
        return None
    try:
        return pd.read_parquet(_PARQUET_PATH)
    except Exception as e:
        print(f"Error loading HFA Parquet dataset: {e}")
        return None


@lru_cache(maxsize=1)
def get_readiness_metadata() -> dict | None:
    """Load the HFA indicator metadata dictionary from disk (cached in memory)."""
    if not _INDICATORS_PATH.exists():
        return None
    try:
        with open(_INDICATORS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading HFA indicator metadata: {e}")
        return None


def is_readiness_data_available() -> bool:
    """Check if the compiled HFA dataset and indicator catalog exist."""
    return _PARQUET_PATH.exists() and _INDICATORS_PATH.exists()


def get_readiness_sheets() -> list[str]:
    """Get the ordered list of metadata sheets."""
    meta = get_readiness_metadata()
    return meta.get("sheets", []) if meta else []


def get_indicators_for_sheet(sheet_name: str) -> list[dict]:
    """Get the indicator definitions for a specific HFA metadata sheet."""
    meta = get_readiness_metadata()
    if not meta or "indicators_by_sheet" not in meta:
        return []
    return meta["indicators_by_sheet"].get(sheet_name, [])


def _calc_subset_stat(ind: dict, sub_df: pd.DataFrame, group_label: str) -> tuple[float | str | None, str | None]:
    """Compute an indicator statistic for a subset of facility rows."""
    if sub_df.empty:
        return None, f"0 {group_label} facilities"

    var = ind["variable"]
    if var not in sub_df.columns:
        return None, "Variable not present in dataset"

    stat = ind.get("statistic", "n_percent")
    resp = ind.get("response")
    denom_var = ind.get("denominator_variable")
    denom_resp = ind.get("denominator_response")

    # Filter by denominator variable if specified
    denom_df = sub_df
    if denom_var and denom_var in sub_df.columns:
        if denom_resp:
            denom_df = sub_df[sub_df[denom_var].astype(str).str.strip().str.lower() == str(denom_resp).strip().lower()]
        else:
            # If denominator is availability > 0 (common for functional sheets)
            denom_numeric = pd.to_numeric(sub_df[denom_var], errors="coerce")
            if denom_numeric.notna().any():
                denom_df = sub_df[denom_numeric > 0]
            else:
                denom_df = sub_df[sub_df[denom_var].notna()]

    den = len(denom_df)
    if den == 0:
        return None, f"0 of {len(sub_df)} {group_label} facilities eligible"

    if stat == "n_percent":
        target = str(resp).strip().lower() if resp is not None else "yes"
        num = (denom_df[var].astype(str).str.strip().str.lower() == target).sum()
        pct = round(num / den * 100, 1)
        detail = f"{num} of {den} {group_label} facilities ({resp})"
        return pct, detail

    elif stat == "n_percent_gt0":
        num_numeric = pd.to_numeric(denom_df[var], errors="coerce")
        num = (num_numeric > 0).sum()
        pct = round(num / den * 100, 1)
        detail = f"{num} of {den} {group_label} facilities with >0 items"
        return pct, detail

    elif stat == "median_iqr":
        num_numeric = pd.to_numeric(denom_df[var], errors="coerce").dropna()
        if num_numeric.empty:
            return None, f"No values reported across {den} facilities"
        med = num_numeric.median()
        q1 = num_numeric.quantile(0.25)
        q3 = num_numeric.quantile(0.75)
        unit = "%" if ind.get("flag_over100") else ""
        val_str = f"{med:.0f}{unit} [{q1:.0f}-{q3:.0f}{unit}]"
        detail = f"Median [Q1-Q3] across {len(num_numeric)} {group_label} facilities"
        return val_str, detail

    return None, None


def compute_readiness_matrix(
    sheet_name: str,
    facility_codes: list[str] | None = None,
    cemonc_codes: list[str] | None = None,
    bemonc_codes: list[str] | None = None,
) -> list[dict]:
    """Compute comparison matrix rows for a given HFA sheet across CEmONC and BEmONC facilities."""
    df = get_readiness_data()
    if df is None or df.empty:
        return []

    indicators = get_indicators_for_sheet(sheet_name)
    if not indicators:
        return []

    # Scope filtering
    if facility_codes:
        df_scope = df[df["facility_code"].isin(facility_codes)]
    else:
        df_scope = df

    if df_scope.empty:
        return []

    # Derive CEmONC and BEmONC groups if not explicitly provided
    if cemonc_codes is None or bemonc_codes is None:
        cemonc_sub = df_scope[df_scope["facility_level"].isin(["Secondary", "Tertiary"])]
        bemonc_sub = df_scope[df_scope["facility_level"] == "Primary"]
    else:
        cemonc_sub = df_scope[df_scope["facility_code"].isin(cemonc_codes)]
        bemonc_sub = df_scope[df_scope["facility_code"].isin(bemonc_codes)]

    rows = []
    for ind in indicators:
        cem_only = ind.get("cemonc_only", False)
        
        cemonc_val, cemonc_detail = _calc_subset_stat(ind, cemonc_sub, "CEmONC")
        if cem_only:
            bemonc_val = None
            bemonc_detail = "Not applicable for BEmONC facilities (CEmONC-only indicator)"
        else:
            bemonc_val, bemonc_detail = _calc_subset_stat(ind, bemonc_sub, "BEmONC")

        rows.append({
            "label": ind["label"],
            "category": ind.get("section_header") or None,
            "cemonc": cemonc_val,
            "cemonc_detail": cemonc_detail,
            "bemonc": bemonc_val,
            "bemonc_detail": bemonc_detail,
            "statistic": ind.get("statistic", "n_percent"),
        })

    return rows


def compute_readiness_detail(sheet_name: str, facility_code: str) -> list[dict]:
    """Compute single-facility detail rows for a given HFA sheet."""
    df = get_readiness_data()
    if df is None or df.empty:
        return []

    indicators = get_indicators_for_sheet(sheet_name)
    if not indicators:
        return []

    fac_rows = df[df["facility_code"] == facility_code]
    if fac_rows.empty:
        return []

    fac_row = fac_rows.iloc[0]
    fac_level = fac_row.get("facility_level", "Primary")
    is_cemonc = fac_level in ["Secondary", "Tertiary"]

    detail_rows = []
    for ind in indicators:
        var = ind["variable"]
        stat = ind.get("statistic", "n_percent")
        resp = ind.get("response")
        cem_only = ind.get("cemonc_only", False)

        if cem_only and not is_cemonc:
            status = "na"
            display_val = "Not expected at this level"
        elif var not in fac_row or pd.isna(fac_row[var]):
            status = "awaiting"
            display_val = "Not reported"
        else:
            raw_val = fac_row[var]
            if stat == "n_percent":
                target = str(resp).strip().lower() if resp is not None else "yes"
                actual = str(raw_val).strip().lower()
                if actual == target:
                    status = "green"
                    display_val = str(raw_val)
                elif "no" in actual or "0" in actual or "not" in actual:
                    status = "red"
                    display_val = str(raw_val)
                else:
                    status = "amber"
                    display_val = str(raw_val)
            elif stat == "n_percent_gt0":
                try:
                    numeric_v = float(raw_val)
                    if numeric_v > 0:
                        status = "green"
                        display_val = f"{int(numeric_v) if numeric_v.is_integer() else numeric_v} available"
                    else:
                        status = "red"
                        display_val = "0 available"
                except (ValueError, TypeError):
                    status = "amber"
                    display_val = str(raw_val)
            elif stat == "median_iqr":
                status = "plain"
                display_val = str(raw_val)
            else:
                status = "plain"
                display_val = str(raw_val)

        detail_rows.append({
            "label": ind["label"],
            "category": ind.get("section_header") or None,
            "status": status,
            "display_value": display_val,
            "raw_value": fac_row.get(var),
            "statistic": stat,
        })

    return detail_rows
