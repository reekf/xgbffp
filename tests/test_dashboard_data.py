#!/usr/bin/env python3
"""Unit tests for XGBFFP rolling-verification aggregation."""

from datetime import date
import json
import math
from pathlib import Path
import struct
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_dashboard_data as dashboard


REPO_DIR = Path(__file__).resolve().parents[1]


def test_categorical_metrics_zero_denominators_are_null():
    metrics = dashboard.categorical_metrics(0, 0, 0, 10)
    assert metrics["ets"] is None
    assert metrics["csi"] is None
    assert metrics["pod"] is None
    assert metrics["far"] is None
    assert metrics["frequency_bias"] is None


def test_perfect_all_event_occurrence_ets_is_one():
    metrics = dashboard.risk_occurrence_metrics(45, 0, 0, 0)
    assert metrics["ets"] == 1.0
    assert metrics["csi"] == 1.0
    assert dashboard.categorical_metrics(45, 0, 0, 0)["ets"] is None


def test_threshold_boundaries_are_inclusive():
    truth = [50, 150, 400, 700]
    for threshold, index in zip(dashboard.THRESHOLDS, range(4)):
        metrics = dashboard.daily_product(truth, truth, threshold)
        expected_positives = 4 - index
        assert metrics["hits"] == expected_positives
        assert metrics["misses"] == 0
        assert metrics["false_alarms"] == 0


def test_pp_uses_its_own_category_breaks_for_forecast_risk_bins():
    forecast = [149, 150, 399, 400, 699, 700]
    pp_truth = [99, 100, 199, 200, 399, 400]
    for forecast_threshold, pp_threshold in dashboard.PP_THRESHOLD_BY_FORECAST_THRESHOLD.items():
        metrics = dashboard.daily_product(
            forecast,
            pp_truth,
            forecast_threshold,
            truth_threshold=pp_threshold,
        )
        assert metrics["forecast_threshold_percent"] == forecast_threshold
        assert metrics["reference_threshold_percent"] == pp_threshold


def test_december_is_assigned_to_following_djf():
    start, end, name = dashboard.season_bounds(date(2026, 12, 15))
    assert (start, end, name) == (date(2026, 12, 1), date(2027, 2, 28), "DJF")
    start, end, name = dashboard.season_bounds(date(2027, 1, 5))
    assert (start, end, name) == (date(2026, 12, 1), date(2027, 2, 28), "DJF")


def test_pooled_counts_are_recalculated():
    def record(day, hits, misses, false_alarms, correct_negatives):
        sample_count = hits + misses + false_alarms + correct_negatives
        row = {
            "hits": hits,
            "misses": misses,
            "false_alarms": false_alarms,
            "correct_negatives": correct_negatives,
            "sample_count": sample_count,
            "truth_positive_count": hits + misses,
            "forecast_positive_count": hits + false_alarms,
            "squared_error_sum": 1.0,
        }
        return {
            "date": day,
            "products": {"ml_r40": {str(value): row.copy() for value in dashboard.THRESHOLDS}},
        }

    window = dashboard.aggregate_window(
        [
            record("20260701", 2, 1, 1, 6),
            record("20260702", 3, 2, 1, 4),
            record("20260703", 0, 3, 0, 7),
        ],
        date(2026, 7, 1),
        date(2026, 7, 3),
        "test",
        "monthly",
    )
    result = window["products"]["ml_r40"]["5"]
    assert result["hits"] == 5
    assert result["misses"] == 6
    assert result["false_alarms"] == 2
    assert result["sample_count"] == 30
    assert result["risk_case_count"] == 2
    assert result["truth_risk_case_count"] == 3
    assert result["risk_occurrence_hits"] == 2
    assert result["risk_occurrence_misses"] == 1
    assert result["risk_occurrence_false_alarms"] == 0
    assert result["risk_occurrence_correct_negatives"] == 0
    assert result["risk_occurrence_csi"] == round(2 / 3, 8)
    assert result["risk_occurrence_ets"] == 0.0
    assert result["verified_forecast_count"] == 3
    assert "brier_skill_score" not in result


def test_published_manifests_and_verification_contracts():
    docs = REPO_DIR / "docs"
    for manifest_path in [
        docs / "model-skill/manifest.json",
        docs / "explainability/manifest.json",
    ]:
        manifest = json.loads(manifest_path.read_text())
        assert manifest["schema_version"] == 1
        for figure in manifest["figures"]:
            assert (docs / figure["path"]).is_file()

    skill = json.loads((docs / "model-skill/manifest.json").read_text())
    assert len(skill["figures"]) == 3
    assert [figure["metric"] for figure in skill["figures"]].count("ETS") == 2
    assert [figure["metric"] for figure in skill["figures"]].count("Brier Score") == 1
    assert {figure["source_directory"] for figure in skill["figures"]} == {
        "paper_verification_bs_ets_final"
    }
    for figure in skill["figures"]:
        assert "MRMS" not in figure["title"]
        assert "MRMS" not in figure["target"]
        assert "Brier Skill Score" not in figure["title"]
        assert "risk-area" not in figure["title"].lower()
        if figure["metric"] == "Brier Score":
            assert figure["evaluations"] == [
                "Including Marginal",
                "Excluding Marginal",
            ]

    contingency = json.loads((docs / "model-skill/risk-occurrence.json").read_text())
    assert contingency["schema_version"] == 1
    assert "forecast-day" in contingency["count_unit"]
    assert set(contingency["excluded_products"]) == {
        "ML Local PMM 100km",
        "ML Ensemble Max",
        "ML r100kmV2",
    }
    assert not set(contingency["excluded_products"]) & set(contingency["products"])
    for thresholds in contingency["products"].values():
        for counts in thresholds.values():
            for count_name in [
                "hit_day_count",
                "miss_day_count",
                "false_alarm_day_count",
                "correct_negative_day_count",
            ]:
                assert isinstance(counts[count_name], int)
                assert counts[count_name] >= 0
            assert counts["verified_day_count"] == contingency["verified_case_count"]
            assert counts["forecast_risk_day_count"] == (
                counts["hit_day_count"] + counts["false_alarm_day_count"]
            )
            assert counts["pp_risk_day_count"] == (
                counts["hit_day_count"] + counts["miss_day_count"]
            )
            for metric in ["csi", "ets"]:
                assert counts[metric] is None or math.isfinite(counts[metric])
    assert contingency["verified_case_count"] <= contingency["catalog_case_count"]
    assert contingency["missing_archived_pp_case_count"] == (
        contingency["catalog_case_count"] - contingency["verified_case_count"]
    )
    for product, thresholds in contingency["products"].items():
        assert thresholds["5"]["ets"] is not None, product

    index = json.loads((docs / "verification/index.json").read_text())
    assert index["dataset_class"] == "realtime-issued-verification"
    daily_dates = set(index["daily_dates"])
    for day in daily_dates:
        record = json.loads((docs / f"verification/daily/{day}.json").read_text())
        assert record["schema_version"] == 3
        assert record["dataset_class"] == "realtime-issued-verification"
        assert record["default_reference"] == "practically_perfect"
        assert set(record["references"]) == {"practically_perfect", "ufvs_40km"}
        assert record["products"] == record["references"]["practically_perfect"]["products"]
        for reference in record["references"].values():
            for thresholds in reference["products"].values():
                assert set(thresholds) == {"5", "15", "40", "70"}
                for metrics in thresholds.values():
                    for count_name in [
                        "hits",
                        "misses",
                        "false_alarms",
                        "correct_negatives",
                        "sample_count",
                    ]:
                        assert isinstance(metrics[count_name], int)
                        assert metrics[count_name] >= 0
                    for value in metrics.values():
                        assert value is None or not isinstance(value, float) or math.isfinite(value)
                    assert "brier_skill_score" not in metrics

    rolling = json.loads((docs / "verification/rolling/latest.json").read_text())
    assert rolling["schema_version"] == 4
    assert rolling["dataset_class"] == "realtime-issued-verification"
    assert set(rolling["windows"]) == {"monthly", "seasonal"}
    assert not (docs / "verification/rolling/weekly.json").exists()
    for window in rolling["windows"].values():
        assert window["schema_version"] == 4
        assert window["default_reference"] == "practically_perfect"
        assert set(window["references"]) == {"practically_perfect", "ufvs_40km"}
        assert set(window["verified_dates"]).issubset(daily_dates)
        assert window["missing_day_count"] >= 0
        assert window["start_date"] <= window["end_date"]
        for reference in window["references"].values():
            for thresholds in reference["products"].values():
                for metrics in thresholds.values():
                    assert "brier_skill_score" not in metrics
                    assert 0 <= metrics["risk_case_count"] <= metrics["verified_forecast_count"]
                    assert 0 <= metrics["reference_risk_case_count"] <= metrics["verified_forecast_count"]
                    assert sum(
                        metrics[name]
                        for name in [
                            "risk_occurrence_hits",
                            "risk_occurrence_misses",
                            "risk_occurrence_false_alarms",
                            "risk_occurrence_correct_negatives",
                        ]
                    ) == metrics["verified_forecast_count"]
                    assert metrics["risk_case_count"] == (
                        metrics["risk_occurrence_hits"]
                        + metrics["risk_occurrence_false_alarms"]
                    )
                    assert metrics["reference_risk_case_count"] == (
                        metrics["risk_occurrence_hits"]
                        + metrics["risk_occurrence_misses"]
                    )

    index_html = (docs / "index.html").read_text()
    logo_path = docs / "assets/xgbffp-logo.png"
    logo_bytes = logo_path.read_bytes()
    assert logo_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", logo_bytes[16:24]) == (512, 512)
    assert 'class="brand-mark" src="assets/xgbffp-logo.png' in index_html
    assert 'rel="icon" type="image/png" href="assets/xgbffp-logo.png' in index_html
    assert 'rel="apple-touch-icon" href="assets/xgbffp-logo.png' in index_html
    assert '<option value="40" selected>Moderate or greater</option>' in index_html
    assert '<option value="risk_occurrence_ets" selected>Day-level ETS</option>' in index_html
    assert 'id="fill-opacity" type="range" min="5" max="100" value="100"' in index_html
    assert 'id="single-radar-toggle" type="checkbox"' in index_html
    assert 'id="radar-station-select" disabled' in index_html
    assert 'id="single-radar-play-toggle" type="button" disabled' in index_html
    assert 'id="product-nav-highlight" class="product-nav-highlight"' in index_html
    assert 'id="continuous-probability-toggle" type="checkbox"' in index_html
    assert 'id="continuous-probability-legend"' in index_html
    assert "Radar is available only when its current scans fall inside" in index_html
    assert "Historical cases retain their archived flood-proxy observations" in index_html
    assert "Click a radar-site point on the 2D map" in index_html
    assert '<div class="product-message">' in index_html
    assert 'id="product-message-toggle" type="button" aria-expanded="false"' in index_html
    assert 'id="mping-section" class="layer-section disabled-section" aria-disabled="true"' in index_html
    assert 'id="mping-flood-toggle" type="checkbox" disabled' in index_html
    assert "Probability of flash flooding" in index_html
    for label in [
        "Marginal (≥5%)",
        "Slight (≥15%)",
        "Moderate (≥40%)",
        "High (≥70%)",
    ]:
        assert label in index_html
    assert "Brier Skill Score" not in index_html
    assert "risk-frequency" not in index_html
    assert 'id="running-reference"' in index_html
    assert 'value="practically_perfect"' in index_html
    assert 'value="ufvs_40km"' in index_html
    assert 'value="weekly"' not in index_html

    app_javascript = (docs / "app.js").read_text()
    stylesheet = (docs / "style.css").read_text()
    legend_styles = stylesheet[
        stylesheet.index(".legend {"):
        stylesheet.index(".height-legend {")
    ]
    assert "grid-template-columns: 1fr" in legend_styles
    assert "display: flex" not in legend_styles
    mobile_styles = stylesheet[
        stylesheet.index("@media (max-width: 900px)"):
        stylesheet.index("@media (max-width: 560px)")
    ]
    assert ".product-message.expanded" in mobile_styles
    assert "-webkit-line-clamp: 2" in mobile_styles
    assert "top: 157px" in mobile_styles
    assert ".loading { top: 157px; }" in mobile_styles
    assert "body:has(.loading:not([hidden])) .legend" in mobile_styles
    assert "body:has(#layer-panel-content:not([hidden])) .legend" in mobile_styles
    assert "body:has(.location-briefing:not([hidden])) .product-message" in mobile_styles
    assert 'selected: "ml_r60v2"' in app_javascript
    assert "zoomSnap: 0.25" in app_javascript
    assert "wheelPxPerZoomLevel: 180" in app_javascript
    assert "XGBFFP forecast domain" in app_javascript
    assert 'SINGLE_RADAR_PRODUCT = "N0B"' in app_javascript
    assert "geojson/network.py?network=NEXRAD&only_online=1" in app_javascript
    assert "json/radar.py" in app_javascript
    assert "ridge::${state.selectedSingleRadar}" in app_javascript
    assert 'map.createPane("radarStationPane")' in app_javascript
    assert "function activateSingleRadarStation" in app_javascript
    assert "function startSingleRadarAnimation" in app_javascript
    assert "function setProductMessageExpanded" in app_javascript
    assert "function updateProductNavHighlight" in app_javascript
    assert "document.startViewTransition" in app_javascript
    assert "function selectedCaseSupportsLiveLayers" in app_javascript
    assert "function updateTemporalLayerAvailability" in app_javascript
    assert "function continuousProbabilityActive" in app_javascript
    assert '{ threshold: 100, color: "#31004d" }' in app_javascript
    assert 'selectedCaseSupportsLiveLayers(state.data?.date, Number(frame.time) * 1000)' in app_javascript
    assert '.product-nav-highlight' in stylesheet
    assert '::view-transition-new(root)' in stylesheet
    assert '.continuous-probability-gradient' in stylesheet
    marker_renderer = app_javascript[
        app_javascript.index("function renderRadarStationMarkers"):
        app_javascript.index("async function fetchRadarStations")
    ]
    assert "|| !state.singleRadarEnabled" in marker_renderer
    assert 'mpingVisible: false' in app_javascript
    assert "fetchMping" not in app_javascript
    assert 'map.getPane("floodAlertPane").style.pointerEvents = "none"' in app_javascript
    assert "interactive: false" in app_javascript[
        app_javascript.index("function renderFloodAlerts"):
        app_javascript.index("async function fetchFloodZoneGeometry")
    ]
    assert app_javascript.index('state.viewMode = "3d";') < app_javascript.index(
        "initialize3dMap();",
        app_javascript.index("function setViewMode"),
    )


if __name__ == "__main__":
    test_categorical_metrics_zero_denominators_are_null()
    test_perfect_all_event_occurrence_ets_is_one()
    test_threshold_boundaries_are_inclusive()
    test_december_is_assigned_to_following_djf()
    test_pooled_counts_are_recalculated()
    test_published_manifests_and_verification_contracts()
    print("Dashboard data unit tests passed.")
