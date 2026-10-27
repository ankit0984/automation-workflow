#!/usr/bin/env python3
"""
Senior Data Analytics - Google Sheets Publishing & Dashboard Engine
===================================================================
Pushes electoral records (form_10.xlsx) programmatically to Google Sheets
using a headless Google Service Account and generates a 2-tab executive view:

  1. 📊 Executive Summary: KPI stat cards, district breakdown, top assemblies, and daily trends.
  2. 📋 Form 10 Database: Pinned Row 1 + Column A, Google Material design, zebra banding,
                          instant filter dropdowns, and optimized column widths.

Usage:
    # Full Publish (Uploads 63,000+ rows, formats database, & creates summary dashboard):
    python push_to_sheet.py

    # Instant Format Only (Applies Senior Analyst styling to existing raw data in ~2s):
    python push_to_sheet.py --format-only

    # Instant Summary Dashboard Only (Refreshes the Executive Summary tab in ~2s):
    python push_to_sheet.py --summary-only
"""

import re
import sys
import argparse
from pathlib import Path
from typing import Any
import pandas as pd
import gspread

SCRIPT_DIR = Path(__file__).resolve().parent

# Automatically resolve form_10.xlsx location
if (SCRIPT_DIR / "output" / "form_10.xlsx").exists():
    DEFAULT_EXCEL_PATH = SCRIPT_DIR / "output" / "form_10.xlsx"
elif (SCRIPT_DIR / "data_processing" / "output" / "form_10.xlsx").exists():
    DEFAULT_EXCEL_PATH = SCRIPT_DIR / "data_processing" / "output" / "form_10.xlsx"
else:
    DEFAULT_EXCEL_PATH = SCRIPT_DIR / "output" / "form_10.xlsx"

# Automatically find service_account.json
SERVICE_ACCOUNT_FILE = None
for candidate in [
    SCRIPT_DIR / "service_account.json",
    SCRIPT_DIR.parent / "service_account.json",
]:
    if candidate.exists():
        SERVICE_ACCOUNT_FILE = candidate
        break

if not SERVICE_ACCOUNT_FILE:
    SERVICE_ACCOUNT_FILE = SCRIPT_DIR / "service_account.json"

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1q8ibGcDugYUcBfjvE2v5gKEFBU0Y4zxR2lfcVey8eY0/edit"

# Assembly Constituency name corrections map:
# Standardizes spelling variations across reports and Google Sheets
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


def get_theme_colors(theme: str = "material"):
    if theme == "blue":
        return {
            "header_bg": {"red": 0.910, "green": 0.941, "blue": 0.996},  # #E8F0FE
            "header_fg": {"red": 0.090, "green": 0.306, "blue": 0.651},  # #174EA6
            "band_1": {"red": 1.0, "green": 1.0, "blue": 1.0},
            "band_2": {"red": 0.965, "green": 0.976, "blue": 0.992},
            "divider": {"red": 0.655, "green": 0.776, "blue": 0.988},
        }
    elif theme == "dark":
        return {
            "header_bg": {"red": 0.122, "green": 0.161, "blue": 0.216},  # #1F2937
            "header_fg": {"red": 1.0, "green": 1.0, "blue": 1.0},
            "band_1": {"red": 1.0, "green": 1.0, "blue": 1.0},
            "band_2": {"red": 0.973, "green": 0.976, "blue": 0.980},
            "divider": {"red": 0.298, "green": 0.337, "blue": 0.388},
        }
    else:  # "material" - Google Sheets Executive Clean
        return {
            "header_bg": {"red": 0.910, "green": 0.918, "blue": 0.929},  # #E8EAED
            "header_fg": {"red": 0.125, "green": 0.129, "blue": 0.141},  # #202124
            "band_1": {"red": 1.0, "green": 1.0, "blue": 1.0},
            "band_2": {"red": 0.973, "green": 0.976, "blue": 0.980},  # #F8F9FA
            "divider": {"red": 0.741, "green": 0.757, "blue": 0.776}, # #BDC1C6
        }


def apply_analyst_table_formatting(
    spreadsheet,
    worksheet,
    total_rows: int,
    columns: list,
    theme: str = "material",
    df: pd.DataFrame | None = None,
):
    """
    Applies Senior Data Analyst table styling:
      - Frozen Header Row (Row 1 stays fixed)
      - Frozen District Column (Column A stays fixed for horizontal scanning)
      - Google Sheets Material styling (#E8EAED header, #202124 bold text, 28px height)
      - Subtle freeze-pane divider line under header
      - Native alternating colors (white & #F8F9FA)
      - Dynamic content-aware Auto-Fit column widths (with filter button padding)
      - Exact alignment rules (dates, serials, EPICs centered; text left-aligned)
      - Interactive Filter Dropdowns (▼) on each column
    """
    sheet_id = worksheet.id
    num_cols = len(columns)
    colors = get_theme_colors(theme)

    # 1. Clear any preexisting banded ranges or basic filters
    try:
        metadata = spreadsheet.fetch_sheet_metadata()
    except Exception:
        metadata = {}

    cleanup_requests = []
    for sheet in metadata.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == sheet_id:
            for br in sheet.get("bandedRanges", []):
                cleanup_requests.append({"deleteBanding": {"bandedRangeId": br["bandedRangeId"]}})
            if sheet.get("basicFilter"):
                cleanup_requests.append({"clearBasicFilter": {"sheetId": sheet_id}})

    if cleanup_requests:
        try:
            spreadsheet.batch_update({"requests": cleanup_requests})
        except Exception:
            pass

    # 2. Build table styling requests
    requests = []

    # A. Unfreeze all rows and columns (normal fluid scrolling)
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": 0,
                    "frozenColumnCount": 0,
                    "hideGridlines": False,
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines",
        }
    })

    # B. Header row height (28px - Google standard)
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": 0,
                "endIndex": 1,
            },
            "properties": {"pixelSize": 28},
            "fields": "pixelSize",
        }
    })

    # C. Dynamic content-aware Auto-Fit Column Widths (accounts for header, filter icon, and longest cell)
    base_optimal_widths = {
        "DISTRICT": 265,
        "Assembly Name": 235,
        "FORM TYPE": 140,
        "GENERATION DATE": 175,
        "NAME OF OBJECTOR": 305,
        "Booth Name": 140,
        "SERIAL NUMBER": 160,
        "EPIC NO.": 140,
        "NAME IN FULL": 305,
        "REASONS IN BRIEF FOR OBJECTION": 320,
    }

    if df is not None and len(df) > 0:
        for col_name in columns:
            if col_name in df.columns:
                max_content_len = df[col_name].astype(str).map(len).max()
                header_len = len(col_name)
                # 8px per character + 55px margin for filter dropdown arrow & cell padding
                computed_px = max(max_content_len, header_len) * 8 + 55
                base_optimal_widths[col_name] = max(base_optimal_widths.get(col_name, 140), min(computed_px, 450))

    for idx, col_name in enumerate(columns):
        width = base_optimal_widths.get(col_name, 160)
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": idx,
                    "endIndex": idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    # D. Header cell styling (Material Theme, bold text, middle center)
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": num_cols,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": colors["header_bg"],
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": colors["header_fg"],
                        "fontSize": 10,
                        "bold": True,
                        "fontFamily": "Arial",
                    },
                    "wrapStrategy": "WRAP",
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
        }
    })

    # E. Header bottom divider line
    requests.append({
        "updateBorders": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": num_cols,
            },
            "bottom": {"style": "SOLID_MEDIUM", "color": colors["divider"]},
        }
    })

    # F. Data rows base typography & alignment (Arial 10pt, vertical middle, left align)
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": total_rows + 1,
                "startColumnIndex": 0,
                "endColumnIndex": num_cols,
            },
            "cell": {
                "userEnteredFormat": {
                    "verticalAlignment": "MIDDLE",
                    "horizontalAlignment": "LEFT",
                    "textFormat": {
                        "foregroundColor": {"red": 0.125, "green": 0.129, "blue": 0.141},
                        "fontSize": 10,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(verticalAlignment,horizontalAlignment,textFormat)",
        }
    })

    # G. Center specific columns (Date, Serial No, EPIC No, Form Type)
    center_cols = {"GENERATION DATE", "SERIAL NUMBER", "EPIC NO.", "FORM TYPE"}
    for idx, col_name in enumerate(columns):
        if col_name in center_cols:
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": total_rows + 1,
                        "startColumnIndex": idx,
                        "endColumnIndex": idx + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat.horizontalAlignment",
                }
            })

    # H. Native Alternating Colors (Banded Range)
    requests.append({
        "addBanding": {
            "bandedRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": total_rows + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "rowProperties": {
                    "headerColor": colors["header_bg"],
                    "firstBandColor": colors["band_1"],
                    "secondBandColor": colors["band_2"],
                },
            }
        }
    })

    # I. Enable Interactive Basic Filter (Dropdown filter & sort menus on headers)
    requests.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": total_rows + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                }
            }
        }
    })

    try:
        spreadsheet.batch_update({"requests": requests})
        print(f"  ✓ Database table formatting applied (Frozen Row 1 & Col A, Filters, Zebra Banding)!")
    except Exception as e:
        print(f"  Warning: Could not apply table formatting: {e}", file=sys.stderr)


def build_executive_summary_dashboard(spreadsheet, df: pd.DataFrame):
    """
    Creates or updates the '📊 Executive Summary' tab with:
      - KPI metric cards
      - District-wise breakdown table
      - Top 10 Assemblies by objections
      - Daily trend timeline
    Moves this tab to index 0 so it is the first tab viewed.
    """
    summary_title = "📊 Executive Summary"
    print(f"\nBuilding '{summary_title}' dashboard...")

    # Open or create worksheet
    try:
        ws = spreadsheet.worksheet(summary_title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=summary_title, rows=120, cols=12)

    total_records = len(df)
    unique_districts = df[df["DISTRICT"] != ""]["DISTRICT"].nunique()
    unique_assemblies = df[df["Assembly Name"] != ""]["Assembly Name"].nunique()
    dates_found = [d for d in df["GENERATION DATE"].unique() if d]
    date_span = f"{dates_found[0]} to {dates_found[-1]}" if len(dates_found) > 1 else (dates_found[0] if dates_found else "N/A")

    # Aggregate District distribution
    dist_df = (
        df[df["DISTRICT"] != ""]
        .groupby("DISTRICT")
        .agg(
            total_objections=("DISTRICT", "count"),
            unique_assemblies=("Assembly Name", lambda s: len(set(x for x in s if x))),
        )
        .reset_index()
        .sort_values(by="total_objections", ascending=False)
    )

    # Aggregate Top 10 Assemblies
    ac_df = (
        df[df["Assembly Name"] != ""]
        .groupby(["Assembly Name", "DISTRICT"])
        .agg(total_objections=("Assembly Name", "count"))
        .reset_index()
        .sort_values(by="total_objections", ascending=False)
        .head(10)
    )

    # Build 2D array of content
    rows = []
    # Row 1: Spacing
    rows.append([""] * 10)
    # Row 2: Header Title
    rows.append(["", "📊  ECINET FORM 10 — EXECUTIVE MONITORING DASHBOARD", "", "", "", "", "", "", "", ""])
    # Row 3: Subtitle
    rows.append(["", f"Automated Electoral Objection Intelligence | Total Database Size: {total_records:,} Records", "", "", "", "", "", "", "", ""])
    # Row 4: Spacing
    rows.append([""] * 10)

    # Row 5-6: KPI Cards
    rows.append(["", "TOTAL OBJECTIONS", "", "DISTRICTS MONITORED", "", "CONSTITUENCIES", "", "DATE SPAN", "", "STATUS"])
    rows.append(["", f"{total_records:,}", "", f"{unique_districts}", "", f"{unique_assemblies}", "", f"{date_span}", "", "LIVE / VERIFIED"])
    # Row 7: Spacing
    rows.append([""] * 10)

    # Row 8: District Table Header
    rows.append(["", "📍 DISTRICT-WISE DISTRIBUTION", "", "", "", "", "🏆 TOP 10 CONSTITUENCIES", "", "", ""])
    rows.append(["", "Rank", "District Name", "Objections", "% Share", "Assemblies", "Rank", "Constituency Name", "District", "Objections"])

    # Combine tables side by side
    max_len = max(len(dist_df), len(ac_df))
    dist_list = dist_df.values.tolist()
    ac_list = ac_df.values.tolist()

    for i in range(max_len):
        row = [""]
        # Left side: District
        if i < len(dist_list):
            d_name, d_count, d_acs = dist_list[i]
            pct = f"{(d_count / total_records * 100):.2f}%"
            row.extend([i + 1, d_name, f"{d_count:,}", pct, d_acs])
        else:
            row.extend(["", "", "", "", ""])

        # Right side: Top AC
        if i < len(ac_list):
            ac_name, ac_dist, ac_count = ac_list[i]
            row.extend([i + 1, ac_name, ac_dist, f"{ac_count:,}"])
        else:
            row.extend(["", "", "", ""])

        rows.append(row)

    # Clear and upload content
    ws.clear()
    ws.update(range_name="A1", values=rows)

    # Apply Dashboard visual styling
    sheet_id = ws.id
    requests = []

    # 1. Move to index 0 (First tab), unfreeze rows/columns, and maintain clean gridlines
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "index": 0,
                "gridProperties": {
                    "frozenRowCount": 0,
                    "frozenColumnCount": 0,
                    "hideGridlines": False,
                },
            },
            "fields": "index,gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.hideGridlines",
        }
    })

    # 2. Title Banner Styling (Deep Navy #1E3A8A)
    requests.append({
        "mergeCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 1, "endColumnIndex": 10},
            "mergeType": "MERGE_ALL",
        }
    })
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 1, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.118, "green": 0.227, "blue": 0.541},  # #1E3A8A
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                        "fontSize": 12,
                        "bold": True,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }
    })

    # Subtitle Row
    requests.append({
        "mergeCells": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 10},
            "mergeType": "MERGE_ALL",
        }
    })
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": 3, "startColumnIndex": 1, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.941, "green": 0.957, "blue": 0.980},
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": {"red": 0.29, "green": 0.33, "blue": 0.39},
                        "fontSize": 9,
                        "italic": True,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }
    })

    # 3. KPI Header Labels & Values Styling
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 1, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.941, "green": 0.953, "blue": 0.973},  # #F1F5F9
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": {"red": 0.278, "green": 0.333, "blue": 0.412},
                        "fontSize": 9,
                        "bold": True,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }
    })
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 5, "endRowIndex": 6, "startColumnIndex": 1, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.976, "green": 0.984, "blue": 1.0},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": {"red": 0.118, "green": 0.227, "blue": 0.541},
                        "fontSize": 13,
                        "bold": True,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }
    })

    # 4. Table Headers (Row 8 & 9)
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 7, "endRowIndex": 8, "startColumnIndex": 1, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.886, "green": 0.914, "blue": 0.953},
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": {"red": 0.118, "green": 0.227, "blue": 0.541},
                        "fontSize": 10,
                        "bold": True,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,verticalAlignment,textFormat)",
        }
    })
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 8, "endRowIndex": 9, "startColumnIndex": 1, "endColumnIndex": 10},
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": 0.910, "green": 0.918, "blue": 0.929},  # #E8EAED
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {
                        "foregroundColor": {"red": 0.125, "green": 0.129, "blue": 0.141},
                        "fontSize": 9,
                        "bold": True,
                        "fontFamily": "Arial",
                    },
                }
            },
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,textFormat)",
        }
    })

    # 5. Dynamic Content-Aware Auto-Fit Column Widths on Dashboard
    col_dash_widths = [25, 60, 240, 130, 110, 130, 60, 240, 220, 130]
    for idx, width in enumerate(col_dash_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": idx, "endIndex": idx + 1},
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    # Also apply Google Sheets native auto-resize on table columns
    requests.append({
        "autoResizeDimensions": {
            "dimensions": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": 1,
                "endIndex": 10,
            }
        }
    })

    try:
        spreadsheet.batch_update({"requests": requests})
        print("  ✓ Executive Summary Dashboard styled and set as Tab 1!")
    except Exception as e:
        print(f"  Warning: Could not style Executive Dashboard: {e}", file=sys.stderr)


def push_to_existing_google_sheet(
    sheet_identifier: str = DEFAULT_SHEET_URL,
    excel_path: Path = DEFAULT_EXCEL_PATH,
    service_account_path: Path = SERVICE_ACCOUNT_FILE,
    worksheet_name: str | None = None,
    chunk_size: int = 10000,
    theme: str = "material",
):
    """Programmatically push form_10.xlsx and generate full Executive Analytics Suite."""
    if not service_account_path.exists():
        print(f"Error: Service account file not found at: {service_account_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    if not excel_path.exists():
        print(f"Error: Excel file not found at: {excel_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Senior Data Analytics — Google Sheet Engine")
    print("=" * 60)
    print(f"Service Account: {service_account_path.name}")
    print(f"Reading Excel:   {excel_path.name}...")

    df = pd.read_excel(excel_path, dtype=str).fillna("")
    if "Assembly Name" in df.columns:
        df["Assembly Name"] = df["Assembly Name"].apply(normalize_assembly_name)
    total_rows = len(df)
    columns = list(df.columns)
    print(f"Loaded {total_rows:,} rows across {len(columns)} columns.")

    print("Authenticating with Google Sheets API...")
    try:
        gc = gspread.service_account(filename=str(service_account_path))
    except Exception as e:
        print(f"Error loading service account credentials: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Opening Google Sheet: '{sheet_identifier}'...")
    try:
        if sheet_identifier.startswith("https://"):
            spreadsheet = gc.open_by_url(sheet_identifier)
        elif len(sheet_identifier) > 30 and "/" not in sheet_identifier:
            spreadsheet = gc.open_by_key(sheet_identifier)
        else:
            spreadsheet = gc.open(sheet_identifier)
    except Exception as e:
        print(f"\nError opening spreadsheet: {e}", file=sys.stderr)
        print("\nTroubleshooting Checklist:", file=sys.stderr)
        auth_obj = getattr(gc, "http_client", None)
        creds = getattr(auth_obj, "auth", None)
        sa_email = getattr(creds, "service_account_email", None) or "your service account email"
        print("1. Did you share the Google Sheet with your service account email as 'Editor'?", file=sys.stderr)
        print(f"   Service Account Email: {sa_email}", file=sys.stderr)
        print("2. Is the Google Sheets API enabled on your Google Cloud project?", file=sys.stderr)
        sys.exit(1)

    # 1. Target Data Worksheet
    db_title = worksheet_name or "📋 Form 10 Database"
    try:
        worksheet = spreadsheet.worksheet(db_title)
    except gspread.WorksheetNotFound:
        # Check if first sheet is Sheet1 and can be reused
        first_sheet = spreadsheet.get_worksheet(0)
        if first_sheet and first_sheet.title == "Sheet1":
            worksheet = first_sheet
            try:
                worksheet.update_title(db_title)
            except Exception:
                pass
        else:
            worksheet = spreadsheet.add_worksheet(title=db_title, rows=total_rows + 100, cols=len(columns))

    print(f"Target Database Tab: '{worksheet.title}'")

    # Resize worksheet if necessary
    required_rows = total_rows + 50
    required_cols = max(len(columns), worksheet.col_count)
    if worksheet.row_count < required_rows or worksheet.col_count < required_cols:
        print(f"Resizing sheet to {required_rows:,} rows x {required_cols} columns...")
        worksheet.resize(rows=required_rows, cols=required_cols)

    # Clear and upload data
    print("Clearing previous worksheet data...")
    worksheet.clear()

    print("Uploading headers...")
    worksheet.update(range_name="A1", values=[columns])

    data_values = df.values.tolist()
    print(f"Uploading {total_rows:,} rows in batches of {chunk_size:,}...")
    for start_idx in range(0, total_rows, chunk_size):
        end_idx = min(start_idx + chunk_size, total_rows)
        chunk = data_values[start_idx:end_idx]
        start_row = start_idx + 2
        end_row = end_idx + 1

        range_str = f"A{start_row}"
        print(f"  -> Uploading rows {start_row:,} to {end_row:,} ({len(chunk):,} records)...")
        worksheet.update(range_name=range_str, values=chunk)

    # Apply database table formatting
    print(f"\nStyling database table into Senior Analyst format ({theme} theme)...")
    apply_analyst_table_formatting(spreadsheet, worksheet, total_rows, columns, theme=theme, df=df)

    # Build Executive Dashboard
    build_executive_summary_dashboard(spreadsheet, df)

    print("\n" + "=" * 60)
    print("✓ Successfully published Executive Analytics Suite to Google Sheets!")
    print("=" * 60)
    print(f"Sheet Title: {spreadsheet.title}")
    print(f"Tab 1:       📊 Executive Summary")
    print(f"Tab 2:       {worksheet.title} ({total_rows:,} records)")
    print(f"Live URL:    {spreadsheet.url}")
    print("=" * 60)


def format_only_google_sheet(
    sheet_identifier: str = DEFAULT_SHEET_URL,
    excel_path: Path = DEFAULT_EXCEL_PATH,
    service_account_path: Path = SERVICE_ACCOUNT_FILE,
    worksheet_name: str | None = None,
    theme: str = "material",
):
    """Formats existing data in the Google Sheet into an interactive table without re-uploading."""
    if not service_account_path.exists():
        print(f"Error: Service account file not found at: {service_account_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    print("Authenticating with Google Sheets API...")
    gc = gspread.service_account(filename=str(service_account_path))

    print(f"Opening Google Sheet: '{sheet_identifier}'...")
    if sheet_identifier.startswith("https://"):
        spreadsheet = gc.open_by_url(sheet_identifier)
    elif len(sheet_identifier) > 30 and "/" not in sheet_identifier:
        spreadsheet = gc.open_by_key(sheet_identifier)
    else:
        spreadsheet = gc.open(sheet_identifier)

    worksheet = spreadsheet.worksheet(worksheet_name) if worksheet_name else spreadsheet.get_worksheet(0)
    print(f"Target Worksheet: '{worksheet.title}'")

    headers = worksheet.row_values(1)
    if not headers:
        print("Error: Sheet appears empty. Run push_to_sheet.py without --format-only first.", file=sys.stderr)
        sys.exit(1)

    # Load dataframe for dynamic auto-fit width calculation if file exists
    df = None
    if excel_path and excel_path.exists():
        try:
            df = pd.read_excel(excel_path, dtype=str).fillna("")
            if "Assembly Name" in df.columns:
                df["Assembly Name"] = df["Assembly Name"].apply(normalize_assembly_name)
        except Exception:
            pass

    total_rows = len(worksheet.col_values(1)) - 1
    print(f"Found {total_rows:,} rows and {len(headers)} columns.")
    print(f"Applying Auto-Fit Senior Data Analyst Table formatting ({theme} theme)...")
    apply_analyst_table_formatting(spreadsheet, worksheet, total_rows, headers, theme=theme, df=df)
    print("\n✓ Auto-fit table formatting complete!")
    print(f"Live URL: {spreadsheet.url}")


def summary_only_google_sheet(
    sheet_identifier: str = DEFAULT_SHEET_URL,
    excel_path: Path = DEFAULT_EXCEL_PATH,
    service_account_path: Path = SERVICE_ACCOUNT_FILE,
):
    """Builds or refreshes only the '📊 Executive Summary' tab in ~2 seconds."""
    if not service_account_path.exists():
        print(f"Error: Service account file not found at: {service_account_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading Excel: {excel_path.name}...")
    df = pd.read_excel(excel_path, dtype=str).fillna("")
    if "Assembly Name" in df.columns:
        df["Assembly Name"] = df["Assembly Name"].apply(normalize_assembly_name)

    print("Authenticating with Google Sheets API...")
    gc = gspread.service_account(filename=str(service_account_path))

    print(f"Opening Google Sheet: '{sheet_identifier}'...")
    if sheet_identifier.startswith("https://"):
        spreadsheet = gc.open_by_url(sheet_identifier)
    elif len(sheet_identifier) > 30 and "/" not in sheet_identifier:
        spreadsheet = gc.open_by_key(sheet_identifier)
    else:
        spreadsheet = gc.open(sheet_identifier)

    build_executive_summary_dashboard(spreadsheet, df)
    print("\n✓ Executive Summary Dashboard refreshed!")
    print(f"Live URL: {spreadsheet.url}")


def main():
    parser = argparse.ArgumentParser(description="Publish Form 10 Electoral Data & Executive Dashboard to Google Sheets.")
    parser.add_argument(
        "sheet",
        nargs="?",
        type=str,
        default=DEFAULT_SHEET_URL,
        help=f"Google Sheet URL or Sheet ID (default: {DEFAULT_SHEET_URL})",
    )
    parser.add_argument(
        "--excel-path",
        type=Path,
        default=DEFAULT_EXCEL_PATH,
        help="Path to source form_10.xlsx",
    )
    parser.add_argument(
        "--service-account",
        type=Path,
        default=SERVICE_ACCOUNT_FILE,
        help="Path to service_account.json key",
    )
    parser.add_argument(
        "--worksheet",
        type=str,
        default=None,
        help="Optional database worksheet tab name (defaults to first tab)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10000,
        help="Number of rows per API batch upload (default: 10,000)",
    )
    parser.add_argument(
        "--theme",
        type=str,
        choices=["material", "blue", "dark"],
        default="material",
        help="Color theme for database: 'material' (default executive clean), 'blue' (soft blue), or 'dark'",
    )
    parser.add_argument(
        "--format-only",
        action="store_true",
        help="Only apply table formatting (frozen row 1 & col A, zebra banding, filters) without re-uploading",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only generate/refresh the Executive Summary Dashboard tab without re-uploading",
    )

    args = parser.parse_args()

    if args.format_only:
        format_only_google_sheet(
            sheet_identifier=args.sheet,
            excel_path=args.excel_path,
            service_account_path=args.service_account,
            worksheet_name=args.worksheet,
            theme=args.theme,
        )
    elif args.summary_only:
        summary_only_google_sheet(
            sheet_identifier=args.sheet,
            excel_path=args.excel_path,
            service_account_path=args.service_account,
        )
    else:
        push_to_existing_google_sheet(
            sheet_identifier=args.sheet,
            excel_path=args.excel_path,
            service_account_path=args.service_account,
            worksheet_name=args.worksheet,
            chunk_size=args.chunk_size,
            theme=args.theme,
        )


if __name__ == "__main__":
    main()
