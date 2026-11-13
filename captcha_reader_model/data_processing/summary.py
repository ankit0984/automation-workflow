#!/usr/bin/env python3
"""
Form 10 Daily Records Summary
=============================
Builds an Excel matrix of how many records were found in each daily PDF.

By default it writes the full layout from the JSON mapping (sheet "Daily Records"),
with one column for every date that has PDFs:

    districtNAME | acNo | acName | 23/10/2026 | 24/10/2026 | ... | TOTAL

With --use-template the Excel (sheet "Form 10") follows the reporting template
"Voter Data FORM 9 & 10(Form 10).csv" exactly instead: same header, row order, names
and date columns, with only the record counts filled into the date cells. Dates not
in the template header are left out. The CSV itself is only read, never modified:

    District | Assembly Constituency | 28/10/26 | 29/10/26 | ...

District / AC mapping comes from data/districts_and_constituencies.json, and
record counts come from the same pypdfium2 extractor used by extract_form10_data.py,
so the numbers match the master Form 10 workbook exactly.

PDF filenames are expected as: form10-<districtCode>-<acNo>-<YYYY-MM-DD>-report.pdf
A blank cell means no PDF was found for that AC on that date; 0 means the PDF had no records.
"""

import re
import sys
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

try:
    from extract_form10_data import extract_records_from_pdf, DATA_ROOT
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from extract_form10_data import extract_records_from_pdf, DATA_ROOT

DEFAULT_INPUT_DIRS = [
    # DATA_ROOT/ "Form_10_29_sep", 
    DATA_ROOT / "form10_data"
    ]
DEFAULT_MAPPING_FILE = DATA_ROOT / "districts_and_constituencies.json"
# Same output folder as extract_form10_data.py (data_processing/output)
DEFAULT_OUTPUT_FILE = Path(__file__).resolve().parent / "output" / "form10_daily_records_summary.xlsx"
DEFAULT_TEMPLATE_FILE = DATA_ROOT / "Voter Data FORM 9 & 10(Form 10).csv"

FILENAME_PATTERN = re.compile(r"form\d+-([A-Za-z0-9]+)-(\d+)-(\d{4})-(\d{2})-(\d{2})", re.IGNORECASE)

FIXED_COLUMNS = ["districtNAME", "acNo", "acName"]

# Template spellings -> districts_and_constituencies.json spellings
TEMPLATE_DISTRICT_ALIASES = {
    "Firozpur": "Ferozepur",
    "SAS Nagar": "Sahibzada Ajit Singh Nagar",
    "Shaheed Bhagat Singh Nagar": "Shahid Bhagat Singh Nagar",
}
TEMPLATE_AC_ALIASES = {
    "Mahal Kalan": "Mehal Kalan",
    "Chamkaur": "Chamkaur Sahib",
}


def name_key(name: str) -> str:
    """Normalize a name for matching (case, spaces and punctuation insensitive)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def fill_template(template_path: Path, entries: List[Dict[str, str]], counts):
    """
    Read the reporting template CSV (read-only) and fill record counts into its date cells.
    Header, row order, names and date columns are kept exactly as in the CSV.
    Returns (header, rows).
    """
    by_name = {(name_key(e["districtNAME"]), name_key(e["acName"])): e for e in entries}

    with open(template_path, "r", encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))

    header = lines[0]
    date_cols = {}
    for col_idx, h in enumerate(header[2:], start=2):
        try:
            date_cols[col_idx] = datetime.strptime(h.strip(), "%d/%m/%y")
        except ValueError:
            pass

    rows = []
    filled = 0
    for line in lines[1:]:
        if len(line) < 2 or not (line[0].strip() or line[1].strip()):
            continue
        row = line + [""] * (len(header) - len(line))
        district, ac = row[0].strip(), row[1].strip()
        entry = by_name.get((
            name_key(TEMPLATE_DISTRICT_ALIASES.get(district, district)),
            name_key(TEMPLATE_AC_ALIASES.get(ac, ac)),
        ))
        if not entry:
            print(f"Warning: Template row '{district} / {ac}' has no match in the mapping file.", file=sys.stderr)
        else:
            for col_idx, date in date_cols.items():
                cnt = counts.get((entry["districtCode"], entry["acNo"], date))
                if cnt is not None:
                    row[col_idx] = cnt
                    filled += 1
        rows.append(row)

    print(f"✓ Template {template_path.name}: {len(rows)} rows, filled {filled} cell(s) across {len(date_cols)} date column(s)")
    return header, rows


def load_mapping(mapping_path: Path) -> List[Dict[str, str]]:
    """Load districts_and_constituencies.json into a flat ordered list of ACs."""
    with open(mapping_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = []
    for district in data.get("districts", []):
        for ac in district.get("constituencies", []):
            entries.append({
                "districtCode": district["districtCode"],
                "districtNAME": district["districtName"],
                "acNo": str(ac["acNumber"]).strip(),
                "acName": ac["acName"],
            })
    entries.sort(key=lambda e: (e["districtNAME"], int(e["acNo"]) if e["acNo"].isdigit() else 0))
    print(f"✓ Loaded {len(entries)} constituencies from {mapping_path.name}")
    return entries


def parse_pdf_filename(pdf_path: Path) -> Tuple[str, str, datetime]:
    """Return (districtCode, acNo, date) parsed from the PDF filename, or None."""
    m = FILENAME_PATTERN.search(pdf_path.name)
    if not m:
        return None
    dist_code, ac_no, y, mo, d = m.groups()
    return dist_code.upper(), str(int(ac_no)), datetime(int(y), int(mo), int(d))


def build_summary(data_dirs: List[Path], mapping_path: Path):
    """Scan PDFs and return (mapping entries, sorted dates, counts[(acNo, date)], unmatched files)."""
    entries = load_mapping(mapping_path)
    ac_by_key = {(e["districtCode"], e["acNo"]): e for e in entries}

    all_pdfs = []
    for data_dir in data_dirs:
        if not data_dir.exists():
            print(f"Warning: Data directory does not exist: {data_dir}", file=sys.stderr)
            continue
        all_pdfs.extend(sorted(data_dir.glob("**/*.pdf")))
    print(f"Total PDFs found: {len(all_pdfs)}\n")

    counts: Dict[Tuple[str, str, datetime], int] = {}
    dates = set()
    unmatched = []

    for idx, pdf_path in enumerate(all_pdfs, 1):
        parsed = parse_pdf_filename(pdf_path)
        if not parsed or (parsed[0], parsed[1]) not in ac_by_key:
            unmatched.append(pdf_path)
            print(f"[{idx}/{len(all_pdfs)}] SKIPPED (no AC mapping) {pdf_path.name}")
            continue

        dist_code, ac_no, date = parsed
        try:
            _, records = extract_records_from_pdf(pdf_path)
        except Exception as e:
            print(f"[{idx}/{len(all_pdfs)}] ERROR in {pdf_path.name}: {e}", file=sys.stderr)
            continue

        counts[(dist_code, ac_no, date)] = len(records)
        dates.add(date)
        if records:
            print(f"[{idx}/{len(all_pdfs)}] {pdf_path.name} -> {len(records)} record(s)")

    return entries, sorted(dates), counts, unmatched


def build_full_rows(entries, dates, counts):
    """Build the districtNAME/acNo/acName x date layout with per-AC and grand totals."""
    headers = FIXED_COLUMNS + [d.strftime("%d/%m/%Y") for d in dates] + ["TOTAL"]
    rows = []
    date_totals = [0] * len(dates)
    for entry in entries:
        row = [entry["districtNAME"], int(entry["acNo"]), entry["acName"]]
        ac_total = 0
        for i, d in enumerate(dates):
            cnt = counts.get((entry["districtCode"], entry["acNo"], d))
            row.append(cnt if cnt is not None else "")
            if cnt:
                ac_total += cnt
                date_totals[i] += cnt
        row.append(ac_total)
        rows.append(row)
    rows.append(["GRAND TOTAL", "", ""] + date_totals + [sum(date_totals)])
    return headers, rows


def save_summary_excel(
    output_excel: Path,
    headers: List[str],
    rows: List[list],
    sheet_title: str,
    text_cols: Tuple[int, ...],
    with_totals: bool = False,
):
    """
    Write headers + rows with the same sheet styling as the master workbook.
    text_cols are 1-based columns that are left aligned; with_totals bolds the last row and column.
    """
    output_excel.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.views.sheetView[0].showGridLines = True

    header_fill = PatternFill(start_color="E8EAED", end_color="E8EAED", fill_type="solid")
    header_font = Font(name="Arial", size=10, bold=True, color="202124")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    header_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="medium", color="BDC1C6"),
    )
    data_font = Font(name="Arial", size=10, color="202124")
    bold_font = Font(name="Arial", size=10, bold=True, color="202124")
    data_border = Border(
        left=Side(style="thin", color="E8EAED"),
        right=Side(style="thin", color="E8EAED"),
        top=Side(style="thin", color="E8EAED"),
        bottom=Side(style="thin", color="E8EAED"),
    )
    row_fill_white = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    row_fill_alt = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")

    ws.append(headers)
    ws.row_dimensions[1].height = 26
    # Freeze the header row and the name columns
    ws.freeze_panes = f"{get_column_letter(max(text_cols) + 1)}2"
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = header_border

    for row in rows:
        ws.append(row)

    last_row = ws.max_row
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, max_row=last_row, max_col=len(headers)), start=2):
        ws.row_dimensions[row_idx].height = 21
        is_total_row = with_totals and row_idx == last_row
        fill = header_fill if is_total_row else (row_fill_alt if row_idx % 2 == 1 else row_fill_white)
        for col_idx, cell in enumerate(row, 1):
            is_total_col = with_totals and col_idx == len(headers)
            cell.font = bold_font if (is_total_row or is_total_col) else data_font
            cell.fill = fill
            cell.border = data_border
            cell.alignment = left_align if col_idx in text_cols else center_align

    for col_idx in range(1, len(headers) + 1):
        if col_idx in text_cols:
            width = max(14, min(max(len(str(r[col_idx - 1])) for r in [headers] + rows) + 4, 45))
        else:
            width = 13 if col_idx > max(text_cols) else 8
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    wb.save(output_excel)
    print(f"\n✓ Saved summary ({len(rows)} rows x {len(headers)} columns) to: {output_excel.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="Build a district/AC x date summary of records found in each Form 10 PDF.")
    parser.add_argument(
        "--input-dir",
        "-i",
        "--data-dir",
        dest="data_dir",
        type=Path,
        nargs="+",
        default=DEFAULT_INPUT_DIRS,
        help="One or more input folders of PDFs (searched recursively)",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=DEFAULT_MAPPING_FILE,
        help="districts_and_constituencies.json mapping file",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Output Excel file path",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE_FILE,
        help="Reporting template CSV used with --use-template (District, Assembly Constituency, DD/MM/YY date columns)",
    )
    parser.add_argument(
        "--use-template",
        action="store_true",
        help="Follow the template CSV's rows and date columns instead of the full districtNAME/acNo/acName layout",
    )
    # Kept so older commands still work; the full layout is now the default.
    parser.add_argument("--no-template", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    entries, dates, counts, unmatched = build_summary(args.data_dir, args.mapping)

    if args.use_template and not args.no_template:
        headers, rows = fill_template(args.template, entries, counts)
        save_summary_excel(args.output, headers, rows, "Form 10", text_cols=(1, 2))
    else:
        headers, rows = build_full_rows(entries, dates, counts)
        save_summary_excel(args.output, headers, rows, "Daily Records", text_cols=(1, 3), with_totals=True)

    if dates:
        latest = dates[-1]
        latest_counts = [c for (_, _, d), c in counts.items() if d == latest]
        print(
            f"\n✓ Latest date {latest.strftime('%d/%m/%Y')}: {sum(latest_counts)} total record(s) "
            f"from {len(latest_counts)} PDF(s)"
        )

    if unmatched:
        print(f"Warning: {len(unmatched)} PDF(s) could not be mapped to an AC and were skipped.")


if __name__ == "__main__":
    main()
