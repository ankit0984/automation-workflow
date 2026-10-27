#!/usr/bin/env python3
"""
Merge New Daily Form 10 Records into Master consolidated_data.xlsx
===================================================================
Appends newly extracted records from output/consolidated_form10.xlsx
into consolidated_data.xlsx with Google Sheets styling and deduplication.
"""

import sys
import re
import shutil
from pathlib import Path
from datetime import datetime
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_FILE = SCRIPT_DIR / "output" / "form_10.xlsx"
NEW_FILE = SCRIPT_DIR / "output" / "consolidated_form10.xlsx"

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


def parse_date(d_str):
    if not d_str:
        return datetime.min
    s = str(d_str).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.min


def parse_int(v):
    if not v:
        return 0
    m = re.search(r"\b(\d+)\b", str(v))
    return int(m.group(1)) if m else 0


ASSEMBLY_NAME_MAPPING = {
    "71 - Nihal Singh Wala": "71 - Nihal Singhwala",
    "Nihal Singh Wala": "Nihal Singhwala",
    "37 - Jalandhar Cantt.": "37 - Jalandhar Cantt",
    "Jalandhar Cantt.": "Jalandhar Cantt",
    "72 - Bagha Purana": "72 - Bhagha Purana",
    "Bagha Purana": "Bhagha Purana",
}


def normalize_assembly_name(name):
    if not name or pd.isna(name):
        return ""
    val = re.sub(r"[\r\n]+", " ", str(name))
    val = re.sub(r"\s+", " ", val).strip()

    if val in ASSEMBLY_NAME_MAPPING:
        return ASSEMBLY_NAME_MAPPING[val]

    val = re.sub(r"^71\s*-\s*Nihal\s+Singh\s*Wala$", "71 - Nihal Singhwala", val, flags=re.IGNORECASE)
    val = re.sub(r"^Nihal\s+Singh\s*Wala$", "Nihal Singhwala", val, flags=re.IGNORECASE)
    val = re.sub(r"^37\s*-\s*Jalandhar\s+Cantt\.?$", "37 - Jalandhar Cantt", val, flags=re.IGNORECASE)
    val = re.sub(r"^Jalandhar\s+Cantt\.?$", "Jalandhar Cantt", val, flags=re.IGNORECASE)
    val = re.sub(r"^72\s*-\s*Bagha\s+Purana$", "72 - Bhagha Purana", val, flags=re.IGNORECASE)
    val = re.sub(r"^Bagha\s+Purana$", "Bhagha Purana", val, flags=re.IGNORECASE)

    return val


def merge_files(base_path: Path = BASE_FILE, new_path: Path = NEW_FILE):
    if not base_path.exists():
        print(f"Error: Base file not found: {base_path}", file=sys.stderr)
        return
    if not new_path.exists():
        print(f"Error: New file not found: {new_path}", file=sys.stderr)
        return

    print(f"Loading master file: {base_path}")
    df_base = pd.read_excel(base_path, sheet_name="FORM 10", dtype=str).fillna("")
    if "Assembly Name" in df_base.columns:
        df_base["Assembly Name"] = df_base["Assembly Name"].apply(normalize_assembly_name)
    print(f"  Existing rows: {len(df_base)}")

    print(f"Loading new file:    {new_path}")
    df_new = pd.read_excel(new_path, sheet_name="Consolidated Data", dtype=str).fillna("")
    if "Assembly Name" in df_new.columns:
        df_new["Assembly Name"] = df_new["Assembly Name"].apply(normalize_assembly_name)
    print(f"  New rows:      {len(df_new)}")

    for col in EXPECTED_COLUMNS:
        if col not in df_base.columns:
            df_base[col] = ""
        if col not in df_new.columns:
            df_new[col] = ""

    df_combined = pd.concat([df_base[EXPECTED_COLUMNS], df_new[EXPECTED_COLUMNS]], ignore_index=True)

    # Deduplicate
    dedup_cols = ["DISTRICT", "Assembly Name", "GENERATION DATE", "Booth Name", "SERIAL NUMBER", "NAME IN FULL"]
    df_combined = df_combined.drop_duplicates(subset=dedup_cols, keep="last")

    # Chronological sort
    records = df_combined.to_dict("records")
    records.sort(
        key=lambda r: (
            parse_date(r.get("GENERATION DATE", "")),
            r.get("DISTRICT", ""),
            parse_int(r.get("Assembly Name", "")),
            parse_int(r.get("Booth Name", "")),
            parse_int(r.get("SERIAL NUMBER", "")),
        )
    )

    print("Building styled workbook (Google Sheets theme)...")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FORM 10"
    ws.views.sheetView[0].showGridLines = True

    # Google Sheets Theme Styles
    header_fill = PatternFill(start_color="E8EAED", end_color="E8EAED", fill_type="solid")
    header_font = Font(name="Arial", size=10, bold=True, color="202124")
    header_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="medium", color="BDC1C6"),
    )
    data_font = Font(name="Arial", size=10, color="202124")
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

    ws.append(EXPECTED_COLUMNS)
    ws.row_dimensions[1].height = 26
    ws.freeze_panes = "A2"
    ws.sheet_format.defaultRowHeight = 21

    for col_idx, col_name in enumerate(EXPECTED_COLUMNS, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = header_border

    for rec in records:
        ws.append([rec.get(col, "") for col in EXPECTED_COLUMNS])

    total_rows = len(records)
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
            cell.alignment = center_align if col_idx in (3, 4, 6, 7, 8) else left_align

    sample_records = records[:500] + records[-500:]
    for col_idx, col_name in enumerate(EXPECTED_COLUMNS, 1):
        max_len = len(col_name)
        for rec in sample_records:
            val_len = len(str(rec.get(col_name, "") or ""))
            if val_len > max_len:
                max_len = val_len
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = max(14, min(max_len + 4, 45))

    # Backup prior file
    backup_path = base_path.with_suffix(".bak.xlsx")
    shutil.copy2(base_path, backup_path)

    wb.save(base_path)
    print(f"✓ Saved {total_rows} total merged rows to: {base_path}")


if __name__ == "__main__":
    merge_files()
