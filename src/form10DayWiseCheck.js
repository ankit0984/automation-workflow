/**
 * Day-wise Form 10 PDF file check.
 *
 * For every date it reports how many of the expected Form 10 PDFs
 * (one per assembly constituency) are present, which districts / ACs are
 * missing, and any empty (0-byte), duplicate or misplaced files.
 *
 * Usage (e.g.):
 *   pnpm form10daycheck                                  # every day, first date to last
 *   pnpm form10daycheck --date 2026-11-01,2026-11-02     # specific days
 *   pnpm form10daycheck --from 2026-10-25 --to 2026-11-02
 *   pnpm form10daycheck --summary-only                   # just the day-by-day table
 *   pnpm form10daycheck --telegram --json report.json    # send to Telegram / save a JSON report
 *   pnpm form10daycheck --dir ./data/form10_data         # scan a different folder
 *
 * Exit code is 1 when any checked day is incomplete or has empty files.
 */
import fs from "node:fs";
import path from "node:path";

import { sendTelegram } from "./utils/telegram.js";

import data from "../data/districts_and_constituencies_s19.json" with {
  type: "json",
};

// ---------- CLI arguments ----------
const args = process.argv.slice(2);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function argValue(...names) {
  for (let i = 0; i < args.length; i++) {
    if (names.includes(args[i]) && args[i + 1] && !args[i + 1].startsWith("-")) {
      return args[i + 1];
    }
  }
  return undefined;
}

const customDates = [];
for (let i = 0; i < args.length; i++) {
  if ((args[i] === "--date" || args[i] === "-d") && args[i + 1] && !args[i + 1].startsWith("-")) {
    customDates.push(...args[i + 1].split(",").map((s) => s.trim()).filter(Boolean));
  }
}
const fromDate = argValue("--from");
const toDate = argValue("--to");
const jsonOut = argValue("--json");
const sendToTelegram = args.includes("--telegram");
const showDetails = !args.includes("--summary-only");

const defaultDir = "./data/form10_data";
const dirPath = argValue("--dir") ?? (fs.existsSync(defaultDir) ? defaultDir : "./data");

for (const d of [...customDates, fromDate, toDate].filter(Boolean)) {
  if (!DATE_RE.test(d) || Number.isNaN(Date.parse(d))) {
    console.error(`❌ Invalid date "${d}". Use YYYY-MM-DD.`);
    process.exit(1);
  }
}
if (!fs.existsSync(dirPath)) {
  console.error(`❌ Directory not found: ${dirPath}`);
  process.exit(1);
}

// ---------- Reference data ----------
const acLookup = new Map(); // acNumber -> { districtName, districtCode, label }
for (const district of data.districts) {
  for (const ac of district.constituencies) {
    acLookup.set(String(ac.acNumber), {
      districtName: district.districtName,
      districtCode: district.districtCode,
      label: ac.label,
    });
  }
}
const totalConstituencies = acLookup.size;

// ---------- Scan files ----------
const excludeFolders = new Set(["captcha_images", "temp_captcha", "form9_data", "node_modules", ".git"]);
const FILE_RE = /^form10-([A-Z0-9]+)-(\d+)-(\d{4}-\d{2}-\d{2})-report\.pdf$/i;

function scan(directory, out = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (!excludeFolders.has(entry.name)) scan(fullPath, out);
    } else if (entry.isFile() && entry.name.toLowerCase().startsWith("form10") && entry.name.toLowerCase().endsWith(".pdf")) {
      let size = 0;
      try {
        size = fs.statSync(fullPath).size;
      } catch {}
      out.push({ name: entry.name, fullPath, size, folder: path.basename(directory) });
    }
  }
  return out;
}

const allFiles = scan(dirPath);

// date -> Map(acNumber -> [files])
const filesByDate = new Map();
const unparsedFiles = [];
const unknownAcFiles = [];

for (const file of allFiles) {
  const m = file.name.match(FILE_RE);
  if (!m) {
    unparsedFiles.push(file);
    continue;
  }
  const [, districtCode, acNumber, dateStr] = m;
  const ref = acLookup.get(String(Number(acNumber)));
  if (!ref) {
    unknownAcFiles.push({ ...file, date: dateStr });
    continue;
  }
  file.districtCode = districtCode;
  file.acNumber = String(Number(acNumber));
  file.codeMismatch = districtCode.toUpperCase() !== String(ref.districtCode).toUpperCase();
  file.wrongFolder = file.folder !== ref.districtName;

  if (!filesByDate.has(dateStr)) filesByDate.set(dateStr, new Map());
  const acMap = filesByDate.get(dateStr);
  if (!acMap.has(file.acNumber)) acMap.set(file.acNumber, []);
  acMap.get(file.acNumber).push(file);
}

// ---------- Decide which dates to check ----------
function dateRange(start, end) {
  const out = [];
  const cur = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (cur <= last) {
    out.push(cur.toISOString().slice(0, 10));
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return out;
}

const detectedDates = [...filesByDate.keys()].sort();
let datesToCheck;
if (customDates.length > 0) {
  datesToCheck = [...new Set(customDates)].sort();
} else if (fromDate || toDate) {
  datesToCheck = dateRange(fromDate ?? detectedDates[0], toDate ?? detectedDates.at(-1) ?? fromDate);
} else if (detectedDates.length > 0) {
  // Fill gaps so days with zero files are reported too
  datesToCheck = dateRange(detectedDates[0], detectedDates.at(-1));
} else {
  datesToCheck = [];
}

console.log("==========================================");
console.log("     FORM 10 DAY-WISE FILE CHECK REPORT   ");
console.log("==========================================\n");
console.log(`📁 Directory         : ${dirPath}`);
console.log(`📄 Form 10 PDFs found: ${allFiles.length}`);
console.log(`🏛️  Reference ACs     : ${totalConstituencies} across ${data.districts.length} districts`);
console.log(`📅 Detected dates    : ${detectedDates.join(", ") || "None"}`);

if (datesToCheck.length === 0) {
  console.error("\n❌ No dates to check (no Form 10 PDFs found and no --date/--from/--to given).\n");
  process.exit(1);
}
console.log(`🎯 Checking ${datesToCheck.length} day(s): ${datesToCheck[0]} → ${datesToCheck.at(-1)}\n`);

// ---------- Day-wise evaluation ----------
const dayResults = [];

for (const dateStr of datesToCheck) {
  const acMap = filesByDate.get(dateStr) ?? new Map();
  const missing = [];
  const empty = [];
  const duplicates = [];
  const misplaced = [];
  const districtRows = [];

  for (const district of data.districts) {
    let found = 0;
    const missingInDistrict = [];
    for (const ac of district.constituencies) {
      const files = acMap.get(String(ac.acNumber)) ?? [];
      const validFiles = files.filter((f) => f.size > 0);
      if (validFiles.length > 0) found++;
      else missingInDistrict.push(ac.label);

      for (const f of files) {
        if (f.size === 0) empty.push(f);
        if (f.wrongFolder || f.codeMismatch) misplaced.push(f);
      }
      if (files.length > 1) duplicates.push(...files);
    }
    const total = district.constituencies.length;
    for (const label of missingInDistrict) missing.push({ district: district.districtName, constituency: label });
    districtRows.push({
      District: district.districtName,
      Found: `${found}/${total}`,
      Missing: total - found,
      Status: found === total ? "✅" : found === 0 ? "❌ All missing" : "⚠️ Partial",
    });
  }

  const found = totalConstituencies - missing.length;
  dayResults.push({
    date: dateStr,
    found,
    expected: totalConstituencies,
    missing,
    empty,
    duplicates,
    misplaced,
    districtRows,
    complete: missing.length === 0 && empty.length === 0,
  });
}

// ---------- Overview table ----------
console.log("📅 DAY-WISE SUMMARY");
console.table(
  dayResults.map((r) => ({
    Date: r.date,
    Found: r.found,
    Expected: r.expected,
    Missing: r.missing.length,
    "Empty (0B)": r.empty.length,
    Duplicates: r.duplicates.length,
    Misplaced: r.misplaced.length,
    "Completion %": ((r.found / r.expected) * 100).toFixed(1),
    Status: r.complete ? "✅ Complete" : r.found === 0 ? "❌ No files" : "⚠️ Incomplete",
  }))
);

// ---------- Per-day details for incomplete days ----------
if (showDetails) {
  for (const r of dayResults) {
    const hasIssues = !r.complete || r.duplicates.length > 0 || r.misplaced.length > 0;
    if (!hasIssues) continue;

    console.log("\n------------------------------------------");
    console.log(`📆 ${r.date}  —  ${r.found}/${r.expected} present`);
    console.log("------------------------------------------");

    if (r.found === 0) {
      console.log("❌ No Form 10 PDFs at all for this date.");
      continue;
    }

    const incompleteDistricts = r.districtRows.filter((d) => d.Missing > 0);
    if (incompleteDistricts.length > 0) {
      console.log("🏛️  Districts with missing PDFs:");
      console.table(incompleteDistricts);
    }
    if (r.missing.length > 0) {
      console.log(`⚠️  Missing ACs (${r.missing.length}):`);
      console.table(r.missing.map((m) => ({ District: m.district, Constituency: m.constituency })));
    }
    if (r.empty.length > 0) {
      console.log(`🗑️  Empty (0-byte) files (${r.empty.length}):`);
      r.empty.forEach((f) => console.log(`   - ${f.fullPath}`));
    }
    if (r.duplicates.length > 0) {
      console.log(`📑 Duplicate files for the same AC (${r.duplicates.length}):`);
      r.duplicates.forEach((f) => console.log(`   - ${f.fullPath}`));
    }
    if (r.misplaced.length > 0) {
      console.log(`📂 Misplaced files (wrong district folder or district code) (${r.misplaced.length}):`);
      r.misplaced.forEach((f) => {
        const ref = acLookup.get(f.acNumber);
        console.log(`   - ${f.fullPath}  (expected folder: ${ref.districtName}, code: ${ref.districtCode})`);
      });
    }
  }
}

// ---------- Files that could not be mapped ----------
if (unparsedFiles.length > 0) {
  console.log(`\n❓ Files not matching form10-<code>-<ac>-<YYYY-MM-DD>-report.pdf (${unparsedFiles.length}):`);
  unparsedFiles.forEach((f) => console.log(`   - ${f.fullPath}`));
}
if (unknownAcFiles.length > 0) {
  console.log(`\n❓ Files with an AC number not in the reference JSON (${unknownAcFiles.length}):`);
  unknownAcFiles.forEach((f) => console.log(`   - ${f.fullPath}`));
}

// ---------- Final verdict ----------
const completeDays = dayResults.filter((r) => r.complete);
const incompleteDays = dayResults.filter((r) => !r.complete);
const totalExpected = totalConstituencies * dayResults.length;
const totalFound = dayResults.reduce((s, r) => s + r.found, 0);

console.log("\n==========================================");
console.log("              FINAL VERDICT               ");
console.log("==========================================");
console.log(`📅 Days checked     : ${dayResults.length}`);
console.log(`✅ Complete days    : ${completeDays.length}`);
console.log(`⚠️  Incomplete days  : ${incompleteDays.length}${incompleteDays.length ? ` (${incompleteDays.map((r) => r.date).join(", ")})` : ""}`);
console.log(`📄 PDFs found       : ${totalFound}/${totalExpected} (${((totalFound / totalExpected) * 100).toFixed(2)}%)`);

// ---------- Optional JSON output ----------
if (jsonOut) {
  const report = {
    generatedAt: new Date().toISOString(),
    directory: dirPath,
    totalConstituencies,
    days: dayResults.map((r) => ({
      date: r.date,
      found: r.found,
      expected: r.expected,
      complete: r.complete,
      missing: r.missing,
      empty: r.empty.map((f) => f.fullPath),
      duplicates: r.duplicates.map((f) => f.fullPath),
      misplaced: r.misplaced.map((f) => f.fullPath),
    })),
    unparsedFiles: unparsedFiles.map((f) => f.fullPath),
    unknownAcFiles: unknownAcFiles.map((f) => f.fullPath),
  };
  fs.writeFileSync(jsonOut, JSON.stringify(report, null, 2));
  console.log(`💾 JSON report written to ${jsonOut}`);
}

// ---------- Optional Telegram ----------
if (sendToTelegram) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = [
    `📋 <b>FORM 10 DAY-WISE CHECK</b>`,
    incompleteDays.length === 0 ? `✅ <b>All ${dayResults.length} day(s) complete</b>` : `⚠️ <b>${incompleteDays.length} of ${dayResults.length} day(s) incomplete</b>`,
    ``,
  ];
  for (const r of dayResults) {
    const extra = r.empty.length ? ` (🗑️ ${r.empty.length} empty)` : "";
    lines.push(`${r.complete ? "✅" : r.found === 0 ? "❌" : "⚠️"} ${r.date}: ${r.found}/${r.expected}${extra}`);
  }
  for (const r of incompleteDays.filter((d) => d.found > 0)) {
    const districts = r.districtRows.filter((d) => d.Missing > 0).map((d) => `${esc(d.District)} (${d.Missing})`);
    lines.push(``, `<b>${r.date} missing:</b> ${districts.join(", ")}`);
  }
  await sendTelegram(lines.join("\n").slice(0, 4000));
}

if (incompleteDays.length > 0) {
  console.error("\n❌ Day-wise check failed: some days are incomplete.\n");
  process.exit(1);
}
console.log("\n✅ All checked days are complete!\n");
