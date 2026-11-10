#!/usr/bin/env python3
"""
validate.py - Form 10 / Statutory Report PDF Validator & Record Counter
========================================================================
Audits Form 10 PDFs:
- Reports total pages per PDF
- Extracts and counts records across every page (multi-page aware)
- Shows page-by-page breakdown (e.g., Page 1: 6, Page 2: 11, Page 3: 11, Page 4: 6)
- Checks application serial numbers for sequence gaps
- Outputs a comprehensive district and statewide summary

Usage (from project root or data_processing folder):
    python validate.py
    python validate.py --date 2026-10-27
    python validate.py --district "Hoshiarpur"
    python validate.py path/to/specific_file.pdf
"""

import os
import sys
import re
import argparse
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# 1. Resolve Project Root & Auto-relaunch inside virtualenv if needed
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent

def find_project_root(start_dir: Path) -> Path:
    current = start_dir
    while current != current.parent:
        if (current / "data").exists() or (current / "captcha_reader_model").exists():
            return current
        current = current.parent
    return Path.cwd()

PROJECT_ROOT = find_project_root(SCRIPT_DIR)
VENV_PYTHON = PROJECT_ROOT / "captcha_reader_model" / ".venv" / "bin" / "python"
if not VENV_PYTHON.exists():
    VENV_PYTHON = PROJECT_ROOT / "captcha_reader_model" / ".venv" / "Scripts" / "python.exe"

# If pypdfium2 is missing, re-launch using the project's venv python automatically
try:
    import pypdfium2 as pdfium
except ImportError:
    if VENV_PYTHON.exists() and sys.executable != str(VENV_PYTHON):
        import subprocess
        sys.exit(subprocess.call([str(VENV_PYTHON)] + sys.argv))
    print("❌ Error: 'pypdfium2' is required.")
    print(f"Please run with venv: {VENV_PYTHON} validate.py")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 2. PDF Parsing Logic
# ---------------------------------------------------------------------------
def resolve_target_path(user_input: str) -> Path:
    """Resolves target path whether run from root or data_processing folder."""
    p = Path(user_input)
    if p.exists():
        return p
    # Check relative to PROJECT_ROOT
    root_p = PROJECT_ROOT / user_input
    if root_p.exists():
        return root_p
    # Check default form10_data directory
    default_dir = PROJECT_ROOT / "data" / "form10_data"
    if default_dir.exists():
        return default_dir
    return p


def parse_pdf_info(pdf_path: Path):
    """Parses a Form 10 PDF, counting total pages and records per page."""
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return {
            "path": pdf_path,
            "filename": pdf_path.name,
            "district": pdf_path.parent.name,
            "ac_name": "N/A",
            "date": "N/A",
            "pages": 0,
            "total_records": 0,
            "page_breakdown": [],
            "app_numbers": [],
            "gaps": [],
            "error": "File is empty (0 bytes) or missing."
        }

    try:
        doc = pdfium.PdfDocument(pdf_path)
    except Exception as e:
        return {
            "path": pdf_path,
            "filename": pdf_path.name,
            "district": pdf_path.parent.name,
            "ac_name": "N/A",
            "date": "N/A",
            "pages": 0,
            "total_records": 0,
            "page_breakdown": [],
            "app_numbers": [],
            "gaps": [],
            "error": f"Corrupt PDF: {e}"
        }

    total_pages = len(doc)
    page_breakdown = []
    all_app_numbers = []
    district = pdf_path.parent.name
    ac_name = ""
    gen_date = ""

    # Parse metadata from Page 1
    if total_pages > 0:
        p1_text = doc[0].get_textpage().get_text_range()
        ac_match = re.search(r"Constituency\s*\(Assembly/Parliamentary\)\s*\n\s*([^\n]+)", p1_text, re.IGNORECASE)
        if ac_match:
            ac_name = ac_match.group(1).strip()
        date_match = re.search(r"From\s+date\s*\n\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})", p1_text, re.IGNORECASE)
        if date_match:
            gen_date = date_match.group(1).replace(".", "/").replace("-", "/")

    # Fallback metadata from filename (form10-<distCode>-<acNum>-<date>-report.pdf)
    fn_match = re.match(r"form\d+-[A-Za-z0-9]+-(\d+)-(\d{4}-\d{2}-\d{2})", pdf_path.name, re.IGNORECASE)
    if fn_match:
        ac_num, fn_date = fn_match.groups()
        if ac_name and not ac_name.startswith(ac_num):
            ac_name = f"{ac_num} - {ac_name}"
        elif not ac_name:
            ac_name = f"AC {ac_num}"
        if not gen_date:
            y, m, d = fn_date.split("-")
            gen_date = f"{d}/{m}/{y}"

    # Extract records across each page
    for p_idx, page in enumerate(doc):
        tp = page.get_textpage()
        count = tp.count_rects()
        if count == 0:
            page_breakdown.append({"page": p_idx + 1, "records": 0, "app_nos": []})
            continue

        header_y = None
        footer_y = 55.0
        rects = []

        for i in range(count):
            rect = tp.get_rect(i)
            l, b, r, t = rect
            txt = tp.get_text_bounded(l, b, r, t).strip()
            if not txt:
                continue

            # Identify header row on Page 1
            if "1 2 3 4 5 6 7 8 (a) 8 (b)" in txt or (l < 70 and txt == "1" and 320 < b < 345):
                header_y = b
            # Identify footer boundary
            if "£ In case of Union territories" in txt or "Date of exhibition" in txt:
                if t > footer_y:
                    footer_y = t
            rects.append((l, b, r, t, txt))

        if header_y is None:
            if p_idx == 0:
                header_candidates = [r for r in rects if 320 <= r[1] <= 345 and r[4] in ("1", "2", "3")]
                header_y = min(r[1] for r in header_candidates) if header_candidates else 333.0
            else:
                # Continuation pages (Page 2+): boundary is below top header banner (~525)
                top_header_rects = [r for r in rects if r[1] > 515 and not r[4].isdigit()]
                header_y = min(r[1] for r in top_header_rects) if top_header_rects else 525.0

        # Extract only data rectangles between header and footer
        table_rects = [r for r in rects if footer_y <= r[1] < (header_y - 4)]
        row_anchors = [r for r in table_rects if r[0] < 90 and r[4].isdigit()]
        row_anchors.sort(key=lambda r: -r[1])

        page_app_nos = [int(r[4]) for r in row_anchors]
        page_breakdown.append({
            "page": p_idx + 1,
            "records": len(row_anchors),
            "app_nos": page_app_nos
        })
        all_app_numbers.extend(page_app_nos)

    total_records = len(all_app_numbers)

    # Sequence continuity check
    gaps = []
    if all_app_numbers:
        sorted_nums = sorted(all_app_numbers)
        for i in range(len(sorted_nums) - 1):
            if sorted_nums[i + 1] != sorted_nums[i] + 1:
                gaps.extend(range(sorted_nums[i] + 1, sorted_nums[i + 1]))

    return {
        "path": pdf_path,
        "filename": pdf_path.name,
        "district": district,
        "ac_name": ac_name,
        "date": gen_date,
        "pages": total_pages,
        "total_records": total_records,
        "page_breakdown": page_breakdown,
        "app_numbers": all_app_numbers,
        "gaps": gaps,
        "error": None
    }


# ---------------------------------------------------------------------------
# 3. Output Formatters
# ---------------------------------------------------------------------------
def print_single_pdf_report(info: dict):
    """Prints a detailed terminal report for a single PDF."""
    print("\n" + "=" * 80)
    print("                    FORM 10 PDF VALIDATION REPORT                              ")
    print("=" * 80)
    print(f"📄 File Name        : {info['filename']}")
    print(f"📁 Full Path        : {info['path']}")
    print(f"🏛️ District         : {info['district']}")
    print(f"🗳️ Assembly Const.  : {info['ac_name'] or 'N/A'}")
    print(f"📅 Generation Date  : {info['date'] or 'N/A'}")
    print(f"📑 Total Pages      : {info['pages']}")
    print(f"📊 Total Records    : {info['total_records']}")

    if info["error"]:
        print(f"\n❌ Error: {info['error']}")
        print("=" * 80 + "\n")
        return

    print("\n--- Page-by-Page Breakdown ---")
    for pb in info["page_breakdown"]:
        apps = pb["app_nos"]
        app_range = f"(Records #{apps[0]} - #{apps[-1]})" if apps else "(No records)"
        print(f"  • Page {pb['page']:2d}: {pb['records']:3d} record(s) {app_range}")

    if info["gaps"]:
        print(f"\n⚠️  WARNING: Serial number gaps detected! Missing records: {info['gaps']}")
    else:
        print("\n✅ Sequence Check: Continuous serial numbers (No gaps detected).")
    print("=" * 80 + "\n")


def print_batch_summary(results: list, verbose: bool = False):
    """Prints aggregated summary across multiple PDFs."""
    total_files = len(results)
    total_pages = sum(r["pages"] for r in results)
    total_records = sum(r["total_records"] for r in results)
    multi_page_files = [r for r in results if r["pages"] > 1]
    empty_files = [r for r in results if r["total_records"] == 0]
    gap_files = [r for r in results if r["gaps"]]

    print("\n" + "=" * 80)
    print("                      BATCH PDF AUDIT SUMMARY                                   ")
    print("=" * 80)
    print(f"📁 Total PDFs Scanned       : {total_files}")
    print(f"📑 Total Pages Across PDFs   : {total_pages}")
    print(f"📊 Total Records Extracted  : {total_records}")
    print(f"📄 Single-page PDFs         : {total_files - len(multi_page_files)}")
    print(f"📚 Multi-page PDFs (>1 pg)  : {len(multi_page_files)} ({len(multi_page_files)/max(total_files,1)*100:.1f}%)")
    if empty_files:
        print(f"ℹ️  PDFs with 0 Records      : {len(empty_files)}")
    if gap_files:
        print(f"⚠️  PDFs with Serial Gaps   : {len(gap_files)}")

    # District-wise breakdown table
    dist_map = defaultdict(lambda: {"files": 0, "pages": 0, "records": 0, "multipage": 0})
    for r in results:
        d = dist_map[r["district"]]
        d["files"] += 1
        d["pages"] += r["pages"]
        d["records"] += r["total_records"]
        if r["pages"] > 1:
            d["multipage"] += 1

    print("\n" + "-" * 80)
    print(f"{'District':<25} | {'PDFs':>5} | {'Pages':>6} | {'Multi-Page':>10} | {'Total Records':>13} | {'Avg/AC':>7}")
    print("-" * 80)
    for dist_name, d in sorted(dist_map.items()):
        avg = f"{d['records'] / max(d['files'], 1):.1f}"
        print(f"{dist_name:<25} | {d['files']:>5} | {d['pages']:>6} | {d['multipage']:>10} | {d['records']:>13} | {avg:>7}")
    print("-" * 80)

    if verbose or len(results) <= 25:
        print("\n--- Individual File Breakdown ---")
        for r in results:
            pages_detail = ", ".join(f"P{pb['page']}:{pb['records']}" for pb in r["page_breakdown"])
            print(f"• {r['filename']:<42} | Pages: {r['pages']:2d} | Records: {r['total_records']:3d} | [{pages_detail}]")
    print("=" * 80 + "\n")


# ---------------------------------------------------------------------------
# 4. Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Validate Form 10 PDFs: page counts, record counts, and summary.")
    parser.add_argument("target", nargs="?", default="data/form10_data", help="PDF file path or folder (default: data/form10_data)")
    parser.add_argument("--date", "-d", help="Filter by date in filename (e.g. 2026-10-27)")
    parser.add_argument("--district", help="Filter by district name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed breakdown for each individual file")
    args = parser.parse_args()

    target_path = resolve_target_path(args.target)

    # 1. Single File Mode
    if target_path.is_file():
        info = parse_pdf_info(target_path)
        print_single_pdf_report(info)
        return

    # 2. Directory Mode
    if not target_path.exists():
        print(f"❌ Error: Path does not exist: {target_path}")
        print(f"💡 Project root is: {PROJECT_ROOT}")
        sys.exit(1)

    pdf_files = sorted(list(target_path.glob("**/*.pdf")))
    if not pdf_files:
        print(f"❌ No PDF files found in {target_path}")
        sys.exit(1)

    # Apply filters
    if args.date:
        pdf_files = [p for p in pdf_files if args.date in p.name]
    if args.district:
        pdf_files = [p for p in pdf_files if args.district.lower() in str(p).lower()]

    print(f"\n⏳ Scanning {len(pdf_files)} PDF file(s) in '{target_path.relative_to(PROJECT_ROOT) if target_path.is_relative_to(PROJECT_ROOT) else target_path}'...")
    results = [parse_pdf_info(p) for p in pdf_files]
    print_batch_summary(results, verbose=args.verbose)


if __name__ == "__main__":
    main()