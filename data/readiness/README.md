   # Operational Readiness & Health Facility Assessment (HFA) Data

This directory contains the compiled dataset, metadata catalog, and audit report for the **Operational Readiness** tab of the dashboard.

The underlying survey data comes from periodic (e.g. annual) Health Facility Assessments across health facilities in Malawi.

---

## 1. Directory Structure

```text
data/readiness/
  ├── hfa_data.parquet                   # Cleaned & typed 67-facility dataset with DHIS2 crosswalk
  ├── hfa_indicators.json                # Complete catalog of 216 indicators and computation rules
  ├── readiness_conversion_report.json   # Machine-readable audit log of the last conversion
  └── README.md                          # Documentation and update guide
```

---

## 2. Pluggable Conversion Pipeline

When a new annual assessment workbook or analysis variable definition workbook is provided, no application code needs to be modified. Simply update the source Excel workbooks and run the CLI converter tool.

### Source Workbooks:
1. **Facility Dataset**: `data/excel/Malawi HFA summary indicators for dashboard dataset_<date>.xlsx`
   - Contains facility-level raw observations and calculated summary indicators (67 facilities, 219+ columns).
2. **Analysis Variables & Metadata**: `data/excel/Analysis variables BF HFA dashboard MW <date>.xlsx`
   - 12 sheets defining indicators across:
     - `Eqt - Maternity (AVL)` / `Eqt - Maternity (FC)` (Equipment availability & functionality)
     - `Eqt - Newborn (AVL)` / `Eqt - Newborn (FC)` (Newborn equipment availability & functionality)
     - `Meds - Maternity (AVL)` / `Meds - Maternity (SO)` (Medicines availability & stockouts)
     - `Meds - Newborn (AVL)` / `Meds - Newborn (SO)` (Newborn medicines availability & stockouts)
     - `SF` (WHO Maternal & Newborn Signal Functions)
     - `INF - Maternity` / `INF - Newborn` (Infrastructure & utilities)
     - `Facility systems` (Transport, governance, quality systems)

### Running the Conversion:

To run with default paths:
```bash
python -m mnid.tools.convert_readiness_data
```

To run with explicit custom paths:
```bash
python mnid/tools/convert_readiness_data.py \
  --data "data/excel/Malawi HFA summary indicators for dashboard dataset_10Sept2026.xlsx" \
  --meta "data/excel/Analysis variables BF HFA dashboard MW 10Sept.xlsx" \
  --output-dir "data/readiness"
```

---

## 3. Generated Artifacts

1. **`hfa_data.parquet`**:
   - High-performance columnar dataset containing all surveyed facilities.
   - Automatically crosswalked 100% to standard DHIS2 codes (`facility_code`), standardized facility names (`facility_name`), districts (`district`), facility types (`facility_type`), and facility tier levels (`facility_level`: Tertiary, Secondary, Primary).

2. **`hfa_indicators.json`**:
   - Structured JSON catalog containing metadata for all 216 indicators:
     - `domain`, `sub_domain`, `unit`, `aspect`
     - `section_header`, `label`, `variable`
     - `statistic` (`n_percent`, `n_percent_gt0`, `median_iqr`)
     - `response` (e.g. `'Available today'`, `'Functional'`)
     - `cemonc_only` (`true`/`false`)
     - `denominator_variable`, `denominator_response`
     - `flag_over100` (`true`/`false`)

3. **`readiness_conversion_report.json`**:
   - Full audit report recording file hashes (SHA256), timestamps, facility mapping crosswalk match rates, indicator counts by sheet, and validation warnings/errors.

---

## 4. Operational Readiness Data Provider

The core module [`mnid.core.readiness_data`](../../mnid/core/readiness_data.py) provides optimized, cached access for the dashboard:

- `is_readiness_data_available() -> bool`: Returns `True` when compiled HFA artifacts are present.
- `compute_readiness_matrix(sheet_name, facility_codes=None, cemonc_codes=None, bemonc_codes=None) -> list[dict]`: Computes traffic-light performance percentages for CEmONC vs. BEmONC facility groups across an entire domain.
- `compute_readiness_detail(sheet_name, facility_code) -> list[dict]`: Computes single-facility status rows and values.

The dashboard view in [`mnid/views/operational_readiness.py`](../../mnid/views/operational_readiness.py) seamlessly connects to these functions for **Products & Commodities**, **Systems & Infrastructure**, and **Signal Functions**, presenting live survey data and metrics across both single-facility and multi-facility scopes.
