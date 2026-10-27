import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

// ======================== CONFIGURATION ========================
// 1. Manually specify your target date or date range here:
export const START_DATE = "2026-09-23"; // Start date (YYYY-MM-DD)
export const END_DATE = null;   // End date (YYYY-MM-DD), or set to null / same as START_DATE for a single day

// 2. Set to true for a dry-run preview (scans and counts files, but deletes nothing)
export const DRY_RUN = false;
// ===============================================================

const DATA_DIRS = {
  form9: "./data/form9_data",
  form10: "./data/form10_data",
};

// Folders and files to exclude when scanning
const EXCLUDE_FOLDERS = new Set(["captcha_images", "temp_captcha", ".git", "node_modules"]);
const EXCLUDE_FILES = new Set([".DS_Store"]);

// ANSI Colors for terminal output
const colors = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  cyan: "\x1b[36m",
  white: "\x1b[37m",
};

/**
 * Format bytes into human-readable string (KB, MB, GB)
 */
function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`;
}

/**
 * Helper to generate date range [startStr ... endStr] (inclusive)
 */
function generateDateRange(startStr, endStr) {
  if (!startStr) return [];
  if (!endStr || startStr === endStr) return [startStr];

  const dates = [];
  const [sY, sM, sD] = startStr.split("-").map(Number);
  const [eY, eM, eD] = endStr.split("-").map(Number);

  const start = new Date(sY, sM - 1, sD);
  const end = new Date(eY, eM - 1, eD);

  if (isNaN(start.getTime()) || isNaN(end.getTime())) {
    throw new Error(`Invalid date format in config: START_DATE="${startStr}", END_DATE="${endStr}"`);
  }

  const current = new Date(start);
  while (current <= end) {
    const yyyy = current.getFullYear();
    const mm = String(current.getMonth() + 1).padStart(2, "0");
    const dd = String(current.getDate()).padStart(2, "0");
    dates.push(`${yyyy}-${mm}-${dd}`);
    current.setDate(current.getDate() + 1);
  }
  return dates;
}

/**
 * Extract YYYY-MM-DD from PDF filenames.
 * Anchored to match standard report names:
 * form9-S1902-11-2026-09-23-report.pdf
 * form10-S1902-11-2026-09-23-report.pdf
 */
function extractDateFromFilename(filename) {
  const match = filename.match(/(\d{4}-\d{2}-\d{2})(?:-report)?\.pdf$/i);
  return match ? match[1] : null;
}

/**
 * Recursively scans directory for PDF report files.
 */
function scanDirectory(dirPath, formType) {
  const files = [];
  if (!fs.existsSync(dirPath)) return files;

  function walk(currentDir) {
    const entries = fs.readdirSync(currentDir, { withFileTypes: true });

    for (const entry of entries) {
      if (entry.isDirectory()) {
        if (!EXCLUDE_FOLDERS.has(entry.name)) {
          walk(path.join(currentDir, entry.name));
        }
      } else if (entry.isFile()) {
        if (EXCLUDE_FILES.has(entry.name)) continue;

        if (entry.name.toLowerCase().endsWith(".pdf")) {
          const fullPath = path.join(currentDir, entry.name);
          const stat = fs.statSync(fullPath);
          const date = extractDateFromFilename(entry.name);
          const district = path.basename(currentDir);

          files.push({
            name: entry.name,
            fullPath,
            size: stat.size,
            date: date || "UNKNOWN",
            formType,
            district,
          });
        }
      }
    }
  }

  walk(dirPath);
  return files;
}

/**
 * Readline question prompt helper
 */
function askQuestion(query) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise((resolve) =>
    rl.question(query, (ans) => {
      rl.close();
      resolve(ans.trim());
    })
  );
}

/**
 * Deletes matching files and cleans empty district folders
 */
function executeDeletion(filesToDelete) {
  const totalBytes = filesToDelete.reduce((acc, f) => acc + f.size, 0);
  console.log(`\n${colors.bold}${colors.red}🗑️  Deleting ${filesToDelete.length} files (${formatBytes(totalBytes)})...${colors.reset}`);

  let deletedCount = 0;
  let freedBytes = 0;
  const affectedDirs = new Set();

  for (const file of filesToDelete) {
    try {
      if (fs.existsSync(file.fullPath)) {
        fs.unlinkSync(file.fullPath);
        deletedCount++;
        freedBytes += file.size;
        affectedDirs.add(path.dirname(file.fullPath));
      }
    } catch (err) {
      console.error(`${colors.red}❌ Failed to delete ${file.fullPath}: ${err.message}${colors.reset}`);
    }
  }

  // Clean empty district folders
  for (const dir of affectedDirs) {
    try {
      if (fs.existsSync(dir)) {
        const remaining = fs.readdirSync(dir);
        if (remaining.length === 0) {
          fs.rmdirSync(dir);
          console.log(`🧹 Removed empty district folder: ${dir}`);
        }
      }
    } catch {
      // Ignore directory cleanup errors
    }
  }

  console.log(`\n${colors.bold}${colors.green}✅ Successfully deleted ${deletedCount} files!${colors.reset}`);
  console.log(`💾 ${colors.bold}Disk space freed:${colors.reset} ${formatBytes(freedBytes)}\n`);
}

// ======================== MAIN ========================

async function main() {
  const args = process.argv.slice(2);

  // Command-line flag overrides (optional)
  const isDryRun = DRY_RUN || args.includes("--dry-run") || args.includes("-n");
  const autoConfirm = args.includes("--yes") || args.includes("-y") || args.includes("--force");

  console.log(`\n${colors.bold}${colors.cyan}=========================================================================${colors.reset}`);
  console.log(`${colors.bold}${colors.cyan}             🗑️  DELETE FILES BY DATE RANGE                              ${colors.reset}`);
  console.log(`${colors.bold}${colors.cyan}=========================================================================${colors.reset}\n`);

  // 1. Process configured dates
  const targetDates = generateDateRange(START_DATE, END_DATE);
  if (targetDates.length === 0) {
    console.error(`${colors.red}❌ No valid dates configured in START_DATE / END_DATE.${colors.reset}\n`);
    return;
  }

  console.log(`📅 ${colors.bold}Configured Date Range:${colors.reset} ${colors.yellow}${START_DATE}${colors.reset} to ${colors.yellow}${END_DATE || START_DATE}${colors.reset}`);
  console.log(`   Dates included (${targetDates.length}): ${colors.dim}${targetDates.join(", ")}${colors.reset}\n`);

  // 2. Ask ONLY which data to target (form9 or form10)
  let targetFolderChoice = "";
  if (args.includes("--form9")) targetFolderChoice = "1";
  else if (args.includes("--form10")) targetFolderChoice = "2";
  else if (args.includes("--both")) targetFolderChoice = "3";
  else {
    console.log(`${colors.bold}Which data do you want to delete?${colors.reset}`);
    console.log(`  ${colors.cyan}[1]${colors.reset} form9_data   (Form 9 files)`);
    console.log(`  ${colors.cyan}[2]${colors.reset} form10_data  (Form 10 files)`);
    console.log(`  ${colors.cyan}[3]${colors.reset} Both         (form9_data & form10_data)`);
    console.log(`  ${colors.dim}[q] Exit / Cancel${colors.reset}\n`);

    targetFolderChoice = await askQuestion(
      `${colors.bold}${colors.yellow}Enter your choice (1, 2, or 3) [default: 1]: ${colors.reset}`
    );
  }

  targetFolderChoice = targetFolderChoice || "1";

  if (targetFolderChoice.toLowerCase() === "q" || targetFolderChoice.toLowerCase() === "exit") {
    console.log("\nOperation canceled by user.\n");
    return;
  }

  let selectedTargets = [];
  let targetLabels = [];

  if (targetFolderChoice === "1" || targetFolderChoice.toLowerCase() === "form9") {
    selectedTargets = ["form9"];
    targetLabels = ["form9_data"];
  } else if (targetFolderChoice === "2" || targetFolderChoice.toLowerCase() === "form10") {
    selectedTargets = ["form10"];
    targetLabels = ["form10_data"];
  } else if (targetFolderChoice === "3" || targetFolderChoice.toLowerCase() === "both") {
    selectedTargets = ["form9", "form10"];
    targetLabels = ["form9_data", "form10_data"];
  } else {
    console.log(`${colors.red}❌ Invalid choice: "${targetFolderChoice}". Exiting.${colors.reset}\n`);
    return;
  }

  console.log(`\n📁 Target folder(s): ${colors.bold}${targetLabels.join(", ")}${colors.reset}`);
  console.log(`🔍 Scanning for files matching date range...`);

  // 3. Scan matching files
  const dateSet = new Set(targetDates);
  const matchedFiles = [];

  for (const target of selectedTargets) {
    const dir = DATA_DIRS[target];
    const scanned = scanDirectory(dir, target);
    for (const file of scanned) {
      if (dateSet.has(file.date)) {
        matchedFiles.push(file);
      }
    }
  }

  if (matchedFiles.length === 0) {
    console.log(`\n${colors.green}✅ No files found matching ${START_DATE}..${END_DATE || START_DATE} in ${targetLabels.join(", ")}.${colors.reset}\n`);
    return;
  }

  // 4. Summarize matches
  const totalBytes = matchedFiles.reduce((acc, f) => acc + f.size, 0);

  // Group by date & form type for clean breakdown
  const summaryMap = {};
  for (const f of matchedFiles) {
    const key = `${f.date} (${f.formType})`;
    summaryMap[key] = (summaryMap[key] || 0) + 1;
  }

  console.log(`\n${colors.bold}📊 Files Found:${colors.reset}`);
  console.table(
    Object.entries(summaryMap).map(([group, count]) => ({
      "Date (Type)": group,
      "Files Found": count,
    }))
  );

  console.log(`📦 ${colors.bold}Total matched files:${colors.reset} ${colors.bold}${colors.red}${matchedFiles.length}${colors.reset} (${formatBytes(totalBytes)})`);

  // 5. Handle Dry Run or Confirmation
  if (isDryRun) {
    console.log(`\n${colors.bold}${colors.yellow}🔍 [DRY RUN ACTIVE] No files deleted.${colors.reset}`);
    console.log(`${colors.cyan}Sample files that would be removed:${colors.reset}`);
    matchedFiles.slice(0, 6).forEach((f) => console.log(`  - ${f.fullPath}`));
    if (matchedFiles.length > 6) {
      console.log(`  ${colors.dim}... and ${matchedFiles.length - 6} more files${colors.reset}`);
    }
    console.log(`\n${colors.green}💡 To actually delete, set DRY_RUN = false in deleteDateFiles.js and run again.${colors.reset}\n`);
    return;
  }

  // Confirm deletion
  if (!autoConfirm) {
    if (!process.stdin.isTTY) {
      console.error(`${colors.red}❌ Interactive terminal required for confirmation. Run with --yes to bypass.${colors.reset}\n`);
      return;
    }

    const confirm = await askQuestion(
      `\n${colors.bold}${colors.red}⚠️  Are you sure you want to permanently delete these ${matchedFiles.length} files? (y/N): ${colors.reset}`
    );

    if (confirm.toLowerCase() !== "y" && confirm.toLowerCase() !== "yes") {
      console.log(`\n${colors.yellow}❌ Operation canceled. No files were deleted.${colors.reset}\n`);
      return;
    }
  }

  // 6. Execute Deletion
  executeDeletion(matchedFiles);
}

main().catch((err) => {
  console.error(`\n${colors.red}Fatal Error: ${err.message}${colors.reset}\n`);
  process.exit(1);
});
