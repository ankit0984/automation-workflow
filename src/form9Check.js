import fs from "node:fs";
import path from "node:path";

import data from "../data/districts_and_constituencies_s19.json" with {
  type: "json",
};

// Check for target directory (prefer specific form9_data folder if present)
const defaultDir = "./data/form9_data";
const dirPath = fs.existsSync(defaultDir) ? defaultDir : "./data";

// Optional CLI arguments: --date YYYY-MM-DD
const args = process.argv.slice(2);
let customDate = null;
const dateArgIndex = args.findIndex((arg) => arg === "--date" || arg === "-d");
if (dateArgIndex !== -1 && args[dateArgIndex + 1]) {
  customDate = args[dateArgIndex + 1];
}

// Folders and files to exclude when scanning
const excludeFolders = new Set([
  "captcha_images",
  "temp_captcha",
  "form10_data",
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
 * Checks if a filename starts with 'form9' and is a PDF.
 */
function isForm9Pdf(filename) {
  const lower = filename.toLowerCase();
  return lower.startsWith("form9") && lower.endsWith(".pdf");
}

/**
 * Recursively scans directory for Form 9 PDF files.
 */
function scanForm9Files(directory) {
  let form9Files = [];
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
      const subResult = scanForm9Files(fullPath);
      form9Files.push(...subResult.form9Files);
      otherFiles.push(...subResult.otherFiles);
    } else if (entry.isFile()) {
      if (isForm9Pdf(entry.name)) {
        let size = 0;
        try {
          size = fs.statSync(fullPath).size;
        } catch {}
        form9Files.push({
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

  return { form9Files, otherFiles };
}

const { form9Files, otherFiles } = scanForm9Files(dirPath);

console.log("==========================================");
console.log("       FORM 9 PDF FILE CHECK REPORT       ");
console.log("==========================================\n");

console.log(`📁 Scanning directory: ${dirPath}`);
console.log(`📄 Total Form 9 PDF files found: ${form9Files.length}`);
if (otherFiles.length > 0) {
  console.log(`ℹ️  Other files encountered: ${otherFiles.length}`);
}

// Check for empty (0 byte) files
const emptyFiles = form9Files.filter((f) => f.size === 0);
if (emptyFiles.length > 0) {
  console.log(`\n⚠️  WARNING: Found ${emptyFiles.length} empty (0-byte) PDF files:`);
  emptyFiles.forEach((f) => console.log(`   - ${f.name} (${f.fullPath})`));
}

// Maps for fast lookup
const existingFileNames = new Set(form9Files.map((f) => f.name.toLowerCase()));
const detectedDates = new Set();
const constituencyPdfsFound = new Map(); // acNumber -> Set of dates found

// Match filenames: form9-<districtCode>-<acNumber>-<YYYY-MM-DD>-report.pdf
for (const file of form9Files) {
  const match = file.name.match(
    /form9-([A-Z0-9]+)-(\d+)-(\d{4}-\d{2}-\d{2})/i
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
const datesToCheck = customDate ? [customDate] : allDetectedDates;

console.log(`📅 Dates detected across files: ${allDetectedDates.join(", ") || "None"}`);
if (customDate) {
  console.log(`🎯 Evaluating specific target date: ${customDate}`);
} else {
  console.log(`🎯 Evaluating all detected dates (${datesToCheck.length} total)`);
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
        const expectedFilename = `form9-${district.districtCode}-${ac.acNumber}-${dateStr}-report.pdf`.toLowerCase();
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
console.log("\n🏛️  Form 9 PDFs by District:");
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
      const expectedFilename = `form9-${district.districtCode}-${ac.acNumber}-${dateStr}-report.pdf`;
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
          expectedPath: `data/form9_data/${district.districtName}/${expectedFilename}`,
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
  console.log(`\n⚠️  DETAILED MISSING FORM 9 PDFs LIST (${missingFiles.length} files):`);
  console.table(
    missingFiles.map((m) => ({
      District: m.district,
      Constituency: m.constituency,
      Date: m.date,
      "Missing File": m.expectedFilename,
    }))
  );
} else {
  console.log("\n🎉 No missing Form 9 files found!");
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

if (missingFiles.length === 0) {
  console.log("\n✅ SUCCESS: All district and assembly PDFs for Form 9 are present!");
} else {
  console.log(`\n⚠️  ATTENTION: ${missingFiles.length} Form 9 PDF(s) missing across ${missingAcMap.size} constituency(ies)!`);
}
console.log("==========================================\n");
