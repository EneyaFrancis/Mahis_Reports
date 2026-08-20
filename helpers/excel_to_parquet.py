import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# Configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = PROJECT_ROOT / "data" / "excel" / "NEST_BF_facilitites.xlsx"
OUT_DIR = PROJECT_ROOT / "data" / "parquet"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Required string columns for the target schema
STR_COLS = [
    "person_id", "encounter_id", "Program", "Service_Area", "Facility",
    "Facility_CODE", "District", "Facility_Type", "Encounter", "new_revisit", "Gender",
    "Home_district", "TA", "Village", "concept_name", "obs_value_coded",
    "Value", "DrugName", "Value_name", "Order_Name", "person_id_key",
    "months", "User", "Reporting_Program", "Source_Program", ""
]


def parse_complex_excel(filepath):
    """Parses the specific wide date-block format of NEST BF facilities.xlsx"""
    df_raw = pd.read_excel(filepath, sheet_name="NEST360_BF Facilities", header=None)

    # Extract the header rows (0 and 1)
    dates_row = df_raw.iloc[0].ffill()
    metrics_row = df_raw.iloc[1]

    # Extract facility data
    facilities = df_raw.iloc[2:, 1].dropna().values

    records = []
    # Loop through the columns to extract Date, Metric, and Values
    for col_idx in range(2, len(df_raw.columns)):
        date_val = dates_row[col_idx]
        metric = metrics_row[col_idx]

        # Only process columns that have a recognizable datetime date block
        if isinstance(date_val, datetime) and pd.notna(metric):
            for row_idx, facility in enumerate(facilities, start=2):
                val = df_raw.iloc[row_idx, col_idx]
                if pd.notna(val):
                    records.append({
                        "Facility": facility,
                        "Date": date_val,
                        "concept_name": str(metric).strip(),
                        "ValueN": val
                    })

    return pd.DataFrame(records)


def generate_parquet_from_df(df):
    all_rows = []
    id_seq = 0

    for _, row in df.iterrows():
        id_seq += 1
        date_str = row["Date"].strftime("%Y-%m-%d")
        month_tag = row["Date"].strftime("%Y-%m")

        # Build the EAV dictionary matching the target schema
        base_row = {
            "person_id": id_seq,
            "encounter_id": id_seq,
            "Date": date_str,
            "Program": "NEONATAL PROGRAM",  # Defaulting based on the spreadsheet indicators
            "Service_Area": "NEONATAL PROGRAM",
            "Facility": row["Facility"],
            "Facility_CODE": "",
            "District": "",
            "Facility_Type": "Secondary",  # Default inference
            "Encounter": "Neonatal enrolment",
            "new_revisit": "new",
            "Age": 0,
            "Age_Group": "Under 5",
            "Gender": "Unknown",
            "Home_district": "",
            "TA": "",
            "Village": "",
            "concept_name": row["concept_name"],
            "obs_value_coded": "",
            "Value": "",
            "ValueN": float(row["ValueN"]) if str(row["ValueN"]).replace('.', '', 1).isdigit() else np.nan,
            "visit_days": 1,
            "DrugName": "",
            "Value_name": "",
            "Order_Name": "",
            "count": 1,
            "count_set": 1,
            "sum": 1,
            "person_id_key": str(id_seq),
            "value_datetime": "",
            "months": month_tag,
            "User": "data_import",
            "Reporting_Program": "",
            "Source_Program": "",
            "": "",
        }
        all_rows.append(base_row)

    final_df = pd.DataFrame(all_rows)
    final_df["Date"] = pd.to_datetime(final_df["Date"])

    # Force string types to prevent Parquet utf-8/Int32 collision
    for col in STR_COLS:
        if col in final_df.columns:
            final_df[col] = final_df[col].fillna("").astype(object)

    # Partition and save by month
    saved = 0
    months = final_df["months"].unique()
    for month in months:
        mask = final_df["months"] == month
        chunk = final_df[mask].copy()
        clean_tag = month.replace("-", "")

        path = OUT_DIR / f"data_{clean_tag}.parquet"
        chunk.to_parquet(path, index=False)
        print(f"Wrote {path.name}: {len(chunk):,} rows")
        saved += 1

    print(f"\nSuccessfully generated {saved} parquet files.")


if __name__ == "__main__":
    print("Parsing Excel...")
    clean_df = parse_complex_excel(INPUT_FILE)
    print(f"Extracted {len(clean_df)} valid metric records. Generating Parquet...")
    generate_parquet_from_df(clean_df)