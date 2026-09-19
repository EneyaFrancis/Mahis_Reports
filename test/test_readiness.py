"""Unit tests for the Health Facility Assessment (HFA) conversion and data provider."""
import unittest
import json
from pathlib import Path
import pandas as pd

from mnid.core.readiness_data import (
    is_readiness_data_available,
    get_readiness_data,
    get_readiness_metadata,
    get_readiness_sheets,
    get_indicators_for_sheet,
    compute_readiness_matrix,
    compute_readiness_detail,
    get_facility_emonc_classification,
)
from mnid.tools.convert_readiness_data import (
    build_facility_crosswalk,
    resolve_facility,
    convert_readiness_workbooks,
)


class TestReadinessConversionAndProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parents[1]
        cls.data_dir = cls.root_dir / "data" / "readiness"
        cls.parquet_path = cls.data_dir / "hfa_data.parquet"
        cls.indicators_path = cls.data_dir / "hfa_indicators.json"
        cls.report_path = cls.data_dir / "readiness_conversion_report.json"

    def test_artifacts_exist(self):
        self.assertTrue(self.parquet_path.exists(), "hfa_data.parquet should exist")
        self.assertTrue(self.indicators_path.exists(), "hfa_indicators.json should exist")
        self.assertTrue(self.report_path.exists(), "readiness_conversion_report.json should exist")

    def test_report_contents(self):
        with open(self.report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["facilities_summary"]["total_hfa_facilities"], 67)
        self.assertEqual(report["facilities_summary"]["mapped_to_dhis2_code"], 67)
        self.assertEqual(report["facilities_summary"]["unmapped_count"], 0)
        self.assertEqual(report["indicators_summary"]["total_indicators"], 216)
        self.assertEqual(report["validation"]["errors_count"], 0)

    def test_data_provider_basics(self):
        self.assertTrue(is_readiness_data_available())
        df = get_readiness_data()
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 67)
        self.assertIn("facility_code", df.columns)
        self.assertIn("facility_name", df.columns)
        self.assertIn("district", df.columns)
        self.assertIn("facility_level", df.columns)

        sheets = get_readiness_sheets()
        self.assertEqual(len(sheets), 12)
        self.assertIn("Eqt - Maternity (AVL)", sheets)
        self.assertIn("SF", sheets)

    def test_emonc_classification_from_sd_del_emonc2(self):
        cls_map = get_facility_emonc_classification()
        self.assertGreater(len(cls_map), 0)
        
        df = get_readiness_data()
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 67)
        
        # Verify classification for all 67 facility codes from sd_del_emonc2
        code_classifications = {row["facility_code"]: cls_map.get(row["facility_code"]) for _, row in df.iterrows()}
        cemonc_codes = [c for c, v in code_classifications.items() if v == "CEmONC"]
        bemonc_codes = [c for c, v in code_classifications.items() if v == "BEmONC"]
        self.assertEqual(len(cemonc_codes), 19)
        self.assertEqual(len(bemonc_codes), 48)

        # Verify lookup by facility name and normalized name
        self.assertEqual(cls_map.get("Bwaila Hospital"), "CEmONC")
        self.assertEqual(cls_map.get("Kamuzu Central Hospital"), "CEmONC")
        self.assertEqual(cls_map.get("Bangwe Health Centre"), "BEmONC")
        self.assertEqual(cls_map.get("Chileka Health Centre (Blantyre)"), "BEmONC")
        self.assertEqual(cls_map.get("Chileka Health Centre (Lilongwe)"), "BEmONC")

    def test_cemonc_only_and_inactivity(self):
        rows = compute_readiness_matrix("SF")
        self.assertGreater(len(rows), 0)
        cemonc_only_rows = [r for r in rows if r.get("cemonc_only")]
        for r in cemonc_only_rows:
            self.assertEqual(r["bemonc"], "N/A", f"BEmONC value for CEmONC-only indicator {r['label']} should be N/A")
            self.assertIn("CEmONC-only", r.get("bemonc_detail", ""))

        # Verify no inactive rows where both CEmONC and BEmONC are empty
        for r in rows:
            self.assertFalse(
                r.get("cemonc") is None and (r.get("bemonc") is None or r.get("bemonc") == "N/A"),
                f"Inactive indicator {r['label']} should have been filtered out",
            )

    def test_all_12_sheets_matrix_computation(self):
        sheets = get_readiness_sheets()
        for sheet_name in sheets:
            rows = compute_readiness_matrix(sheet_name)
            self.assertIsInstance(rows, list)
            self.assertGreater(len(rows), 0, f"Sheet {sheet_name} should produce matrix rows")
            for r in rows:
                self.assertIn("label", r)
                self.assertIn("cemonc", r)
                self.assertIn("bemonc", r)
                self.assertIn("statistic", r)

    def test_detail_computation(self):
        # Bwaila Hospital facility code
        rows = compute_readiness_detail("Eqt - Maternity (AVL)", "LL040122")
        self.assertGreater(len(rows), 0)
        first = rows[0]
        self.assertIn("label", first)
        self.assertIn("status", first)
        self.assertIn("display_value", first)
        self.assertIn(first["status"], ["green", "amber", "red", "na", "awaiting", "plain"])

    def test_overview_facility_classification_and_ui(self):
        from mnid.views.operational_readiness import (
            _get_facility_classification,
            _matrix_cell,
            _plain_cell,
            _matrix_table,
            _facility_comparison_records,
            _facility_comparison_table,
        )
        # Test HFA-based classification for CEmONC and BEmONC
        cls_bwaila, _, note = _get_facility_classification("LL040122", {}, "Primary")
        self.assertEqual(cls_bwaila, "CEmONC")
        self.assertIn("Health Facility Assessment", note)

        cls_bangwe, _, _ = _get_facility_classification("BT240059", {}, "Primary")
        self.assertEqual(cls_bangwe, "BEmONC")

        # Test N/A matrix cell rendering (grey background and default visible text)
        cell_na = _matrix_cell("N/A", "CEmONC-only indicator")
        self.assertEqual(cell_na.children, "N/A")
        self.assertEqual(cell_na.style["background"], "#F1F5F9")

        cell_plain_na = _plain_cell("Not Applicable")
        self.assertEqual(cell_plain_na.children, "N/A")
        self.assertEqual(cell_plain_na.style["background"], "#F1F5F9")

        # Test empty matrix table returns clean notice rather than broken table
        empty_tbl = _matrix_table([])
        self.assertIn("No indicators with reported data", str(empty_tbl.children))

        # Test Overview comparison records and table
        classifications = {"LL040122": "CEmONC", "BT240059": "BEmONC"}
        records = _facility_comparison_records(
            ["LL040122", "BT240059"], classifications,
            {"LL040122": 500, "BT240059": 120},
            {"LL040122": 100, "BT240059": 0},
            {"LL040122": 50, "BT240059": 0},
            True,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["classification"], "CEmONC")
        self.assertEqual(records[1]["classification"], "BEmONC")

        dt = _facility_comparison_table(records)
        self.assertIsNotNone(dt)


if __name__ == "__main__":
    unittest.main()
