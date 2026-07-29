"use strict";

(function exposeForecastComparison(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.XGBFFPForecastComparison = api;
}(typeof globalThis !== "undefined" ? globalThis : this, () => {
  function normalizeDate(value) {
    return String(value || "").replace(/-/g, "").slice(0, 8);
  }

  function findMatchingDay2Entry(entries, validDate) {
    const target = normalizeDate(validDate);
    if (!/^\d{8}$/.test(target)) return null;
    return (entries || []).find((entry) => (
      normalizeDate(entry?.date) === target
      && entry?.map_available !== false
      && entry?.published !== false
    )) || null;
  }

  function sameValidPeriod(day1, day2) {
    const day1Date = normalizeDate(day1?.date);
    const day2Date = normalizeDate(day2?.date);
    if (!day1Date || day1Date !== day2Date) return false;
    const day1Label = String(day1?.valid_period_label || "").trim();
    const day2Label = String(day2?.valid_period_label || "").trim();
    return !day1Label || !day2Label || day1Label === day2Label;
  }

  function blendWeights(value) {
    const day1 = Math.max(0, Math.min(100, Number(value) || 0)) / 100;
    return { day1, day2: 1 - day1 };
  }

  return {
    blendWeights,
    findMatchingDay2Entry,
    normalizeDate,
    sameValidPeriod,
  };
}));
