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

    def test_render_operational_readiness_invocation(self):
        from mnid.views.operational_readiness import render_operational_readiness, _render_operational_readiness_tab, _TABS
        df = get_readiness_data()
        self.assertIsNotNone(df)

        # 1. Call with supply_inds, wf_inds, dq_inds (as called by mnid.views.renderer)
        view = render_operational_readiness(
            df,
            supply_inds=[{"id": "test_s1"}],
            wf_inds=[{"id": "test_w1"}],
            dq_inds=[{"id": "test_d1"}],
            scope_meta={"scope_type": "national"},
            start_date="2026-01-01",
            end_date="2026-06-30",
        )
        self.assertIsNotNone(view)

        # Verify store payload
        store_comp = view.children[-1]
        self.assertIn("payload_id", store_comp.data)

        # Test rendering each subtab
        for tab_val, _ in _TABS:
            outputs = _render_operational_readiness_tab(tab_val, store_comp.data)
            self.assertEqual(len(outputs), 5)
            # Find the active tab index
            tab_indices = {v: i for i, (v, _) in enumerate(_TABS)}
            active_idx = tab_indices[tab_val]
            self.assertIsNotNone(outputs[active_idx])

        # 2. Call with indicators positional or keyword
        view_with_inds = render_operational_readiness(
            df,
            indicators=[{"id": "ind1"}],
            selected_indicators=["ind1"],
        )
        self.assertIsNotNone(view_with_inds)

        # 3. Call minimal
        view_minimal = render_operational_readiness(df)
        self.assertIsNotNone(view_minimal)

    def test_signal_functions_tab_maternal_and_newborn(self):
        from mnid.views.operational_readiness import (
            _build_signal_functions_tab,
            _signal_functions_comparison,
            _signal_functions_detail,
        )
        df = get_readiness_data()
        self.assertIsNotNone(df)

        all_codes = list(df["facility_code"].unique())
        # Test comparison view
        view = _build_signal_functions_tab(all_codes, df, None, None, None)
        self.assertIsNotNone(view)

        # Test single facility detail view
        bwaila_view = _build_signal_functions_tab(["LL040122"], df, None, None, None)
        self.assertIsNotNone(bwaila_view)

        # Test matrix data for maternal signal functions has non-zero values
        matrix = compute_readiness_matrix("SF", all_codes)
        maternal = [r for r in matrix if r.get("category") == "Maternal signal functions"]
        self.assertGreater(len(maternal), 0)
        # Verify that maternal signal functions have real non-zero rates
        has_positive_cemonc = any(isinstance(r["cemonc"], (int, float)) and r["cemonc"] > 0 for r in maternal)
        has_positive_bemonc = any(isinstance(r["bemonc"], (int, float)) and r["bemonc"] > 0 for r in maternal)
        self.assertTrue(has_positive_cemonc, "CEmONC maternal signal functions should have positive rates")
        self.assertTrue(has_positive_bemonc, "BEmONC maternal signal functions should have positive rates")

    def test_occupancy_rates_flagging(self):
        from mnid.views.operational_readiness import (
            _matrix_cell,
            _systems_tab,
        )
        # 1. Test single-facility detail occupancy thresholds (<80% green, 80-100% amber, >=100% red)
        # Bwaila (LL040122): maternity occupancy ~66.7% (green), neonatal occupancy ~138.8% (red)
        mat_bwaila = [r for r in compute_readiness_detail("INF - Maternity", "LL040122") if "occupancy" in r["label"].lower()]
        self.assertEqual(len(mat_bwaila), 1)
        self.assertEqual(mat_bwaila[0]["status"], "green")
        self.assertTrue(mat_bwaila[0]["display_value"].endswith("%"))

        nb_bwaila = [r for r in compute_readiness_detail("INF - Newborn", "LL040122") if "occupancy" in r["label"].lower()]
        self.assertEqual(len(nb_bwaila), 1)
        self.assertEqual(nb_bwaila[0]["status"], "red")
        self.assertTrue(nb_bwaila[0]["display_value"].endswith("%"))

        # MZ161098: neonatal occupancy ~83.3% (amber)
        nb_mz = [r for r in compute_readiness_detail("INF - Newborn", "MZ161098") if "occupancy" in r["label"].lower()]
        self.assertEqual(len(nb_mz), 1)
        self.assertEqual(nb_mz[0]["status"], "amber")

        # MZ160388: neonatal occupancy ~41.7% (green)
        nb_mz_norm = [r for r in compute_readiness_detail("INF - Newborn", "MZ160388") if "occupancy" in r["label"].lower()]
        self.assertEqual(len(nb_mz_norm), 1)
        self.assertEqual(nb_mz_norm[0]["status"], "green")

        # 2. Test matrix row metadata
        mat_matrix = compute_readiness_matrix("INF - Maternity")
        mat_occ_rows = [r for r in mat_matrix if "occupancy" in r["label"].lower()]
        self.assertEqual(len(mat_occ_rows), 1)
        self.assertTrue(mat_occ_rows[0].get("is_occupancy"))
        self.assertTrue(mat_occ_rows[0].get("flag_over100"))

        nb_matrix = compute_readiness_matrix("INF - Newborn")
        nb_occ_rows = [r for r in nb_matrix if "occupancy" in r["label"].lower()]
        self.assertEqual(len(nb_occ_rows), 1)
        self.assertTrue(nb_occ_rows[0].get("is_occupancy"))
        self.assertTrue(nb_occ_rows[0].get("flag_over100"))

        # 3. Test _matrix_cell occupancy styling
        green_cell = _matrix_cell("45% [31-67%]", is_occupancy=True)
        self.assertEqual(green_cell.style["color"], "#15803D")
        self.assertEqual(green_cell.style["background"], "#DCFCE7")

        amber_cell = _matrix_cell("82% [9-120%]", is_occupancy=True)
        self.assertEqual(amber_cell.style["color"], "#D97706")
        self.assertEqual(amber_cell.style["background"], "#FEF3C7")

        red_cell = _matrix_cell("115% [90-140%]", is_occupancy=True)
        self.assertEqual(red_cell.style["color"], "#DC2626")
        self.assertEqual(red_cell.style["background"], "#FEE2E2")

        # 4. Test systems tab rendering
        df = get_readiness_data()
        all_codes = list(df["facility_code"].unique())
        comp_view = _systems_tab(all_codes, None, df)
        self.assertIsNotNone(comp_view)

        detail_view = _systems_tab(["LL040122"], None, df)
        self.assertIsNotNone(detail_view)

    def test_facility_profile_summation_equals_67(self):
        from mnid.views.operational_readiness import _facility_profile_rows, _facility_type_by_code
        df = get_readiness_data()
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 67)

        all_codes = list(df["facility_code"].unique())
        self.assertEqual(len(all_codes), 67)

        emonc = get_facility_emonc_classification()
        cemonc_group = [c for c in all_codes if emonc.get(c) == "CEmONC"]
        bemonc_group = [c for c in all_codes if emonc.get(c) == "BEmONC"]

        self.assertEqual(len(cemonc_group), 19)
        self.assertEqual(len(bemonc_group), 48)

        rows = _facility_profile_rows(all_codes, cemonc_group, bemonc_group)
        self.assertEqual(len(rows), 4)

        labels = [r["label"] for r in rows]
        self.assertEqual(labels, ["Central Hospital", "District Hospital", "Hospital", "Health Centre"])

        total_sum = sum(int(r["total"]) for r in rows)
        cemonc_sum = sum(int(r["cemonc"]) for r in rows)
        bemonc_sum = sum(int(r["bemonc"]) for r in rows)

        self.assertEqual(total_sum, 67, "Total facilities in Facility Profile must sum to 67")
        self.assertEqual(cemonc_sum, 19, "CEmONC facilities in Facility Profile must sum to 19")
        self.assertEqual(bemonc_sum, 48, "BEmONC facilities in Facility Profile must sum to 48")

        # Check individual row counts
        by_label = {r["label"]: r for r in rows}
        self.assertEqual(int(by_label["Central Hospital"]["total"]), 3)
        self.assertEqual(int(by_label["Central Hospital"]["cemonc"]), 3)
        self.assertEqual(int(by_label["Central Hospital"]["bemonc"]), 0)

        self.assertEqual(int(by_label["District Hospital"]["total"]), 2)
        self.assertEqual(int(by_label["District Hospital"]["cemonc"]), 2)
        self.assertEqual(int(by_label["District Hospital"]["bemonc"]), 0)

        self.assertEqual(int(by_label["Hospital"]["total"]), 15)
        self.assertEqual(int(by_label["Hospital"]["cemonc"]), 11)
        self.assertEqual(int(by_label["Hospital"]["bemonc"]), 4)

        self.assertEqual(int(by_label["Health Centre"]["total"]), 47)
        self.assertEqual(int(by_label["Health Centre"]["cemonc"]), 3)
        self.assertEqual(int(by_label["Health Centre"]["bemonc"]), 44)


if __name__ == "__main__":
    unittest.main()
