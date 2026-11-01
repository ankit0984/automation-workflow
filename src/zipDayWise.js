import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

// In ES modules, derive __dirname from import.meta.url
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// 1. Define paths (relative to project root)
const PROJECT_ROOT = path.resolve(__dirname, '..');
const DATA_DIR = path.join(PROJECT_ROOT, 'data');
const OUTPUT_DIR = path.join(DATA_DIR, 'zipped_data');

// CLI:
//   node src/zipDayWise.js [optional_folder_path]                    -> form10_<date>.zip for every date
//   node src/zipDayWise.js --date 2026-10-28 [optional_folder_path]  -> form10_2026-10-28.zip only
// Inside each zip, PDFs keep their district folders (Amritsar/..., Barnala/...).
const args = process.argv.slice(2);
const dateFlagIdx = args.indexOf('--date');
const TARGET_DATE = dateFlagIdx !== -1 ? normalizeDateArg(args[dateFlagIdx + 1]) : null;
const positional = args.filter((_, i) => i !== dateFlagIdx && i !== dateFlagIdx + 1);

const defaultSource = path.join(DATA_DIR, 'form10_data');
const SOURCE_DIR = positional[0]
  ? path.resolve(positional[0])
  : (fs.existsSync(defaultSource) ? defaultSource : DATA_DIR);

/**
 * Normalizes a --date argument (YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY) to YYYY-MM-DD.
 */
function normalizeDateArg(value) {
  if (!value) {
    console.error('❌ --date requires a value, e.g. --date 2026-10-28');
    process.exit(1);
  }
  let match = value.match(/^(20\d\d)[-_/](\d{1,2})[-_/](\d{1,2})$/);
  if (match) return `${match[1]}-${match[2].padStart(2, '0')}-${match[3].padStart(2, '0')}`;

  match = value.match(/^(\d{1,2})[-_/](\d{1,2})[-_/](20\d\d)$/);
  if (match) return `${match[3]}-${match[2].padStart(2, '0')}-${match[1].padStart(2, '0')}`;

  console.error(`❌ Unrecognized date "${value}". Use YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY.`);
  process.exit(1);
}

/**
 * Extracts a date string from the filename or falls back to file modification date.
 */
function getDateForFile(fileName, filePath) {
  // 1. YYYY-MM-DD or YYYY_MM_DD
  let match = fileName.match(/\b(20\d\d[-_](0[1-9]|1[0-2])[-_](0[1-9]|[12]\d|3[01]))\b/);
  if (match) return match[1].replace(/_/g, '-');

  // 2. DD-MM-YYYY or DD_MM_YYYY
  match = fileName.match(/\b((0[1-9]|[12]\d|3[01])[-_](0[1-9]|1[0-2])[-_]20\d\d)\b/);
  if (match) return match[1].replace(/_/g, '-');

  // 3. DD_Month (e.g. 21_Oct, 22_Oct, 21-Oct)
  match = fileName.match(/\b(0[1-9]|[12]\d|3[01])[-_\s]?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/i);
  if (match) return `${match[1]}_${match[2].toLowerCase()}`;

  // 4. Isolated day number if in a date-specific subfolder (e.g. *21* or *22*)
  match = fileName.match(/(?:^|[^0-9])(21|22)(?:[^0-9]|$)/);
  if (match) return `${match[1]}_Oct`;

  // 5. Fallback: file modification timestamp (YYYY-MM-DD)
  try {
    const stats = fs.statSync(filePath);
    return stats.mtime.toISOString().split('T')[0];
  } catch {
    return 'unknown_date';
  }
}

/**
 * Recursively find all files in a directory (excluding zipped_data).
 */
function getAllFiles(dir, fileList = []) {
  if (!fs.existsSync(dir)) return fileList;
  const entries = fs.readdirSync(dir, { withFileTypes: true });

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    // Avoid re-scanning output folder or hidden files
    if (entry.name === 'zipped_data' || entry.name.startsWith('.')) continue;

    if (entry.isDirectory()) {
      getAllFiles(fullPath, fileList);
    } else if (entry.isFile()) {
      fileList.push(fullPath);
    }
  }
  return fileList;
}

/**
 * Creates form10_<date>.zip with the same folder structure as the source
 * (e.g. Amritsar/form10-S1902-11-2026-10-28-report.pdf). Rebuilt from scratch on every run.
 */
function zipDate(date, files) {
  const zipFileName = `form10_${date}.zip`;
  const zipFilePath = path.join(OUTPUT_DIR, zipFileName);
  const districts = new Set(files.map((file) => path.basename(path.dirname(file))));

  console.log(`\n📦 Packaging ${files.length} file(s) across ${districts.size} district(s) for [${date}] -> ${zipFileName}`);

  if (fs.existsSync(zipFilePath)) {
    fs.rmSync(zipFilePath);
  }

  // Paths relative to SOURCE_DIR keep the district folders inside the zip; file list is fed via stdin (-@)
  const relativePaths = files.map((file) => path.relative(SOURCE_DIR, file)).sort();
  try {
    execSync(`zip -q "${zipFilePath}" -@`, { cwd: SOURCE_DIR, input: relativePaths.join('\n') });
    console.log(`✅ Created: ${zipFilePath}`);
  } catch (err) {
    console.error(`❌ Failed to zip ${date}:`, err.message);
  }
}

function run() {
  console.log(`🔍 Scanning source directory: ${SOURCE_DIR}`);

  if (!fs.existsSync(SOURCE_DIR)) {
    console.error(`❌ Source directory does not exist: ${SOURCE_DIR}`);
    process.exit(1);
  }

  // Ensure data/zipped_data exists
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    console.log(`📁 Created output folder: ${OUTPUT_DIR}`);
  }

  const allFiles = getAllFiles(SOURCE_DIR);
  console.log(`📄 Found ${allFiles.length} file(s) to process.`);

  // Group files by date
  const filesByDate = {};
  for (const filePath of allFiles) {
    const fileName = path.basename(filePath);
    const dateKey = getDateForFile(fileName, filePath);

    if (!filesByDate[dateKey]) {
      filesByDate[dateKey] = [];
    }
    filesByDate[dateKey].push(filePath);
  }

  if (TARGET_DATE) {
    if (!filesByDate[TARGET_DATE]) {
      console.error(`❌ No files found for date ${TARGET_DATE} in ${SOURCE_DIR}`);
      process.exit(1);
    }
    zipDate(TARGET_DATE, filesByDate[TARGET_DATE]);
  } else {
    for (const [date, files] of Object.entries(filesByDate)) {
      zipDate(date, files);
    }
  }

  console.log(`\n🎉 Zip files are ready in: ${OUTPUT_DIR}`);
}

run();