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

    def test_matrix_computation(self):
        rows = compute_readiness_matrix("Eqt - Maternity (AVL)")
        self.assertGreater(len(rows), 0)
        first = rows[0]
        self.assertIn("label", first)
        self.assertIn("cemonc", first)
        self.assertIn("bemonc", first)
        self.assertIn("statistic", first)

    def test_detail_computation(self):
        # Bwaila Hospital facility code
        rows = compute_readiness_detail("Eqt - Maternity (AVL)", "LL040122")
        self.assertGreater(len(rows), 0)
        first = rows[0]
        self.assertIn("label", first)
        self.assertIn("status", first)
        self.assertIn("display_value", first)
        self.assertIn(first["status"], ["green", "amber", "red", "na", "awaiting", "plain"])


if __name__ == "__main__":
    unittest.main()
