# Excel Data Sources & DHIS2 Aggregate Integration Guide

This guide describes how to add Excel data sources to the MaHIS Dashboards / MNID pipeline and the required order of script execution.

> **Note on Version Control:**  
> All Excel workbooks in `data/excel/` (`*.xlsx`, `*.xls`) are **gitignored** (`.gitignore`) and are not committed or pushed to the remote repository. Developers and deployers must obtain or place the Excel source files manually in the `data/excel/` directory.

---

## 1. Supported Excel Data Sources

The pipeline currently supports multiple Excel workbooks providing offline or facility-specific statistics:

1. **NEST360 / BF Facilities Workbook**
   - **Path:** `data/excel/NEST_BF_facilitites.xlsx`
   - **Sheet Name:** `NEST360_BF Facilities`
   - **Coverage:** 7 high-priority facilities (Bwaila Hospital, Embangweni Mission Hospital, Kamuzu Central Hospital, Mzimba District Hospital, Mzuzu Central Hospital, Nkhoma Mission Hospital, Queen Elizabeth Central Hospital).
   - **Metrics:** Admissions, Sepsis, RDS, KMC, Prophylactic CPAP, Symptomatic CPAP, Bilirubin Measurement, Phototherapy, Antibiotics, Hypothermia on Admission, Hypothermia during Stay.

2. **PPH Monthly Service Statistics Workbook**
   - **Path:** `data/excel/PPH_Monthly_Service_Statistics_2026.xlsx`
   - **Sheet Name:** `MAHIS Data`
   - **Coverage:** 34 facilities across 4 districts (Blantyre, Mzimba South, Mzimba North, Lilongwe).
   - **Metrics:** PPH Cases, Women detected early (300ml-500ml), Women detected early with full bundle given, Drapes used, % of women detected early, % of women detected early with full bundle given.

---

## 2. Priority & Overwrite Rules

When merging Excel workbook data into `data/mnid_aggregates/dhis2/indicator_aggregates.parquet`:

- **High-Priority Replacement:** For any facility and period present in an Excel workbook, the Excel-derived indicator values take precedence over DHIS2-sourced values.
- **DHIS2 Preservation:** Any indicators **not** covered by the Excel sheets for those facilities are preserved directly from DHIS2.
- **Other Facilities:** Facilities not present in either Excel workbook remain 100% sourced from DHIS2.
- **Missing File Gracefulness:** If either `NEST_BF_facilitites.xlsx` or `PPH_Monthly_Service_Statistics_2026.xlsx` (or both) is missing, the scripts will process whatever files are available without raising errors.

---

## 3. Recommended Script Execution Order

When updating or initializing the dashboard datasets from scratch or when new Excel data arrives:

### Step 1: Place Excel Files
Ensure the necessary Excel files are placed in `data/excel/`:
- `data/excel/NEST_BF_facilitites.xlsx`
- `data/excel/PPH_Monthly_Service_Statistics_2026.xlsx`

### Step 2: Ingest & Sync DHIS2 Data (if using live/cached DHIS2 sync)
If running a DHIS2 ingestion and synchronization cycle:
```bash
# Ingest raw DHIS2 payloads
python -m mnid.dhis2.ingestion

# Run atomic DHIS2 normalization & sync
python -m mnid.dhis2.sync

# Publish DHIS2 indicator aggregates (automatically merges available Excel data)
python -m mnid.dhis2.mnid_publish
```

### Step 3: Convert & Merge Excel Data Directly (Standalone Tool)
To convert Excel workbooks to DHIS2 parquet format and merge into the MNID indicator aggregates:
```bash
python mnid/dhis2/tools/excel_to_dhis2_parquet.py
```

Optional CLI flags:
- `--nest-file path/to/nest.xlsx`: Custom NEST workbook path
- `--pph-file path/to/pph.xlsx`: Custom PPH workbook path
- `--no-merge`: Generate parquet datasets without merging into existing indicator aggregates
- `--log-level DEBUG|INFO|WARNING|ERROR`: Logging verbosity

### Step 4: Standalone Merge Tool (Optional)
If you only need to re-run the prioritized merge into `indicator_aggregates.parquet`:
```bash
python mnid/dhis2/tools/merge_excel_aggregates.py
```

### Step 5: Verify Dataset Integrity
Verify that `indicator_aggregates.parquet` and `meta.json` have been updated:
```bash
python -c "import pandas as pd; df = pd.read_parquet('data/mnid_aggregates/dhis2/indicator_aggregates.parquet'); print('Rows:', len(df), 'Indicators:', df['indicator_id'].nunique(), 'Facilities:', df['facility_code'].nunique())"
```

---

## 4. How to Add a New Excel Data Source in the Future

To add another Excel workbook:
1. Add the file path constant in `mnid/dhis2/tools/excel_to_dhis2_parquet.py`.
2. Implement a dedicated parse function (e.g., `parse_<source>_excel_metrics`) handling the workbook's layout and headers.
3. Map metric column headers to DHIS2 and MNID indicator IDs (`<SOURCE>_METRIC_TO_DHIS2`).
4. Update `FACILITY_NAME_MAP` if any facility naming differences exist compared to the DHIS2 crosswalk (`mnid.core.dhis2_facilities`).
5. Include the parser call in `get_excel_mnid_records()` with file existence checks.
