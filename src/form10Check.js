import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import data from "../data/districts_and_constituencies_s19.json" with {
  type: "json",
};

// Check for target directory (prefer specific form10_data folder if present)
const defaultDir = "./data/form10_data";
const dirPath = fs.existsSync(defaultDir) ? defaultDir : "./data";

// Optional CLI arguments: --date YYYY-MM-DD (supports multiple dates or comma-separated list)
const args = process.argv.slice(2);
const customDates = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--date" || args[i] === "-d") {
    if (args[i + 1] && !args[i + 1].startsWith("-")) {
      const parts = args[i + 1]
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      customDates.push(...parts);
    }
  }
}
const checkOnly = args.includes("--check-only") || args.includes("--audit-only");

// Folders and files to exclude when scanning
const excludeFolders = new Set([
  "captcha_images",
  "temp_captcha",
  "form9_data",
  "node_modules",
  ".git",
]);

const excludeFiles = new Set([
  ".DS_Store",
  "districts_and_constituencies_s19.json",
  "districts_and_constituencies.json",
  "captcha_data.json",
  "captcha_labels.json",
]);

/**
 * Checks if a filename starts with 'form10' and is a PDF.
 */
function isForm10Pdf(filename) {
  const lower = filename.toLowerCase();
  return lower.startsWith("form10") && lower.endsWith(".pdf");
}

/**
 * Recursively scans directory for Form 10 PDF files.
 */
function scanForm10Files(directory) {
  let form10Files = [];
  let otherFiles = [];

  const entries = fs.readdirSync(directory, { withFileTypes: true });

  for (const entry of entries) {
    if (entry.isDirectory() && excludeFolders.has(entry.name)) {
      continue;
    }

    if (entry.isFile() && excludeFiles.has(entry.name)) {
      continue;
    }

    const fullPath = path.join(directory, entry.name);

    if (entry.isDirectory()) {
      const subResult = scanForm10Files(fullPath);
      form10Files.push(...subResult.form10Files);
      otherFiles.push(...subResult.otherFiles);
    } else if (entry.isFile()) {
      if (isForm10Pdf(entry.name)) {
        let size = 0;
        try {
          size = fs.statSync(fullPath).size;
        } catch {}
        form10Files.push({
          name: entry.name,
          fullPath,
          size,
          districtDir: path.basename(directory),
        });
      } else {
        otherFiles.push({
          name: entry.name,
          fullPath,
        });
      }
    }
  }

  return { form10Files, otherFiles };
}

const { form10Files, otherFiles } = scanForm10Files(dirPath);

console.log("==========================================");
console.log("       FORM 10 PDF FILE CHECK REPORT      ");
console.log("==========================================\n");

console.log(`📁 Scanning directory: ${dirPath}`);
console.log(`📄 Total Form 10 PDF files found: ${form10Files.length}`);
if (otherFiles.length > 0) {
  console.log(`ℹ️  Other files encountered: ${otherFiles.length}`);
}

// Check for empty (0 byte) files
const emptyFiles = form10Files.filter((f) => f.size === 0);
if (emptyFiles.length > 0) {
  console.log(`\n⚠️  WARNING: Found ${emptyFiles.length} empty (0-byte) PDF files:`);
  emptyFiles.forEach((f) => console.log(`   - ${f.name} (${f.fullPath})`));
}

// Maps for fast lookup
const existingFileNames = new Set(form10Files.map((f) => f.name.toLowerCase()));
const detectedDates = new Set();
const constituencyPdfsFound = new Map(); // acNumber -> Set of dates found

// Match filenames: form10-<districtCode>-<acNumber>-<YYYY-MM-DD>-report.pdf
for (const file of form10Files) {
  const match = file.name.match(
    /form10-([A-Z0-9]+)-(\d+)-(\d{4}-\d{2}-\d{2})/i
  );
  if (match) {
    const [, , acNumber, dateStr] = match;
    detectedDates.add(dateStr);

    if (!constituencyPdfsFound.has(acNumber)) {
      constituencyPdfsFound.set(acNumber, new Set());
    }
    constituencyPdfsFound.get(acNumber).add(dateStr);
  }
}

// Determine dates to evaluate
const allDetectedDates = [...detectedDates].sort();
const datesToCheck = customDates.length > 0 ? [...new Set(customDates)].sort() : allDetectedDates;

console.log(`📅 Dates detected across files: ${allDetectedDates.join(", ") || "None"}`);
if (customDates.length > 0) {
  console.log(`🎯 Evaluating specific target date(s): ${datesToCheck.join(", ")}`);
} else {
  console.log(`🎯 Evaluating all detected dates (${datesToCheck.length} total)`);
}

if (datesToCheck.length === 0) {
  console.error("\n❌ CHECK FAILED: No Form 10 PDF files found to evaluate. Stopping execution.\n");
  process.exit(1);
}

// Reference info
const totalDistrictsCount = data.districts.length;
const totalConstituencies = data.districts.reduce(
  (total, district) => total + district.constituencies.length,
  0
);

console.log("\n--- Reference Validation ---");
console.log(`Total Districts: ${totalDistrictsCount}`);
console.log(`Total Constituencies: ${totalConstituencies} (Reference: ${data.totalConstituencies})`);
if (data.totalConstituencies === totalConstituencies) {
  console.log("✅ Reference constituency count matches!");
} else {
  console.error("❌ Constituency count mismatch in JSON reference!");
}

// Date-wise completion
if (datesToCheck.length > 0) {
  console.log("\n📅 Summary by Date:");
  const dateSummary = datesToCheck.map((dateStr) => {
    let foundCount = 0;
    for (const district of data.districts) {
      for (const ac of district.constituencies) {
        const expectedFilename = `form10-${district.districtCode}-${ac.acNumber}-${dateStr}-report.pdf`.toLowerCase();
        if (existingFileNames.has(expectedFilename)) {
          foundCount++;
        }
      }
    }
    const missingCount = totalConstituencies - foundCount;
    return {
      Date: dateStr,
      "Expected ACs": totalConstituencies,
      "Found PDFs": foundCount,
      "Missing PDFs": missingCount,
      Status:
        missingCount === 0
          ? "✅ Complete (100%)"
          : `⚠️ ${missingCount} Missing (${((foundCount / totalConstituencies) * 100).toFixed(1)}%)`,
    };
  });
  console.table(dateSummary);
}

// District-wise breakdown & missing check
console.log("\n🏛️  Form 10 PDFs by District:");
const districtSummary = [];
const completelyMissingDistricts = [];
const missingFiles = [];
const missingAcMap = new Map(); // acNumber -> { district, label, missingDates: [] }

for (const district of data.districts) {
  const totalACs = district.constituencies.length;
  const expectedPdfs = totalACs * datesToCheck.length;
  let foundPdfs = 0;
  let districtMissingFiles = 0;

  for (const ac of district.constituencies) {
    for (const dateStr of datesToCheck) {
      const expectedFilename = `form10-${district.districtCode}-${ac.acNumber}-${dateStr}-report.pdf`;
      if (existingFileNames.has(expectedFilename.toLowerCase())) {
        foundPdfs++;
      } else {
        districtMissingFiles++;
        missingFiles.push({
          district: district.districtName,
          constituency: ac.label,
          acNumber: ac.acNumber,
          date: dateStr,
          expectedFilename,
          expectedPath: `data/form10_data/${district.districtName}/${expectedFilename}`,
        });

        if (!missingAcMap.has(ac.acNumber)) {
          missingAcMap.set(ac.acNumber, {
            district: district.districtName,
            constituency: ac.label,
            missingDates: [],
          });
        }
        missingAcMap.get(ac.acNumber).missingDates.push(dateStr);
      }
    }
  }

  if (foundPdfs === 0 && expectedPdfs > 0) {
    completelyMissingDistricts.push(district.districtName);
  }

  const completionPct = expectedPdfs > 0 ? ((foundPdfs / expectedPdfs) * 100).toFixed(1) : "0.0";
  const statusStr =
    districtMissingFiles === 0
      ? `✅ Complete (${foundPdfs}/${expectedPdfs})`
      : foundPdfs === 0
      ? `❌ Entire District Missing (0/${expectedPdfs})`
      : `⚠️ ${districtMissingFiles} Missing (${foundPdfs}/${expectedPdfs})`;

  districtSummary.push({
    District: district.districtName,
    "AC Count": totalACs,
    "Expected PDFs": expectedPdfs,
    "Found PDFs": foundPdfs,
    "Missing PDFs": districtMissingFiles,
    Status: statusStr,
  });
}

console.table(districtSummary);

// 1. Check for completely missing districts
console.log("\n==========================================");
console.log("          MISSING AUDIT RESULTS           ");
console.log("==========================================");

if (completelyMissingDistricts.length > 0) {
  console.log(`\n❌ COMPLETELY MISSING DISTRICTS (${completelyMissingDistricts.length}/${totalDistrictsCount}):`);
  completelyMissingDistricts.forEach((d) => console.log(`   - ❌ ${d}`));
} else {
  console.log("\n✅ All 23 districts have files downloaded!");
}

// 2. Check for missing assembly constituencies
if (missingAcMap.size > 0) {
  console.log(`\n⚠️  ASSEMBLY CONSTITUENCIES WITH MISSING PDFs (${missingAcMap.size}/${totalConstituencies}):`);
  const acMissingTable = [];
  for (const [, item] of missingAcMap) {
    const isTotallyMissing = item.missingDates.length === datesToCheck.length;
    acMissingTable.push({
      District: item.district,
      Constituency: item.constituency,
      "Missing Dates Count": item.missingDates.length,
      "Missing Dates": item.missingDates.join(", "),
      Type: isTotallyMissing ? "❌ Completely Missing" : "⚠️ Partially Missing",
    });
  }
  console.table(acMissingTable);
} else {
  console.log(`\n✅ All ${totalConstituencies} assembly constituencies are fully complete!`);
}

// 3. Detailed missing files list
if (missingFiles.length > 0) {
  console.log(`\n⚠️  DETAILED MISSING FORM 10 PDFs LIST (${missingFiles.length} files):`);
  console.table(
    missingFiles.map((m) => ({
      District: m.district,
      Constituency: m.constituency,
      Date: m.date,
      "Missing File": m.expectedFilename,
    }))
  );
} else {
  console.log("\n🎉 No missing Form 10 files found!");
}

// Final Summary Banner
const totalExpected = totalConstituencies * datesToCheck.length;
const totalFound = totalExpected - missingFiles.length;
const overallPct = totalExpected > 0 ? ((totalFound / totalExpected) * 100).toFixed(2) : "0.00";

console.log("\n==========================================");
console.log("              FINAL VERDICT               ");
console.log("==========================================");
console.log(`📊 Total Expected PDFs : ${totalExpected}`);
console.log(`📄 Total Found PDFs    : ${totalFound}`);
console.log(`❌ Total Missing PDFs  : ${missingFiles.length}`);
console.log(`📈 Completion Rate     : ${overallPct}%`);

if (missingFiles.length > 0 || emptyFiles.length > 0) {
  if (emptyFiles.length > 0) {
    console.error(`\n❌ ERROR: Found ${emptyFiles.length} empty (0-byte) PDF file(s) that require re-download.`);
  }
  if (missingFiles.length > 0) {
    console.error(`\n⚠️  ATTENTION: ${missingFiles.length} Form 10 PDF(s) missing across ${missingAcMap.size} constituency(ies)!`);
  }
  console.error("\n❌ STOPPING: Data check failed due to missing or incomplete files.\n");
  process.exit(1);
}

console.log("\n✅ SUCCESS: All district and assembly PDFs for Form 10 are present!");
console.log("==========================================\n");

if (checkOnly) {
  console.log("ℹ️  --check-only specified. Skipping python extraction.");
  process.exit(0);
}

// Helper: Resolve python virtual environment executable
function getPythonExecutable() {
  const rootDir = path.resolve();
  const venvPythonUnix = path.join(rootDir, "captcha_reader_model", ".venv", "bin", "python");
  if (fs.existsSync(venvPythonUnix)) {
    return venvPythonUnix;
  }
  const venvPythonWin = path.join(rootDir, "captcha_reader_model", ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPythonWin)) {
    return venvPythonWin;
  }
  return process.platform === "win32" ? "python" : "python3";
}

// Helper: Resolve extract_form10_data.py location
function getExtractScriptPath() {
  const rootDir = path.resolve();
  const candidates = [
    path.join(rootDir, "captcha_reader_model", "data_processing", "extract_form10_data.py"),
    path.join(rootDir, "captcha_reader_model", "dataprocessing", "extract_form10_data.py"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return candidates[0];
}

console.log("==========================================");
console.log("   EXECUTING FORM 10 DATA EXTRACTION      ");
console.log("==========================================\n");

const pythonBin = getPythonExecutable();
const extractScript = getExtractScriptPath();

if (!fs.existsSync(extractScript)) {
  console.error(`❌ Error: Extraction script not found at ${extractScript}`);
  process.exit(1);
}

console.log(`🐍 Python binary : ${pythonBin}`);
console.log(`📄 Script path   : ${extractScript}`);
console.log(`⏳ Starting extraction pipeline...\n`);

const pythonProcess = spawnSync(pythonBin, [extractScript], {
  stdio: "inherit",
  cwd: path.dirname(extractScript),
});

if (pythonProcess.error) {
  console.error(`\n❌ Failed to execute python script:`, pythonProcess.error);
  process.exit(1);
}

if (pythonProcess.status !== 0) {
  console.error(`\n❌ Python extraction script failed with exit code ${pythonProcess.status}`);
  process.exit(pythonProcess.status || 1);
}

console.log(`\n🎉 Form 10 extraction completed successfully!\n`);
process.exit(0);
