"""DQ20 (Aggregate reconciliation) -- WHO DQR Module 3's "Verification
Factor": independently recompute each indicator from source records and
compare the total against the value reported through the official system,
flagging material disagreement rather than assuming either source is right.

Here, MAHIS (data/mnid_aggregates/default) is the facility source-document
equivalent -- built from raw patient encounters -- and HMIS/DHIS2
(data/mnid_aggregates/dhis2) is the officially reported/aggregated system,
matching the WHO formula's "recounted" and "reported" roles respectively.

Deliberately reconciles at the national/period level, not per facility:
MAHIS facility codes are plain numbers (e.g. "32") and DHIS2 codes are
alphanumeric (e.g. "BT240059") with no crosswalk between them in this
system, so a facility-level join isn't possible. Reconciling at a higher
aggregating level is explicitly sanctioned by the WHO toolkit itself
("Verification factor for higher-level aggregating units"), not a
workaround.
"""
import os

import pandas as pd

_MAHIS_PATH = os.path.join("data", "mnid_aggregates", "default", "indicator_aggregates.parquet")
_DHIS2_PATH = os.path.join("data", "mnid_aggregates", "dhis2", "indicator_aggregates.parquet")

# WHO's own worked example calls a 20% difference "indicative of a
# potential problem" -- VF bands below follow that same reference point,
# escalating severity as the gap widens.
_VF_BANDS = [
    (50, "critical"),  # VF < 50% or > 150% of 100
    (30, "high"),
    (20, "medium"),
]


def _severity_for_vf(vf: float) -> str | None:
    deviation = abs(vf - 100)
    for threshold, severity in _VF_BANDS:
        if deviation >= threshold:
            return severity
    return None


def available() -> bool:
    return os.path.exists(_MAHIS_PATH) and os.path.exists(_DHIS2_PATH)


def _load_monthly(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    return df[df["grain"] == "monthly"] if "grain" in df.columns else df


def common_indicators() -> list[str]:
    """mnid ids present in both aggregates -- only these are reconcilable;
    an id that only one route publishes has nothing to verify it against."""
    if not available():
        return []
    mahis_ids = set(_load_monthly(_MAHIS_PATH)["indicator_id"].unique())
    dhis2_ids = set(_load_monthly(_DHIS2_PATH)["indicator_id"].unique())
    return sorted(mahis_ids & dhis2_ids)


def compute_reconciliation(start_date: str | None = None, end_date: str | None = None) -> dict:
    """For every indicator both routes publish, sums numerator/denominator
    across all facilities and across whichever period window is given.

    With no window given, defaults to the two routes' *overlapping*
    coverage (max of their start dates to min of their end dates) rather
    than each route's own latest period in isolation -- comparing MAHIS's
    latest month against DHIS2's latest month independently is misleading
    whenever the two don't share a calendar month (confirmed true here:
    MAHIS's extract currently only spans Jul-Aug 2026, well short of
    DHIS2's Jan 2025-Sep 2026), since a quiet single month on one side
    then reads as a near-total verification failure on every indicator.
    Summing over the real overlap still can't fix a route that's
    genuinely sparse across its whole covered range, but it stops
    one-month noise from masquerading as a reconciliation failure.
    """
    if not available():
        return {"rows": [], "period_label": None}

    mahis = _load_monthly(_MAHIS_PATH)
    dhis2 = _load_monthly(_DHIS2_PATH)

    if start_date and end_date:
        start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
        period_label = f"{start_date} to {end_date}"
    else:
        start = max(mahis["period_start"].min(), dhis2["period_start"].min())
        end = min(mahis["period_start"].max(), dhis2["period_start"].max())
        period_label = (
            f"{start.date()} to {end.date()} (overlap of MAHIS's and DHIS2's covered periods)"
            if pd.notna(start) and pd.notna(end) else "—"
        )

    mahis = mahis[(mahis["period_start"] >= start) & (mahis["period_start"] <= end)]
    dhis2 = dhis2[(dhis2["period_start"] >= start) & (dhis2["period_start"] <= end)]

    ids = common_indicators()
    if not ids:
        return {"rows": [], "period_label": period_label}

    m_sum = mahis[mahis["indicator_id"].isin(ids)].groupby(["indicator_id", "indicator_label", "category"]).agg(
        numerator=("numerator", "sum")
    ).reset_index()
    d_sum = dhis2[dhis2["indicator_id"].isin(ids)].groupby("indicator_id").agg(
        numerator=("numerator", "sum")
    ).reset_index().rename(columns={"numerator": "dhis2_numerator"})

    merged = m_sum.merge(d_sum, on="indicator_id", how="inner")
    merged = merged.rename(columns={"numerator": "mahis_numerator"})

    rows = []
    for _, r in merged.iterrows():
        mahis_n, dhis2_n = r["mahis_numerator"], r["dhis2_numerator"]
        if dhis2_n == 0:
            vf = None
        else:
            vf = round(float(mahis_n) / float(dhis2_n) * 100, 1)
        rows.append({
            "indicator_id": r["indicator_id"], "label": r["indicator_label"], "category": r["category"],
            "mahis_numerator": int(mahis_n), "dhis2_numerator": int(dhis2_n), "vf": vf,
            "severity": _severity_for_vf(vf) if vf is not None else "high",
        })

    rows.sort(key=lambda r: (abs((r["vf"] or 999) - 100)), reverse=True)
    return {"rows": rows, "period_label": period_label}
