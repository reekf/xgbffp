#!/usr/bin/env python3
"""Generate Day-2 independent-test verification and feature-importance assets."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


RADII = (40, 60, 75, 100)
THRESHOLDS = (0.05, 0.15, 0.40, 0.70)
PP_THRESHOLD_BY_FORECAST_THRESHOLD = {0.05: 0.05, 0.15: 0.10, 0.40: 0.20, 0.70: 0.40}
COLORS = {40: "#58c84d", 60: "#e7d938", 75: "#e14b3f", 100: "#a65ad9", "wpc": "#6bbcf2"}


def safe_ratio(a: float, b: float) -> float | None:
    return None if b == 0 else float(a / b)


def load_data(project: Path) -> pd.DataFrame:
    grid_path = project / "df_pp_viewer_with_wpc_ero_day2valid.parquet"
    columns = ["Date", "RAP_Init_Date", "Lat", "Lon", "PP_Any flood proxy", "UFVS_ANY", "WPC_ERO_Risk"]
    frame = pd.read_parquet(grid_path, columns=columns)
    frame["Date"] = frame["Date"].astype(str).str[:8]
    keys = frame[["Date", "Lat", "Lon"]].copy()
    prediction_dir = project / "v33day2valid_singletarget_radius_sensitivity_viewer_prediction_cache"
    for radius in RADII:
        path = prediction_dir / f"v33day2valid_singletarget_radius_sensitivity_predictions_r{radius}km.parquet"
        pred = pd.read_parquet(path, columns=["Date", "Lat", "Lon", "ML_Forecast_Prob"])
        pred["Date"] = pred["Date"].astype(str).str[:8]
        if len(pred) != len(frame) or not pred[["Date", "Lat", "Lon"]].reset_index(drop=True).equals(keys.reset_index(drop=True)):
            raise RuntimeError(f"Day-2 r{radius} test predictions do not align with the canonical grid")
        frame[f"ml_r{radius}"] = pd.to_numeric(pred["ML_Forecast_Prob"], errors="coerce").fillna(0).to_numpy(np.float32)
        del pred
    frame["ml_mean"] = frame[[f"ml_r{r}" for r in RADII]].mean(axis=1).astype(np.float32)
    return frame


def style_axis(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=13, weight="bold")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)


def publish_skill_assets(project: Path, skill: Path, generated: str) -> dict:
    """Publish the finalized Day-2 viewer violins without replotting them."""
    source_directory = "paper_verification_bs_ets_final_v33day2valid"
    source_root = project / source_directory
    source_script = "hazard_ml_v33_day2_verification_viewer.ipynb"
    specs = [
        {
            "source_name": "ets_any_flood_proxy_ets.png",
            "destination_name": "ets_any_flood_proxy.png",
            "title": "Day-2 Any Flood Proxy ETS",
            "metric": "ETS",
            "target": "Any flood proxy",
            "thresholds_percent": [5],
            "source_function": "compute_ets_pod_far / run_final_bs_ets_verification_plots",
        },
        {
            "source_name": "ets_pp_any_flood_proxy_ets.png",
            "destination_name": "ets_practically_perfect.png",
            "title": "Day-2 Practically Perfect ETS by threshold",
            "metric": "ETS",
            "target": "Practically Perfect: Any flood proxy",
            "thresholds_percent": [5, 15, 40, 70],
            "source_function": "compute_ets_pod_far / run_final_bs_ets_verification_plots",
        },
        {
            "source_name": "bs_any_flood_proxy_include_exclude_marginal.png",
            "destination_name": "brier_any_flood_proxy_including_excluding_marginal.png",
            "title": "Day-2 Any Flood Proxy Brier Score",
            "metric": "Brier Score",
            "target": "Any flood proxy",
            "thresholds_percent": [5, 15],
            "evaluations": ["Including Marginal", "Excluding Marginal"],
            "source_function": "run_final_bs_ets_verification_plots",
        },
    ]
    figures = []
    for spec in specs:
        source = source_root / spec["source_name"]
        if not source.is_file():
            raise FileNotFoundError(f"Finalized Day-2 viewer figure is required: {source}")
        destination = skill / spec["destination_name"]
        shutil.copy2(source, destination)
        figure = {
            "generated_utc": generated,
            "metric": spec["metric"],
            "model": "XGBoost v33 Day-2 radius configurations and WPC ERO",
            "path": f"model-skill/{spec['destination_name']}",
            "source_asset": f"{source_directory}/{spec['source_name']}",
            "source_directory": source_directory,
            "source_function": spec["source_function"],
            "source_script": source_script,
            "target": spec["target"],
            "test_case_count": 45,
            "test_date_range": "20240610–20250729",
            "test_period": "2024–2025",
            "thresholds_percent": spec["thresholds_percent"],
            "title": spec["title"],
        }
        if "evaluations" in spec:
            figure["evaluations"] = spec["evaluations"]
        figures.append(figure)

    for stale_name in (
        "ets_ufvs_expanded40.png",
        "brier_including_excluding_marginal.png",
        "metrics.json",
    ):
        (skill / stale_name).unlink(missing_ok=True)

    manifest = {
        "dataset_class": "formal-independent-test-set",
        "figures": figures,
        "forecast_day": 2,
        "generated_utc": generated,
        "schema_version": 1,
        "test_period": "2024–2025",
    }
    (skill / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def risk_occurrence(frame: pd.DataFrame, generated: str) -> dict:
    products = {}
    labels = {**{f"ml_r{r}": f"ML r{r}" for r in RADII}, "ml_mean": "ML ensemble mean", "wpc": "WPC Day 2"}
    for source, label in labels.items():
        products[label] = {}
        column = "WPC_ERO_Risk" if source == "wpc" else source
        for threshold in THRESHOLDS:
            pp_threshold = PP_THRESHOLD_BY_FORECAST_THRESHOLD[threshold]
            counts = dict(hits=0, misses=0, false_alarms=0, correct_negatives=0)
            for _, sub in frame.groupby("Date", sort=True):
                forecast_yes = bool((sub[column].to_numpy(float) >= threshold).any())
                truth_yes = bool((sub["PP_Any flood proxy"].to_numpy(float) >= pp_threshold).any())
                key = "hits" if forecast_yes and truth_yes else "misses" if truth_yes else "false_alarms" if forecast_yes else "correct_negatives"
                counts[key] += 1
            total = sum(counts.values())
            random_hits = ((counts["hits"] + counts["misses"]) * (counts["hits"] + counts["false_alarms"]) / total) if total else None
            denom = None if random_hits is None else counts["hits"] + counts["misses"] + counts["false_alarms"] - random_hits
            if not denom:
                ets = 1.0 if counts["hits"] > 0 and counts["misses"] == 0 and counts["false_alarms"] == 0 else None
            else:
                ets = (counts["hits"] - random_hits) / denom
            csi = safe_ratio(counts["hits"], counts["hits"] + counts["misses"] + counts["false_alarms"])
            products[label][str(int(threshold * 100))] = {
                "threshold_label": f"≥{int(threshold * 100)}%",
                "pp_threshold_percent": int(pp_threshold * 100),
                "hit_day_count": counts["hits"],
                "miss_day_count": counts["misses"],
                "false_alarm_day_count": counts["false_alarms"],
                "correct_negative_day_count": counts["correct_negatives"],
                "forecast_risk_day_count": counts["hits"] + counts["false_alarms"],
                "pp_risk_day_count": counts["hits"] + counts["misses"],
                "verified_day_count": total,
                "csi": csi,
                "ets": ets,
            }
    return {
        "schema_version": 1,
        "forecast_day": 2,
        "dataset_class": "formal-independent-test-set",
        "test_period": "2024–2025",
        "verification_target": "Practically Perfect: Any flood proxy",
        "count_unit": "forecast-day risk-occurrence contingency counts across 45 test cases",
        "generated_utc": generated,
        "products": products,
    }


def publish_feature_importance(project: Path, root: Path, generated: str) -> dict:
    figures = []
    tables = {}
    for radius in RADII:
        manifest_path = project / "prob_flood_models" / f"active_artifacts_v33day2valid_r{radius}km_singletarget_radiusstats_mse_apcp13p7cv_domain.json"
        manifest = json.loads(manifest_path.read_text())
        model_path = Path(manifest.get("current_model_alias") or manifest["model_path"])
        feature_path = Path(manifest.get("current_feature_names_alias") or manifest["feature_names_path"])
        feature_data = json.loads(feature_path.read_text())
        names = feature_data if isinstance(feature_data, list) else feature_data.get("feature_names") or feature_data.get("features")
        model = joblib.load(model_path)
        importance = np.asarray(model.feature_importances_, dtype=float)
        if len(importance) != len(names):
            raise RuntimeError(f"Day-2 r{radius} feature-importance length mismatch")
        order = np.argsort(importance)[::-1][:20]
        selected_names = [str(names[i]) for i in order][::-1]
        selected_values = importance[order][::-1]
        fig, ax = plt.subplots(figsize=(12, 8.5))
        ax.barh(selected_names, selected_values, color=COLORS[radius], edgecolor="#333")
        style_axis(ax, f"Day-2 r{radius} XGBoost feature importance", "Native XGBoost importance")
        ax.set_xlabel("Relative feature importance")
        ax.set_ylabel("")
        ax.tick_params(axis="y", labelsize=7)
        fig.tight_layout()
        relative = f"explainability/importance/r{radius}.png"
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(destination, dpi=220, bbox_inches="tight")
        plt.close(fig)
        tables[f"r{radius}"] = [
            {"feature": str(names[i]), "importance": float(importance[i]), "rank": rank}
            for rank, i in enumerate(order, start=1)
        ]
        figures.append({
            "title": f"Day-2 r{radius} native XGBoost feature importance",
            "kind": "importance",
            "model": f"r{radius}",
            "test_period": "2024–2025",
            "generated_utc": generated,
            "path": relative,
        })
    (root / "explainability").mkdir(parents=True, exist_ok=True)
    (root / "explainability/feature-importance.json").write_text(json.dumps(tables, indent=2) + "\n")
    manifest = {
        "schema_version": 1,
        "forecast_day": 2,
        "dataset_class": "trained-model-feature-importance",
        "test_period": "2024–2025",
        "generated_utc": generated,
        "figures": figures,
    }
    (root / "explainability/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path("/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj"))
    parser.add_argument("--docs-dir", type=Path, default=Path(__file__).resolve().parent / "docs")
    args = parser.parse_args()
    root = args.docs_dir / "day2"
    skill = root / "model-skill"
    skill.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frame = load_data(args.project_dir)
    publish_skill_assets(args.project_dir, skill, generated)
    (skill / "risk-occurrence.json").write_text(json.dumps(risk_occurrence(frame, generated), indent=2) + "\n")
    publish_feature_importance(args.project_dir, root, generated)
    print(f"Published Day-2 test-set verification and feature importance under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
