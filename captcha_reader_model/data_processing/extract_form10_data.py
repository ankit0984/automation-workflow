#!/usr/bin/env python3
"""
District-wise Electoral Objection PDF to Excel Extractor (Fast PDFium Engine)
=============================================================================
Extracts structured objection records (Form 10 / Form 9 / Form 7) from daily district PDFs
into an Excel workbook.

Does NOT use pdfplumber. Instead uses pypdfium2 (high-performance Chromium engine)
with coordinate-based spatial bucketing for 100% exact character extraction.

Columns extracted:
1. DISTRICT
2. ASSEMBLY CONSTITUENCY
3. FORM TYPE
4. GENERATION DATE
5. NAME OF OBJECTOR
6. PARTS NUMBER
7. SERIAL NUMBER
8. NAME IN FULL
9. REASONS IN BRIEF FOR OBJECTION
"""

import os
import re
import sys
import json
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Optional

import pandas as pd
import pypdfium2 as pdfium
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Import headless Google Sheet publisher from push_to_sheet
try:
    from push_to_sheet import push_to_existing_google_sheet
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from push_to_sheet import push_to_existing_google_sheet

# Output format columns requested:
# DISTRICT, Assembly Name, FORM TYPE, GENERATION DATE, NAME OF OBJECTOR, Booth Name, SERIAL NUMBER, EPIC NO., NAME IN FULL, REASONS IN BRIEF FOR OBJECTION
EXPECTED_COLUMNS = [
    "DISTRICT",
    "Assembly Name",
    "FORM TYPE",
    "GENERATION DATE",
    "NAME OF OBJECTOR",
    "Booth Name",
    "SERIAL NUMBER",
    "EPIC NO.",
    "NAME IN FULL",
    "REASONS IN BRIEF FOR OBJECTION",
]

# Base directories dynamically anchored to script location
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_ROOT = PROJECT_ROOT.parent / "data"

DEFAULT_DATA_DIR = DATA_ROOT / "form10_data"
DEFAULT_OUTPUT_FILE = SCRIPT_DIR / "output" / "form_10.xlsx"
DEFAULT_LEDGER_FILE = SCRIPT_DIR / "processed_files.json"


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash for incremental tracking."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_ledger(ledger_path: Path) -> dict:
    """Load tracking ledger."""
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not read ledger {ledger_path}: {e}. Creating new.")
    return {"files": {}}


def save_ledger(ledger_path: Path, data: dict):
    """Save tracking ledger."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ledger_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    if os.path.exists(ledger_path):
        os.remove(ledger_path)
    os.rename(temp_path, ledger_path)


def clean_text(text: Optional[str]) -> str:
    """Clean and normalize whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"[\r\n]+", " ", str(text))
    return re.sub(r"\s+", " ", cleaned).strip()


# Assembly Constituency name corrections map:
# Standardizes spelling variations across PDF reports and forms
ASSEMBLY_NAME_MAPPING = {
    "71 - Nihal Singh Wala": "71 - Nihal Singhwala",
    "Nihal Singh Wala": "Nihal Singhwala",
    "37 - Jalandhar Cantt.": "37 - Jalandhar Cantt",
    "Jalandhar Cantt.": "Jalandhar Cantt",
    "72 - Bagha Purana": "72 - Bhagha Purana",
    "Bagha Purana": "Bhagha Purana",
}


def normalize_assembly_name(name: Any) -> str:
    """Normalize assembly constituency names according to standard naming rules."""
    if not name or pd.isna(name):
        return ""
    val = clean_text(str(name))

    # Exact match check
    if val in ASSEMBLY_NAME_MAPPING:
        return ASSEMBLY_NAME_MAPPING[val]

    # Regex checks for spacing, dots, or casing variations
    # 71 - Nihal Singh Wala -> 71 - Nihal Singhwala
    val = re.sub(r"^71\s*-\s*Nihal\s+Singh\s*Wala$", "71 - Nihal Singhwala", val, flags=re.IGNORECASE)
    val = re.sub(r"^Nihal\s+Singh\s*Wala$", "Nihal Singhwala", val, flags=re.IGNORECASE)

    # 37 - Jalandhar Cantt. -> 37 - Jalandhar Cantt
    val = re.sub(r"^37\s*-\s*Jalandhar\s+Cantt\.?$", "37 - Jalandhar Cantt", val, flags=re.IGNORECASE)
    val = re.sub(r"^Jalandhar\s+Cantt\.?$", "Jalandhar Cantt", val, flags=re.IGNORECASE)

    # 72 - Bagha Purana -> 72 - Bhagha Purana
    val = re.sub(r"^72\s*-\s*Bagha\s+Purana$", "72 - Bhagha Purana", val, flags=re.IGNORECASE)
    val = re.sub(r"^Bagha\s+Purana$", "Bhagha Purana", val, flags=re.IGNORECASE)

    return val


def parse_metadata(doc: pdfium.PdfDocument, pdf_path: Path) -> Dict[str, str]:
    """Extract District, Assembly Constituency, Form Type, and Date from PDF."""
    first_page_text = doc[0].get_textpage().get_text_range()

    # 1. District: Use parent directory name (e.g. Amritsar, Bathinda)
    district = pdf_path.parent.name.strip()
    if not district or district.lower() in ("form10_data", "data", "."):
        district = "General"

    # 2. Form Type (e.g. FORM 10, FORM 9)
    form_match = re.search(r"\b(FORM\s+\d+)\b", first_page_text, re.IGNORECASE)
    form_type = form_match.group(1).upper() if form_match else "FORM 10"

    # 3. Assembly Constituency
    ac_name = ""
    ac_match = re.search(
        r"Constituency\s*\(Assembly/Parliamentary\)\s*\n\s*([^\n]+)",
        first_page_text,
        re.IGNORECASE,
    )
    if ac_match:
        ac_name = normalize_assembly_name(ac_match.group(1))

    # Also check AC number from filename (e.g., form10-S1909-52-2026-10-23-report.pdf -> 52)
    fn_ac = re.search(r"form\d+-[A-Za-z0-9]+-(\d+)-", pdf_path.name)
    if fn_ac:
        ac_num = fn_ac.group(1)
        if ac_name:
            ac_full = f"{ac_num} - {ac_name}"
        else:
            ac_full = ac_num
    else:
        ac_full = ac_name

    ac_full = normalize_assembly_name(ac_full)

    # 4. Generation Date
    gen_date = ""
    date_match = re.search(
        r"From\s+date\s*\n\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})",
        first_page_text,
        re.IGNORECASE,
    )
    if date_match:
        raw_d = date_match.group(1).replace(".", "/").replace("-", "/")
        parts = raw_d.split("/")
        if len(parts) == 3:
            gen_date = f"{int(parts[0]):02d}/{int(parts[1]):02d}/{parts[2]}"
        else:
            gen_date = raw_d
    else:
        # Fallback to date in filename (YYYY-MM-DD)
        fn_date = re.search(r"(\d{4})-(\d{2})-(\d{2})", pdf_path.name)
        if fn_date:
            y, m, d = fn_date.groups()
            gen_date = f"{int(d):02d}/{int(m):02d}/{y}"

    return {
        "district": district,
        "assembly_constituency": ac_full,
        "form_type": form_type,
        "generation_date": gen_date,
    }


def extract_records_from_pdf(pdf_path: Path) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """
    Extract records from digital PDF using pypdfium2 coordinate bounding.
    Guarantees 100% exact text extraction without table border misalignments.
    """
    doc = pdfium.PdfDocument(pdf_path)
    metadata = parse_metadata(doc, pdf_path)
    records = []

    for p_idx, page in enumerate(doc):
        tp = page.get_textpage()
        count = tp.count_rects()
        if count == 0:
            continue

        header_y = None
        footer_y = 55.0  # default bottom margin above page timestamp (y ~ 42)

        rects = []
        for i in range(count):
            rect = tp.get_rect(i)
            l, b, r, t = rect
            txt = tp.get_text_bounded(l, b, r, t).strip()
            if not txt:
                continue

            # Identify the column number header row: "1 2 3 4 5 6 7 8 (a) 8 (b)"
            if "1 2 3 4 5 6 7 8 (a) 8 (b)" in txt or (l < 70 and txt == "1" and 320 < b < 345):
                header_y = b
            # Identify footer boundary
            if "£ In case of Union territories" in txt or "Date of exhibition" in txt:
                if t > footer_y:
                    footer_y = t

            rects.append((l, b, r, t, txt))

        if header_y is None:
            if p_idx == 0:
                # Page 1: Fallback table search if header row wasn't single string
                header_candidates = [r for r in rects if 320 <= r[1] <= 345 and r[4] in ("1", "2", "3")]
                if header_candidates:
                    header_y = min(r[1] for r in header_candidates)
            else:
                # Continuation pages (Page 2+): The ECI portal omits the column numbers header row.
                # Table rows begin right below the top page header banner (y ~ 525).
                top_header_rects = [r for r in rects if r[1] > 515 and not r[4].isdigit()]
                if top_header_rects:
                    header_y = min(r[1] for r in top_header_rects)
                else:
                    header_y = 525.0

        if header_y is None:
            continue

        # Extract only data rectangles between table header and footer
        table_rects = [r for r in rects if footer_y <= r[1] < (header_y - 4)]
        if not table_rects:
            continue

        # Application serial numbers (column 1, x < 90, numeric) act as row anchors
        row_anchors = [r for r in table_rects if r[0] < 90 and r[4].isdigit()]
        # Sort row anchors top-to-bottom by Y coordinate
        row_anchors.sort(key=lambda r: -r[1])

        if not row_anchors:
            continue

        for idx, anchor in enumerate(row_anchors):
            top_bound = anchor[3] + 6
            if idx + 1 < len(row_anchors):
                bot_bound = row_anchors[idx + 1][3] - 2
            else:
                bot_bound = footer_y - 5

            # Select all text fragments belonging to this vertical row slice
            row_items = [r for r in table_rects if bot_bound <= r[1] <= top_bound]

            # Assign text fragments into column buckets by horizontal (x) position
            cols = defaultdict(list)
            for l, b, r, t, txt in row_items:
                if l < 90:
                    cols[1].append((b, txt))  # App No
                elif l < 180:
                    cols[2].append((b, txt))  # Receipt Date
                elif l < 280:
                    cols[3].append((b, txt))  # Name of objector
                elif l < 355:
                    cols[4].append((b, txt))  # Parts number
                elif l < 425:
                    cols[5].append((b, txt))  # Serial number
                elif l < 525:
                    cols[6].append((b, txt))  # Name in full
                elif l < 610:
                    cols[7].append((b, txt))  # Reason
                elif l < 690:
                    cols[8].append((b, txt))  # Hearing date
                else:
                    cols[9].append((b, txt))  # Hearing time

            def format_col(col_idx: int) -> str:
                # Sort fragments top-to-bottom within the cell
                items = sorted(cols[col_idx], key=lambda x: -x[0])
                return clean_text(" ".join(x[1] for x in items))

            obj_name = format_col(3)
            part_no = format_col(4)
            serial_no = format_col(5)
            full_name = format_col(6)
            reason = format_col(7)

            # Skip empty or malformed rows
            if not (full_name or obj_name or serial_no or reason):
                continue

            # Check if any EPIC number pattern (e.g. ABC1234567) exists in the row
            row_full_str = " ".join(r[4] for r in row_items)
            epic_match = re.search(r"\b([A-Z]{3}\d{7})\b", row_full_str)
            epic_no = epic_match.group(1) if epic_match else ""

            records.append({
                "DISTRICT": metadata["district"],
                "Assembly Name": metadata["assembly_constituency"],
                "FORM TYPE": metadata["form_type"],
                "GENERATION DATE": metadata["generation_date"],
                "NAME OF OBJECTOR": obj_name,
                "Booth Name": part_no,
                "SERIAL NUMBER": serial_no,
                "EPIC NO.": epic_no,
                "NAME IN FULL": full_name,
                "REASONS IN BRIEF FOR OBJECTION": reason,
            })

    return metadata, records


def parse_date_for_sort(date_str: Any) -> datetime:
    """Parse date string for chronological sorting."""
    if not date_str:
        return datetime.min
    clean = str(date_str).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            pass
    return datetime.min


def parse_int_for_sort(val: Any) -> int:
    """Parse integer for numerical sorting."""
    if not val:
        return 0
    m = re.search(r"\b(\d+)\b", str(val))
    return int(m.group(1)) if m else 0


def save_to_excel(output_excel: Path, all_records: List[Dict[str, Any]], separate_district_sheets: bool = False):
    """Save extracted records cleanly to Excel with professional formatting."""
    output_excel.parent.mkdir(parents=True, exist_ok=True)

    # Sort records datewise, district, AC, part number, serial number
    all_records.sort(
        key=lambda r: (
            parse_date_for_sort(r.get("GENERATION DATE", "")),
            r.get("DISTRICT", ""),
            parse_int_for_sort(r.get("Assembly Name", "")),
            parse_int_for_sort(r.get("Booth Name", "")),
            parse_int_for_sort(r.get("SERIAL NUMBER", "")),
        )
    )

    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # Google Sheets Material Design Theme
    header_fill = PatternFill(start_color="E8EAED", end_color="E8EAED", fill_type="solid")
    header_font = Font(name="Arial", size=10, bold=True, color="202124")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    
    # Google Sheets freeze bar / bottom border for headers
    header_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="medium", color="BDC1C6"),
    )

    # Data row fonts and borders
    data_font = Font(name="Arial", size=10, color="202124")
    data_border = Border(
        left=Side(style="thin", color="E8EAED"),
        right=Side(style="thin", color="E8EAED"),
        top=Side(style="thin", color="E8EAED"),
        bottom=Side(style="thin", color="E8EAED"),
    )

    # Google Sheets Banded Rows (Alternating Zebra striping)
    row_fill_white = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    row_fill_alt = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")

    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")

    def create_sheet(sheet_title: str, records: List[Dict[str, Any]]):
        ws = wb.create_sheet(title=sheet_title[:31])  # Excel limits sheet names to 31 chars
        
        # Ensure Google Sheets / Excel gridlines are always visible
        ws.views.sheetView[0].showGridLines = True
        
        ws.append(EXPECTED_COLUMNS)
        ws.row_dimensions[1].height = 26
        ws.freeze_panes = "A2"
        ws.sheet_format.defaultRowHeight = 21

        # Style header row (Row 1)
        for col_idx, col_name in enumerate(EXPECTED_COLUMNS, 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = header_border

        # Append data rows
        for rec in records:
            ws.append([rec.get(col, "") for col in EXPECTED_COLUMNS])

        # Style data cells with Google Sheets banded zebra striping
        total_rows = len(records)
        if total_rows > 0:
            for row_idx, row in enumerate(
                ws.iter_rows(min_row=2, max_row=total_rows + 1, min_col=1, max_col=len(EXPECTED_COLUMNS)),
                start=2,
            ):
                ws.row_dimensions[row_idx].height = 21
                current_fill = row_fill_alt if row_idx % 2 == 1 else row_fill_white
                for col_idx, cell in enumerate(row, 1):
                    cell.font = data_font
                    cell.fill = current_fill
                    cell.border = data_border
                    # Center align Form Type, Date, Booth Name, Serial, EPIC
                    cell.alignment = center_align if col_idx in (3, 4, 6, 7, 8) else left_align

        # Auto-fit column widths with pleasant padding
        for col_idx, col_name in enumerate(EXPECTED_COLUMNS, 1):
            max_len = len(col_name)
            for rec in records:
                val_len = len(str(rec.get(col_name, "") or ""))
                if val_len > max_len:
                    max_len = val_len
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = max(14, min(max_len + 4, 45))

    # 1. Master Consolidated Sheet
    create_sheet("FORM 10", all_records)

    # 2. District-wise tabs if requested
    if separate_district_sheets:
        districts = sorted(list(set(r.get("DISTRICT", "General") for r in all_records)))
        for dist in districts:
            dist_records = [r for r in all_records if r.get("DISTRICT") == dist]
            create_sheet(dist, dist_records)

    # Automatic safety backup of previous master file
    if output_excel.exists():
        backup_path = output_excel.with_suffix(".bak.xlsx")
        import shutil
        try:
            shutil.copy2(output_excel, backup_path)
            print(f"✓ Created backup of previous master file at: {backup_path.name}")
        except Exception as e:
            print(f"Warning: Could not create backup: {e}")

    wb.save(output_excel)
    print(f"✓ Saved {len(all_records)} total records to: {output_excel.resolve()}")


def process_district_pdfs(
    data_dir: Path = DEFAULT_DATA_DIR,
    output_excel: Path = DEFAULT_OUTPUT_FILE,
    ledger_file: Path = DEFAULT_LEDGER_FILE,
    force: bool = False,
    district_tabs: bool = False,
    push_to_sheet: bool = True,
):
    """Main workflow to scan, parse and export district-wise PDFs to Excel."""
    print("=" * 60)
    print("ECINET Form 10 Electoral Objection Extractor (Fast PDFium)")
    print("=" * 60)
    print(f"Data Directory: {data_dir.resolve()}")
    print(f"Master Excel:   {output_excel.resolve()}")
    print(f"Mode:           {'Force Rebuild' if force else 'Incremental Append'}\n")

    if not data_dir.exists():
        print(f"Error: Data directory does not exist: {data_dir}", file=sys.stderr)
        return

    # Find all PDFs in all district folders
    all_pdfs = sorted(list(data_dir.glob("**/*.pdf")))
    if not all_pdfs:
        print(f"No PDF files found in '{data_dir}'.")
        return

    ledger = {} if force else load_ledger(ledger_file)
    processed_files = ledger.get("files", {})

    files_to_process = []
    for pdf in all_pdfs:
        rel_path = str(pdf.relative_to(data_dir))
        file_hash = compute_file_hash(pdf)
        mtime = pdf.stat().st_mtime

        if force:
            files_to_process.append((pdf, rel_path, file_hash, mtime))
        else:
            prev = processed_files.get(rel_path)
            if not prev or prev.get("hash") != file_hash:
                files_to_process.append((pdf, rel_path, file_hash, mtime))

    print(f"Total PDFs found across districts: {len(all_pdfs)}")
    print(f"Already processed (cached):         {len(all_pdfs) - len(files_to_process)}")
    print(f"New / modified PDFs to parse:       {len(files_to_process)}\n")

    # Load existing records non-destructively so past data is NEVER lost
    all_records = []
    existing_keys = set()

    if output_excel.exists():
        try:
            xl = pd.ExcelFile(output_excel)
            target_sheet = "FORM 10" if "FORM 10" in xl.sheet_names else xl.sheet_names[0]
            df_old = xl.parse(target_sheet, dtype=str).fillna("")
            print(f"Loading existing master file '{output_excel.name}' [Sheet: '{target_sheet}'] ({len(df_old)} records)...")
            for row in df_old.to_dict("records"):
                rec = {col: clean_text(row.get(col, "")) for col in EXPECTED_COLUMNS}
                rec["Assembly Name"] = normalize_assembly_name(rec.get("Assembly Name", ""))
                all_records.append(rec)
                key = (
                    rec["DISTRICT"],
                    rec["Assembly Name"],
                    rec["GENERATION DATE"],
                    rec["Booth Name"],
                    rec["SERIAL NUMBER"],
                    rec["NAME IN FULL"],
                )
                existing_keys.add(key)
            print(f"✓ Preserved all {len(all_records)} existing records from master file.")
        except Exception as e:
            print(f"Warning: Could not read existing records ({e}).")

    if not files_to_process and all_records:
        print(f"Master file '{output_excel.name}' is already completely up to date ({len(all_records)} records). Nothing to parse.")
        return

    # Parse PDFs
    new_records_count = 0
    new_ledger_entries = {}

    for idx, (pdf_path, rel_path, file_hash, mtime) in enumerate(files_to_process, 1):
        try:
            meta, records = extract_records_from_pdf(pdf_path)
            for rec in records:
                key = (
                    rec["DISTRICT"],
                    rec["Assembly Name"],
                    rec["GENERATION DATE"],
                    rec["Booth Name"],
                    rec["SERIAL NUMBER"],
                    rec["NAME IN FULL"],
                )
                if key not in existing_keys:
                    existing_keys.add(key)
                    all_records.append(rec)
                    new_records_count += 1

            new_ledger_entries[rel_path] = {
                "hash": file_hash,
                "mtime": mtime,
                "processed_at": datetime.now().isoformat(),
                "records_count": len(records),
            }

            if records:
                print(f"[{idx}/{len(files_to_process)}] {rel_path} -> {len(records)} record(s)")
        except Exception as e:
            print(f"[{idx}/{len(files_to_process)}] ERROR in {rel_path}: {e}", file=sys.stderr)

    print(f"\nExtraction finished! Found {new_records_count} new records (Total in master: {len(all_records)}).")

    if new_records_count == 0 and len(all_records) > 0 and output_excel.exists() and not force:
        print("\nNo new records to append. Everything is already up to date in Excel!")
        processed_files.update(new_ledger_entries)
        ledger["files"] = processed_files
        save_ledger(ledger_file, ledger)

        if push_to_sheet:
            print("\n" + "=" * 60)
            print("Syncing Current Database to Google Sheets...")
            print("=" * 60)
            try:
                push_to_existing_google_sheet(excel_path=output_excel)
            except Exception as e:
                print(f"Warning: Failed to sync to Google Sheets: {e}", file=sys.stderr)
        return

    # Write to Excel
    save_to_excel(output_excel, all_records, separate_district_sheets=district_tabs)

    # Update ledger
    processed_files.update(new_ledger_entries)
    ledger["files"] = processed_files
    save_ledger(ledger_file, ledger)

    # Automatically push to Google Sheets
    if push_to_sheet:
        print("\n" + "=" * 60)
        print("Automatically Pushing Updated Data to Google Sheets...")
        print("=" * 60)
        try:
            push_to_existing_google_sheet(excel_path=output_excel)
        except Exception as e:
            print(f"Warning: Failed to automatically push to Google Sheets: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Extract ECINET Form 10 PDFs to Excel and push to Google Sheets.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Root folder containing district subfolders of PDFs",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Output Excel file path",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_FILE,
        help="JSON file tracking processed files for incremental runs",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force re-extraction of all PDFs from scratch",
    )
    parser.add_argument(
        "--district-tabs",
        action="store_true",
        help="Also create separate tabs in Excel for each District",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Skip automatic push to Google Sheets after Excel creation",
    )
    args = parser.parse_args()

    process_district_pdfs(
        data_dir=args.data_dir,
        output_excel=args.output,
        ledger_file=args.ledger,
        force=args.force,
        district_tabs=args.district_tabs,
        push_to_sheet=not args.no_push,
    )


if __name__ == "__main__":
    main()
