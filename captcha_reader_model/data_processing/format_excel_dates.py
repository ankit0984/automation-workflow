#!/usr/bin/env python3
"""
Convert text dates in an Excel workbook into real Excel date cells.

The extractor stores dates such as GENERATION DATE as text ("23/10/2026"), so Excel
cannot sort, filter or calculate on them as dates. This script rewrites every column
whose header contains "DATE" as a true Excel date shown as DD/MM/YYYY, keeping all
other values and cell styling.

By default it writes a separate client copy (<name>_formatted.xlsx) and leaves the
master untouched, because push_to_sheet.py and extract_form10_data.py read the
master back expecting DD/MM/YYYY text.

Usage:
    python format_excel_dates.py                                # default master -> *_formatted.xlsx
    python format_excel_dates.py input.xlsx -o client.xlsx
    python format_excel_dates.py input.xlsx --format "DD-MMM-YYYY"
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, date

import openpyxl

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "output" / "form10_data.xlsx"

# Text formats seen in the PDFs / sheets, tried in order (day first, Indian style)
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y", "%Y-%m-%d %H:%M:%S")


def parse_date(value):
    """Return a datetime.date for a text/date value, or None if it isn't a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def format_workbook(input_path: Path, output_path: Path, number_format: str) -> None:
    print(f"Loading {input_path} ...")
    wb = openpyxl.load_workbook(input_path)

    for ws in wb.worksheets:
        # Find date columns from the header row
        date_cols = [
            cell.column
            for cell in ws[1]
            if isinstance(cell.value, str) and "DATE" in cell.value.upper()
        ]
        if not date_cols:
            print(f"  [{ws.title}] no date columns, skipped")
            continue

        converted, unparsed = 0, 0
        for col in date_cols:
            for (cell,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
                if cell.value in (None, ""):
                    continue
                d = parse_date(cell.value)
                if d is None:
                    unparsed += 1
                    continue
                cell.value = d
                cell.number_format = number_format
                converted += 1

        headers = ", ".join(str(ws.cell(row=1, column=c).value) for c in date_cols)
        print(f"  [{ws.title}] {headers}: {converted} converted, {unparsed} left as text (unrecognised)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    print(f"✓ Saved: {output_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="Convert text dates in an Excel file into real Excel dates.")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT, help="Excel file to convert")
    parser.add_argument("-o", "--output", type=Path, help="Output file (default: <input>_formatted.xlsx)")
    parser.add_argument("--format", default="DD/MM/YYYY", help="Excel display format (default: DD/MM/YYYY)")
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Error: file not found: {args.input}")

    output = args.output or args.input.with_name(f"{args.input.stem}_formatted.xlsx")
    format_workbook(args.input, output, args.format)


if __name__ == "__main__":
    main()
