#!/usr/bin/env python3
"""Build sanitized browser map data for one v33 forecast date.

The output contains only public forecast/verification fields: coordinates,
probabilities, categorical contour lines, and valid-period metadata.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

PROJECT_DIR = Path("/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj")
REALTIME_DIR = PROJECT_DIR / "v33_realtime_radiusstats_forecasts" / "verified"
REALTIME_WPC_DIR = PROJECT_DIR / "realtime_wpc_ero_cache_v33"
HISTORICAL_GRID = PROJECT_DIR / "df_pp_viewer_with_wpc_ero_day1.parquet"
HISTORICAL_PREDICTIONS = PROJECT_DIR / "v33_singletarget_radius_sensitivity_viewer_prediction_cache"
OBSERVATION_DIR = PROJECT_DIR / "v33_realtime_radiusstats_forecasts" / "ufvs_raw"
REALTIME_FEATURE_DIR = PROJECT_DIR / "v33_realtime_radiusstats_forecasts" / "features"
RADII = (40, 60, 75, 100)
MODEL_MEMBER_COLUMNS = (
    "ML_r40_Prob",
    "ML_r60_Prob",
    "ML_r75_Prob",
    "ML_r100_Prob",
)
THRESHOLDS = (0.05, 0.15, 0.40, 0.70)
PP_THRESHOLDS = (0.05, 0.10, 0.20, 0.40)
TOP_PREDICTORS = {
    40: (
        ("qpf_ffg_ratio_spread", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R40km_Std", "Spread of 6-h QPF / 6-h FFG ratio", "ratio", 0.851431, "Higher values generally increase flash-flood probability."),
        ("qpf_ffg_ratio_max", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R40km_Max", "Max 6-h QPF / 6-h FFG ratio", "ratio", 0.484859, "Higher values increase flash-flood probability."),
        ("ffg_minimum_mean", "Guidance_FFG_1h3h6h12h24h_mm_Min_R40km_Mean", "Mean neighborhood minimum FFG", "mm", 0.152549, "Lower values increase flash-flood probability; higher values decrease it."),
        ("mcs_maintenance_min", "MCS_Maintenance_Prob_RAPCalc_0_6_12_18_24h_Mean_R40km_Min", "Minimum MCS maintenance probability", "probability", 0.148210, "Higher values generally increase flash-flood probability."),
        ("ffg_minimum", "Guidance_FFG_1h3h6h12h24h_mm_Min_R40km_Min", "Minimum FFG", "mm", 0.143200, "Lower values increase flash-flood probability; higher values decrease it."),
    ),
    60: (
        ("qpf_ffg_ratio_spread", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R60km_Std", "Spread of 6-h QPF / 6-h FFG ratio", "ratio", 0.510582, "Higher values generally increase flash-flood probability."),
        ("qpf_ffg_ratio_max", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R60km_Max", "Max 6-h QPF / 6-h FFG ratio", "ratio", 0.415524, "Higher values increase flash-flood probability."),
        ("qpf_ffg_duration_spread", "Forecast_APCP_to_Guidance_FFG_Ratio_Across_6h12h24h_Mean_R60km_Std", "Spread of mean QPF / FFG ratio", "ratio", 0.207766, "Higher values generally increase flash-flood probability."),
        ("ffg_mean_spread", "Guidance_FFG_1h3h6h12h24h_mm_Mean_R60km_Std", "Spread of mean FFG", "mm", 0.167175, "Higher values generally increase flash-flood probability."),
        ("ffg_minimum_mean", "Guidance_FFG_1h3h6h12h24h_mm_Min_R60km_Mean", "Mean neighborhood minimum FFG", "mm", 0.133088, "Lower values increase flash-flood probability; higher values decrease it."),
    ),
    75: (
        ("qpf_ffg_ratio_max", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R75km_Max", "Max 6-h QPF / 6-h FFG ratio", "ratio", 0.757887, "Higher values increase flash-flood probability."),
        ("qpf_ffg_ratio_spread", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R75km_Std", "Spread of 6-h QPF / 6-h FFG ratio", "ratio", 0.415814, "Higher values generally increase flash-flood probability."),
        ("qpf_24h_spread_max", "Forecast_APCP_RunningTotals_0to6_0to12_0to18_0to24h_mm_Std_R75km_Max", "Max spread of 24-h running-total QPF", "mm", 0.235574, "Higher values generally increase flash-flood probability."),
        ("ffg_minimum_mean", "Guidance_FFG_1h3h6h12h24h_mm_Min_R75km_Mean", "Mean neighborhood minimum FFG", "mm", 0.193857, "Lower values increase flash-flood probability; higher values decrease it."),
        ("ffg_spread_spread", "Guidance_FFG_1h3h6h12h24h_mm_Std_R75km_Std", "Neighborhood variability of FFG spread", "mm", 0.177722, "Higher values generally increase flash-flood probability."),
    ),
    100: (
        ("qpf_ffg_ratio_max", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R100km_Max", "Max 6-h QPF / 6-h FFG ratio", "ratio", 0.814045, "Higher values increase flash-flood probability."),
        ("qpf_ffg_ratio_spread", "Forecast_APCP_Max_6h_Window_0to24h_to_Guidance_FFG_06h_Ratio_R100km_Std", "Spread of 6-h QPF / 6-h FFG ratio", "ratio", 0.273313, "Higher values generally increase flash-flood probability."),
        ("ffg_mean_spread", "Guidance_FFG_1h3h6h12h24h_mm_Mean_R100km_Std", "Spread of mean FFG", "mm", 0.217604, "Higher values generally increase flash-flood probability."),
        ("ffg_minimum", "Guidance_FFG_1h3h6h12h24h_mm_Min_R100km_Min", "Minimum FFG", "mm", 0.183194, "Lower values increase flash-flood probability; higher values decrease it."),
        ("qpf_24h_spread_max", "Forecast_APCP_RunningTotals_0to6_0to12_0to18_0to24h_mm_Std_R100km_Max", "Max spread of 24-h running-total QPF", "mm", 0.172194, "Higher values generally increase flash-flood probability."),
    ),
}
OBSERVATION_SPECS = {
    "stage4_ffg": ("Stage IV > FFG", "ST4gFFG"),
    "stage4_ari": ("Stage IV ARI", "ST4gARI"),
    "usgs": ("USGS", "USGS"),
    "flash_lsr": ("Flash-flood reports", "LSRFLASH"),
    "regular_flood_lsr": ("Flood reports", "LSRREG"),
}

LAYER_SPECS = {
    "ml_r40": ("ML r40 km", "ML_r40_Prob", "forecast"),
    "ml_r60": ("ML r60 km", "ML_r60_Prob", "forecast"),
    "ml_r75": ("ML r75 km", "ML_r75_Prob", "forecast"),
    "ml_r100": ("ML r100 km", "ML_r100_Prob", "forecast"),
    "ml_mean": ("ML Ensemble Mean", "ML_Ensemble_Mean", "forecast"),
    "wpc": ("WPC ERO", "WPC_ERO_Risk", "reference"),
    "pp": ("Practically Perfect", "PP_Any flood proxy", "verification"),
}


def date8(value: str) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) < 8:
        raise ValueError(f"Expected YYYYMMDD date, got {value!r}")
    return text[:8]


def _read_date(path: Path, date: str, columns: list[str] | None = None) -> pd.DataFrame:
    kwargs = {"filters": [("Date", "==", date)]}
    if columns is not None:
        kwargs["columns"] = columns
    frame = pd.read_parquet(path, **kwargs)
    if frame.empty:
        # Some older parquet files store Date with a non-string dtype/filter encoding.
        frame = pd.read_parquet(path, columns=columns)
        frame = frame[frame["Date"].astype(str).str[:8] == date].copy()
    return frame


def _sort_grid(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["Date"] = out["Date"].astype(str).str[:8]
    return out.sort_values(["Lat", "Lon"]).reset_index(drop=True)


def _merge_aligned(base: pd.DataFrame, extra: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    left = _sort_grid(base)
    right = _sort_grid(extra)
    keys = ["Date", "Lat", "Lon"]
    if len(left) != len(right) or not left[keys].equals(right[keys]):
        raise RuntimeError("Map-data source grids do not align on Date/Lat/Lon")
    for column in columns:
        left[column] = right[column].to_numpy()
    return left


def load_historical(date: str) -> pd.DataFrame:
    base = _sort_grid(
        _read_date(
            HISTORICAL_GRID,
            date,
            ["Date", "Lat", "Lon", "WPC_ERO_Risk", "PP_Any flood proxy"],
        )
    )
    if base.empty:
        raise RuntimeError(f"No historical WPC/verification grid for {date}")
    for radius in RADII:
        path = HISTORICAL_PREDICTIONS / f"v33_singletarget_radius_sensitivity_predictions_r{radius}km.parquet"
        pred = _read_date(path, date, ["Date", "Lat", "Lon", "ML_Forecast_Prob"])
        pred = pred.rename(columns={"ML_Forecast_Prob": f"ML_r{radius}_Prob"})
        base = _merge_aligned(base, pred, [f"ML_r{radius}_Prob"])
    return base


def _preferred_realtime_forecast(date: str) -> Path:
    # Keep the issued forecast as the authoritative source for forecast and
    # WPC layers. Verification parquets intentionally contain PP/UFVS fields
    # but do not necessarily repeat WPC_ERO_Risk.
    exact = REALTIME_DIR / f"realtime_verified_v33_multiradius_r40_r60_r75_r100_{date}.parquet"
    if exact.exists():
        return exact
    candidates = sorted(
        REALTIME_DIR.glob(f"realtime_verified_v33_multiradius_*_{date}.parquet"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    verification = _realtime_verification(date)
    if verification is not None:
        return verification
    raise RuntimeError(f"No realtime multi-radius forecast parquet for {date}")


def _realtime_verification(date: str) -> Path | None:
    exact = REALTIME_DIR / f"realtime_ufvs_verified_v33_multiradius_r40_r60_r75_r100_{date}.parquet"
    if exact.exists():
        return exact
    candidates = sorted(
        REALTIME_DIR.glob(f"realtime_ufvs_verified_v33_multiradius_*_{date}.parquet"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_realtime(date: str) -> pd.DataFrame:
    base = _sort_grid(pd.read_parquet(_preferred_realtime_forecast(date)))
    verification_path = _realtime_verification(date)
    if verification_path is not None:
        verification = pd.read_parquet(verification_path)
        verification_columns = [
            column
            for column in (
                "PP_Any flood proxy",
                "UFVS_ANY",
                "PP_Reconstruction_Recipe",
                "PP_Reconstruction_Label",
                "PP_Reconstruction_Required_Sources_Complete",
            )
            if column in verification.columns
        ]
        if verification_columns:
            base = _merge_aligned(base, verification, verification_columns)
    if "WPC_ERO_Risk" not in base.columns:
        wpc_candidates = []
        for pattern in (
            f"wpc_ero_day1_issue{date}_valid{date}_12to12_*rows.parquet",
            f"wpc_ero_risk_grid_{date}_valid12to12_*rows.parquet",
        ):
            wpc_candidates.extend(REALTIME_WPC_DIR.glob(pattern))
        wpc_candidates = sorted(
            set(wpc_candidates), key=lambda path: path.stat().st_mtime, reverse=True
        )
        if wpc_candidates:
            wpc = pd.read_parquet(wpc_candidates[0])
            base = _merge_aligned(base, wpc, ["WPC_ERO_Risk"])
    return base


def load_case(date: str, source: str = "auto") -> tuple[pd.DataFrame, str]:
    if source in {"auto", "realtime"}:
        try:
            return load_realtime(date), "realtime"
        except Exception:
            if source == "realtime":
                raise
    return load_historical(date), "historical"


def probability_millipercent(values: pd.Series) -> list[int]:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(0.0, 1.0).to_numpy(float)
    # 0..1000 represents probability percent to one decimal place in the browser.
    return np.rint(numeric * 1000.0).astype(np.uint16).tolist()


def expand_binary_40km(frame: pd.DataFrame, column: str) -> np.ndarray:
    mask = pd.to_numeric(frame[column], errors="coerce").fillna(0).to_numpy(float) > 0
    if not mask.any():
        return np.zeros(len(frame), dtype=np.uint16)
    lat = np.deg2rad(pd.to_numeric(frame["Lat"], errors="coerce").to_numpy(float))
    lon = np.deg2rad(pd.to_numeric(frame["Lon"], errors="coerce").to_numpy(float))
    xyz = np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))
    tree = cKDTree(xyz)
    chord = 2.0 * np.sin((40.0 / 6371.0) / 2.0)
    expanded = np.zeros(len(frame), dtype=bool)
    for neighbors in tree.query_ball_point(xyz[np.flatnonzero(mask)], r=chord):
        if neighbors:
            expanded[np.asarray(neighbors, dtype=np.int64)] = True
    return expanded.astype(np.uint16) * 1000


def load_top_predictors(date: str, base: pd.DataFrame) -> dict:
    payload = {}
    for radius, specs in TOP_PREDICTORS.items():
        path = REALTIME_FEATURE_DIR / f"realtime_features_v33_r{radius}km_{date}.parquet"
        if not path.exists():
            continue
        columns = ["Date", "Lat", "Lon"] + [spec[1] for spec in specs]
        features = pd.read_parquet(path, columns=columns)
        aligned = _merge_aligned(base[["Date", "Lat", "Lon"]], features, columns[3:])
        maximum_importance = max(spec[4] for spec in specs)
        radius_payload = {}
        for rank, (key, column, label, units, importance, direction) in enumerate(specs, start=1):
            numeric = pd.to_numeric(aligned[column], errors="coerce").to_numpy(float)
            finite = numeric[np.isfinite(numeric)]
            if finite.size == 0:
                continue
            low, high = np.nanpercentile(finite, [2, 98])
            if not np.isfinite(low) or not np.isfinite(high) or high <= low:
                low, high = float(np.nanmin(finite)), float(np.nanmax(finite))
            span = high - low
            encoded = np.zeros(len(numeric), dtype=np.uint16)
            if span > 0:
                encoded = np.rint(np.clip((numeric - low) / span, 0.0, 1.0) * 1000.0)
                encoded = np.nan_to_num(encoded, nan=0.0).astype(np.uint16)
            radius_payload[key] = {
                "rank": rank,
                "label": label,
                "units": units,
                "direction": direction,
                "mean_abs_shap": round(importance, 6),
                "relative_importance_percent": round(importance / maximum_importance * 100.0, 1),
                "scale_min": round(float(low), 4),
                "scale_max": round(float(high), 4),
                "values": encoded.tolist(),
            }
        payload[f"r{radius}"] = radius_payload
    return payload


def contour_segments(
    lon: np.ndarray,
    lat: np.ndarray,
    values: np.ndarray,
    thresholds=THRESHOLDS,
) -> dict[str, list[list[list[float]]]]:
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=1.0, neginf=0.0)
    result: dict[str, list[list[list[float]]]] = {}
    fig, ax = plt.subplots(figsize=(2, 2))
    try:
        contours = ax.tricontour(lon, lat, values, levels=thresholds)
        for threshold, groups in zip(thresholds, contours.allsegs):
            lines = []
            for group in groups:
                if len(group) < 2:
                    continue
                # Leaflet consumes [lat, lon]. Four decimals is ~10 m and keeps files compact.
                line = [[round(float(y), 4), round(float(x), 4)] for x, y in group]
                lines.append(line)
            result[str(int(round(threshold * 100)))] = lines
    finally:
        plt.close(fig)
    return result


def _parse_observation_points(text: str) -> list[list[float]]:
    points = set()
    for line in str(text).splitlines():
        values = [float(value) for value in re.findall(r"[-+]?\d+(?:\.\d+)?", line)]
        if len(values) < 2:
            continue
        first, second = values[:2]
        if 15.0 <= first <= 60.0 and -130.0 <= second <= -60.0:
            lat, lon = first, second
        elif 15.0 <= second <= 60.0 and -130.0 <= first <= -60.0:
            lat, lon = second, first
        else:
            continue
        if 30.0 <= lat <= 50.1 and -105.1 <= lon <= -80.4:
            points.add((round(lat, 4), round(lon, 4)))
    return [list(point) for point in sorted(points)]


def load_observations(date: str) -> dict:
    end = (datetime.strptime(date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    observations = {}
    for key, (label, prefix) in OBSERVATION_SPECS.items():
        candidates = sorted(OBSERVATION_DIR.glob(f"{prefix}_s{date}*_e{end}12.txt"))
        if not candidates:
            continue
        points = _parse_observation_points(candidates[0].read_text(errors="ignore"))
        observations[key] = {"label": label, "points": points}
    return observations


def build_payload(
    frame: pd.DataFrame,
    date: str,
    source: str,
    *,
    forecast_day: int = 1,
    issue_date: str | None = None,
) -> dict:
    required = ["Date", "Lat", "Lon"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise RuntimeError(f"Map dataframe missing required columns: {missing}")
    frame = _sort_grid(frame)
    member_columns = [column for column in MODEL_MEMBER_COLUMNS if column in frame.columns]
    if member_columns:
        # Always derive this from the members so old caches containing a Local
        # PMM cannot leak the retired product back onto the website.
        frame["ML_Ensemble_Mean"] = frame[member_columns].apply(
            pd.to_numeric, errors="coerce"
        ).mean(axis=1, skipna=True)
    lat = pd.to_numeric(frame["Lat"], errors="coerce").to_numpy(float)
    lon = pd.to_numeric(frame["Lon"], errors="coerce").to_numpy(float)
    if not np.isfinite(lat).all() or not np.isfinite(lon).all():
        raise RuntimeError("Map grid contains invalid coordinates")

    layers = {}
    contours = {}
    reconstruction_recipe = ""
    reconstruction_label = ""
    reconstruction_complete = None
    if "PP_Reconstruction_Recipe" in frame:
        recipes = frame["PP_Reconstruction_Recipe"].dropna().astype(str)
        reconstruction_recipe = recipes.iloc[0] if len(recipes) else ""
    if "PP_Reconstruction_Label" in frame:
        labels = frame["PP_Reconstruction_Label"].dropna().astype(str)
        reconstruction_label = labels.iloc[0] if len(labels) else ""
    if "PP_Reconstruction_Required_Sources_Complete" in frame:
        complete = frame["PP_Reconstruction_Required_Sources_Complete"].dropna()
        reconstruction_complete = bool(complete.iloc[0]) if len(complete) else None
    for key, (label, column, kind) in LAYER_SPECS.items():
        if column not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).clip(0.0, 1.0)
        layer_thresholds = PP_THRESHOLDS if key == "pp" else THRESHOLDS
        layer_label = reconstruction_label if key == "pp" and reconstruction_label else label
        layers[key] = {
            "label": layer_label,
            "kind": kind,
            "values": probability_millipercent(numeric),
            "risk_threshold_percent": [int(round(value * 100)) for value in layer_thresholds],
        }
        contours[key] = contour_segments(
            lon,
            lat,
            numeric.to_numpy(float),
            thresholds=layer_thresholds,
        )

    start = datetime.strptime(date + "12", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    day = int(forecast_day)
    issue = date8(issue_date) if issue_date else (
        (datetime.strptime(date, "%Y%m%d") - timedelta(days=day - 1)).strftime("%Y%m%d")
    )
    verification_truths = {}
    if "PP_Any flood proxy" in frame:
        verification_truths["practically_perfect"] = {
            "label": reconstruction_label or "Official WPC Practically Perfect",
            "values": probability_millipercent(frame["PP_Any flood proxy"]),
            "provenance": (
                {
                    "product_class": "reconstructed",
                    "recipe_id": reconstruction_recipe,
                    "required_sources_complete": reconstruction_complete,
                    "valid_cycle": "12Z-to-12Z",
                }
                if reconstruction_recipe
                else {"product_class": "official-wpc-archive"}
            ),
        }
    if "UFVS_ANY" in frame:
        verification_truths["ufvs_40km"] = {
            "label": "UFVS flood proxies (40-km expansion)",
            "values": expand_binary_40km(frame, "UFVS_ANY").tolist(),
        }
    return {
        "schema_version": 5,
        "date": date,
        "issue_date": issue,
        "forecast_day": day,
        "valid_period_label": f"{start:%Y-%m-%d} 12Z to {end:%Y-%m-%d} 12Z",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_class": source,
        "probability_encoding": "integer 0..1000; divide by 10 for percent",
        "risk_threshold_percent": [5, 15, 40, 70],
        "pp_risk_threshold_percent": [5, 10, 20, 40],
        "grid": {
            "lat": np.round(lat, 5).tolist(),
            "lon": np.round(lon, 5).tolist(),
        },
        "layers": layers,
        "contours": contours,
        "observations": load_observations(date) if "pp" in layers else {},
        "verification_truths": verification_truths,
        "predictors": load_top_predictors(date, frame) if source == "realtime" and day == 1 else {},
    }


def write_frame_map_data(
    frame: pd.DataFrame,
    date: str,
    output: Path,
    source: str,
    *,
    forecast_day: int = 1,
    issue_date: str | None = None,
) -> Path:
    date = date8(date)
    payload = build_payload(frame, date, source, forecast_day=forecast_day, issue_date=issue_date)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(
        f"Wrote interactive map data: {output} "
        f"rows={len(frame):,} layers={list(payload['layers'])} size={output.stat().st_size:,} bytes",
        flush=True,
    )
    return output


def write_map_data(
    date: str,
    output: Path,
    source: str = "auto",
    *,
    forecast_day: int = 1,
    issue_date: str | None = None,
    input_parquet: Path | None = None,
) -> Path:
    date = date8(date)
    if input_parquet is not None:
        frame = pd.read_parquet(input_parquet)
        selected_source = source if source != "auto" else "realtime"
    else:
        frame, selected_source = load_case(date, source=source)
    return write_frame_map_data(
        frame,
        date,
        output,
        selected_source,
        forecast_day=forecast_day,
        issue_date=issue_date,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Forecast valid-start date YYYYMMDD")
    parser.add_argument("--output", required=True, help="Destination map.json")
    parser.add_argument("--source", choices=("auto", "realtime", "historical"), default="auto")
    parser.add_argument("--forecast-day", type=int, choices=(1, 2), default=1)
    parser.add_argument("--issue-date", default=None)
    parser.add_argument("--input-parquet", type=Path, default=None)
    args = parser.parse_args()
    write_map_data(
        args.date,
        Path(args.output),
        source=args.source,
        forecast_day=args.forecast_day,
        issue_date=args.issue_date,
        input_parquet=args.input_parquet,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
