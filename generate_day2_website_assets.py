#!/usr/bin/env python3
"""Generate Day-2 independent-test verification and feature-importance assets."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


RADII = (40, 60, 75, 100)
THRESHOLDS = (0.05, 0.15, 0.40, 0.70)
COLORS = {40: "#58c84d", 60: "#e7d938", 75: "#e14b3f", 100: "#a65ad9", "wpc": "#6bbcf2"}


def safe_ratio(a: float, b: float) -> float | None:
    return None if b == 0 else float(a / b)


def contingency(forecast: np.ndarray, truth: np.ndarray) -> dict:
    f = np.asarray(forecast, dtype=bool)
    t = np.asarray(truth, dtype=bool)
    hits = int(np.count_nonzero(f & t))
    misses = int(np.count_nonzero(~f & t))
    false_alarms = int(np.count_nonzero(f & ~t))
    correct_negatives = int(np.count_nonzero(~f & ~t))
    total = hits + misses + false_alarms + correct_negatives
    random_hits = safe_ratio((hits + misses) * (hits + false_alarms), total)
    ets_denom = None if random_hits is None else hits + misses + false_alarms - random_hits
    ets = None if random_hits is None or ets_denom == 0 else (hits - random_hits) / ets_denom
    csi = safe_ratio(hits, hits + misses + false_alarms)
    return {
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "correct_negatives": correct_negatives,
        "ets": ets,
        "csi": csi,
    }


def expand_40km(lat: np.ndarray, lon: np.ndarray, mask: np.ndarray) -> np.ndarray:
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return np.zeros(mask.shape, dtype=bool)
    lat_r = np.deg2rad(lat)
    lon_r = np.deg2rad(lon)
    xyz = np.column_stack((np.cos(lat_r) * np.cos(lon_r), np.cos(lat_r) * np.sin(lon_r), np.sin(lat_r)))
    tree = cKDTree(xyz)
    chord = 2.0 * np.sin((40.0 / 6371.0) / 2.0)
    out = np.zeros(mask.shape, dtype=bool)
    for neighbors in tree.query_ball_point(xyz[np.flatnonzero(mask)], chord):
        if neighbors:
            out[np.asarray(neighbors, dtype=np.int64)] = True
    return out


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


def save_ets_pp(frame: pd.DataFrame, output: Path) -> dict:
    sources = [f"ml_r{r}" for r in RADII] + ["ml_mean", "wpc"]
    labels = [f"ML r{r}" for r in RADII] + ["ML mean", "WPC Day 2"]
    values = defaultdict(list)
    pp = pd.to_numeric(frame["PP_Any flood proxy"], errors="coerce").fillna(0).to_numpy(float)
    for threshold in THRESHOLDS:
        truth = pp >= threshold
        for source in sources:
            column = "WPC_ERO_Risk" if source == "wpc" else source
            forecast = pd.to_numeric(frame[column], errors="coerce").fillna(0).to_numpy(float) >= threshold
            values[source].append(contingency(forecast, truth)["ets"])
    x = np.arange(len(THRESHOLDS))
    width = 0.12
    fig, ax = plt.subplots(figsize=(12, 6.5))
    for index, (source, label) in enumerate(zip(sources, labels)):
        color = COLORS.get(int(source.split("r")[-1]), "#9ca7af") if source.startswith("ml_r") else (COLORS["wpc"] if source == "wpc" else "#d0d4d8")
        ax.bar(x + (index - 2.5) * width, values[source], width, label=label, color=color, edgecolor="#333", linewidth=0.4)
    ax.axhline(0, color="#333", linewidth=0.8)
    ax.set_xticks(x, ["Marginal ≥5%", "Slight ≥15%", "Moderate ≥40%", "High ≥70%"])
    style_axis(ax, "Day-2 ETS against Practically Perfect", "Equitable Threat Score")
    ax.legend(ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return {source: values[source] for source in sources}


def save_ets_ufvs(frame: pd.DataFrame, output: Path) -> tuple[dict, dict[str, np.ndarray]]:
    expanded_by_date = {}
    for day, sub in frame.groupby("Date", sort=True):
        idx = sub.index.to_numpy()
        expanded_by_date[str(day)] = expand_40km(
            sub["Lat"].to_numpy(float),
            sub["Lon"].to_numpy(float),
            pd.to_numeric(sub["UFVS_ANY"], errors="coerce").fillna(0).to_numpy(float) > 0,
        )
    truth = np.zeros(len(frame), dtype=bool)
    for day, sub in frame.groupby("Date", sort=True):
        truth[sub.index.to_numpy()] = expanded_by_date[str(day)]
    sources = [f"ml_r{r}" for r in RADII] + ["ml_mean", "wpc"]
    labels = [f"ML r{r}" for r in RADII] + ["ML mean", "WPC Day 2"]
    metrics = {}
    for source in sources:
        column = "WPC_ERO_Risk" if source == "wpc" else source
        metrics[source] = contingency(frame[column].to_numpy(float) >= 0.05, truth)
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = [COLORS[r] for r in RADII] + ["#d0d4d8", COLORS["wpc"]]
    ax.bar(labels, [metrics[source]["ets"] for source in sources], color=colors, edgecolor="#333")
    ax.axhline(0, color="#333", linewidth=0.8)
    style_axis(ax, "Day-2 ETS against UFVS flood proxies expanded 40 km", "Equitable Threat Score (≥5% forecast)")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return metrics, expanded_by_date


def save_brier(frame: pd.DataFrame, output: Path) -> dict:
    sources = [f"ml_r{r}" for r in RADII] + ["ml_mean", "wpc"]
    labels = [f"ML r{r}" for r in RADII] + ["ML mean", "WPC Day 2"]
    pp = frame["PP_Any flood proxy"].to_numpy(float)
    results = {source: [] for source in sources}
    for truth_threshold in (0.05, 0.15):
        truth = (pp >= truth_threshold).astype(float)
        for source in sources:
            column = "WPC_ERO_Risk" if source == "wpc" else source
            probability = np.clip(frame[column].to_numpy(float), 0, 1)
            results[source].append(float(np.mean((probability - truth) ** 2)))
    x = np.arange(len(sources))
    width = 0.36
    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.bar(x - width / 2, [results[s][0] for s in sources], width, label="Including marginal (PP ≥5%)", color="#58c84d")
    ax.bar(x + width / 2, [results[s][1] for s in sources], width, label="Excluding marginal (PP ≥15%)", color="#e7d938")
    ax.set_xticks(x, labels, rotation=20)
    style_axis(ax, "Day-2 Brier Score", "Brier Score (lower is better)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return results


def risk_occurrence(frame: pd.DataFrame, generated: str) -> dict:
    products = {}
    labels = {**{f"ml_r{r}": f"ML r{r}" for r in RADII}, "ml_mean": "ML ensemble mean", "wpc": "WPC Day 2"}
    for source, label in labels.items():
        products[label] = {}
        column = "WPC_ERO_Risk" if source == "wpc" else source
        for threshold in THRESHOLDS:
            counts = dict(hits=0, misses=0, false_alarms=0, correct_negatives=0)
            for _, sub in frame.groupby("Date", sort=True):
                forecast_yes = bool((sub[column].to_numpy(float) >= threshold).any())
                truth_yes = bool((sub["PP_Any flood proxy"].to_numpy(float) >= threshold).any())
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
    pp_metrics = save_ets_pp(frame, skill / "ets_practically_perfect.png")
    ufvs_metrics, _ = save_ets_ufvs(frame, skill / "ets_ufvs_expanded40.png")
    brier = save_brier(frame, skill / "brier_including_excluding_marginal.png")
    figures = [
        {"title": "Day-2 Practically Perfect ETS by threshold", "metric": "ETS", "target": "Practically Perfect: Any flood proxy", "test_period": "2024–2025", "test_case_count": 45, "path": "model-skill/ets_practically_perfect.png"},
        {"title": "Day-2 UFVS-expanded-40-km ETS", "metric": "ETS", "target": "UFVS Any flood proxy expanded 40 km", "test_period": "2024–2025", "test_case_count": 45, "path": "model-skill/ets_ufvs_expanded40.png"},
        {"title": "Day-2 Brier Score: including and excluding marginal", "metric": "Brier Score", "target": "Practically Perfect: Any flood proxy", "test_period": "2024–2025", "test_case_count": 45, "evaluations": ["Including Marginal", "Excluding Marginal"], "path": "model-skill/brier_including_excluding_marginal.png"},
    ]
    manifest = {"schema_version": 1, "forecast_day": 2, "dataset_class": "formal-independent-test-set", "test_period": "2024–2025", "generated_utc": generated, "figures": figures}
    (skill / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (skill / "risk-occurrence.json").write_text(json.dumps(risk_occurrence(frame, generated), indent=2) + "\n")
    (skill / "metrics.json").write_text(json.dumps({"pp_ets": pp_metrics, "ufvs_ets": ufvs_metrics, "brier": brier}, indent=2) + "\n")
    publish_feature_importance(args.project_dir, root, generated)
    print(f"Published Day-2 test-set verification and feature importance under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
