"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const comparison = require("../docs/forecast-comparison.js");

const entries = [
  { date: "20260729", issue_date: "20260728", forecast_day: 2, map_available: true, published: true },
  { date: "20260728", issue_date: "20260727", forecast_day: 2, map_available: false, published: true },
];

assert.strictEqual(comparison.findMatchingDay2Entry(entries, "2026-07-29"), entries[0]);
assert.strictEqual(comparison.findMatchingDay2Entry(entries, "20260728"), null);
assert.strictEqual(comparison.findMatchingDay2Entry(entries, "20260730"), null);

assert(comparison.sameValidPeriod(
  { date: "20260729", valid_period_label: "2026-07-29 12Z to 2026-07-30 12Z" },
  { date: "20260729", valid_period_label: "2026-07-29 12Z to 2026-07-30 12Z" },
));
assert(!comparison.sameValidPeriod(
  { date: "20260729", valid_period_label: "2026-07-29 12Z to 2026-07-30 12Z" },
  { date: "20260730", valid_period_label: "2026-07-30 12Z to 2026-07-31 12Z" },
));

assert.deepStrictEqual(comparison.blendWeights(100), { day1: 1, day2: 0 });
assert.deepStrictEqual(comparison.blendWeights(0), { day1: 0, day2: 1 });
assert.deepStrictEqual(comparison.blendWeights(35), { day1: 0.35, day2: 0.65 });

const docs = path.join(__dirname, "../docs");
const day1Archive = JSON.parse(fs.readFileSync(path.join(docs, "archive/index.json"), "utf8")).entries;
const day2Archive = JSON.parse(fs.readFileSync(path.join(docs, "day2/archive/index.json"), "utf8")).entries;
const comparable = day1Archive.filter((entry) => comparison.findMatchingDay2Entry(day2Archive, entry.date));
assert(
  comparable.length >= 45,
  `Expected at least 45 backfilled Day-1/Day-2 pairs, found ${comparable.length}`,
);

const sampleDate = "20250729";
const sampleDay1 = JSON.parse(fs.readFileSync(path.join(docs, `archive/${sampleDate}/map.json`), "utf8"));
const sampleDay2 = JSON.parse(fs.readFileSync(path.join(docs, `day2/archive/${sampleDate}/map.json`), "utf8"));
assert(comparison.sameValidPeriod(sampleDay1, sampleDay2));
assert.strictEqual(sampleDay2.issue_date, "20250728");
assert.strictEqual(sampleDay2.forecast_day, 2);
assert(sampleDay2.layers.pp);
assert(sampleDay1.contours.pp);
for (const key of ["ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc"]) {
  assert(sampleDay1.layers[key]);
  assert(sampleDay2.layers[key]);
}

console.log("Forecast-comparison unit tests passed.");
