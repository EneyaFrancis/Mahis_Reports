"""
CLI / Tool to translate and merge Excel facility data (from data/excel/NEST_BF_facilitites.xlsx)
into the aggregated DHIS2 indicator dataset at data/mnid_aggregates/dhis2/indicator_aggregates.parquet.

Priority:
For the 7 facilities in the Excel sheet, the Excel records take precedence over DHIS2.
Indicators not present in Excel for those facilities are preserved from DHIS2.
All other facilities remain sourced from DHIS2.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mnid.dhis2.tools.excel_to_dhis2_parquet import (
    DEFAULT_INPUT_FILE,
    DEFAULT_MNID_AGGREGATE_FILE,
    DEFAULT_MNID_META_FILE,
    merge_excel_into_indicator_aggregates,
)

_LOG = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge Excel facility indicator data into DHIS2 indicator_aggregates.parquet"
    )
    parser.add_argument(
        "--excel-file",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help="Path to the source Excel workbook",
    )
    parser.add_argument(
        "--target-parquet",
        type=Path,
        default=DEFAULT_MNID_AGGREGATE_FILE,
        help="Path to target indicator_aggregates.parquet",
    )
    parser.add_argument(
        "--target-meta",
        type=Path,
        default=DEFAULT_MNID_META_FILE,
        help="Path to target meta.json",
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
        res = merge_excel_into_indicator_aggregates(
            excel_input_file=args.excel_file,
            target_parquet_path=args.target_parquet,
            target_meta_path=args.target_meta,
        )
        print(json.dumps(res, indent=2))
        return 0
    except Exception as exc:
        _LOG.exception("Merge failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
