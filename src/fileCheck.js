import fs from "node:fs";
import path from "node:path";

import data from "../data/districts_and_constituencies_s19.json" with {
  type: "json",
};

const dirPath = "./data/form9_data";

// Folders and files to exclude
const excludeFolders = new Set([
  "captcha_images",
  "temp_captcha",
]);

const excludeFiles = new Set([
  ".DS_Store",
  "districts_and_constituencies_s19.json",
  "captcha_data.json",
  "captcha_labels.json",
  "districts_and_constituencies.json"
]);
// console.table([...excludeFolders, ...excludeFiles]);

function countFiles(directory) {
  let totalFiles = 0;

  const entries = fs.readdirSync(directory, {
    withFileTypes: true,
  });

  for (const entry of entries) {
    // Exclude folder
    if (entry.isDirectory() && excludeFolders.has(entry.name)) {
      continue;
    }

    // Exclude file
    if (entry.isFile() && excludeFiles.has(entry.name)) {
      continue;
    }

    const fullPath = path.join(directory, entry.name);

    // console.log(fullPath);

    if (entry.isDirectory()) {
      totalFiles += countFiles(fullPath);
    } else if (entry.isFile()) {
      totalFiles++;
    }
  }

  return totalFiles;
}


const totalFiles = countFiles(dirPath);

console.log(`Total files: ${totalFiles}`);

// Constituency validation
const totalConstituencies = data.districts.reduce(
  (total, district) => total + district.constituencies.length,
  0
);

console.log("JSON total:", data.totalConstituencies);
console.log("Calculated total:", totalConstituencies);

if (data.totalConstituencies !== totalConstituencies) {
  console.error("❌ Constituency count mismatch!");
} else {
  console.log("✅ Constituency count is correct!");
}