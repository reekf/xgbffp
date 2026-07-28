#!/usr/bin/env python3
"""
Standalone realtime ML probability plotter with an internal generation gate. Checked v8: legacy-cache compatible.

Run from shell/cron/systemd. It does NOT require a notebook session.

Core workflow:
  1) Download/read HRRR simulated brightness temperature and composite
     reflectivity for the 12Z-to-12Z valid window. Track objects through time
     using the actual PyFLEXTRKR lifecycle pipeline. Default: 12Z HRRR f00-f24,
     SBT < 241 K and cold shield >6.0e4 km^2 for at least 3 hours, with the
     precipitation/convective structure lasting at least 4 hours.
  2) If the HRRR MCS trigger fires, build realtime features using the generated v33
     helper script used during training. The helper's GRIB validation is patched to
     avoid a pygrib/eccodes segfault observed on Python 3.14.
  3) Load saved model/scaler/feature-name artifacts for each requested radius.
  4) Predict probabilities; fail loudly if trained predictors are missing or contain
     NaN/inf unless --allow-feature-nan-fill-zero is explicitly set.
  5) Merge/rasterize WPC Day-1 ERO if available.
  6) Save public ERO-category maps for each radius member plus WPC only.
     Public forecast graphics never include internal generation-gate contours.

Example automation run:
  python realtime_mcs_trigger_plot_standalone_v8_checked_legacycache_radii_wpc.py --date 20260629 --radii 40 60 75 100

If an upstream detector already decided to trigger:
  python realtime_mcs_trigger_plot_standalone_v8_checked_legacycache_radii_wpc.py --date 20260629 --force-trigger --no-run-hrrr-detector
"""

from __future__ import annotations

import argparse
import gc
import glob
import importlib.util
import json
import math
import os
import re
import sys
import traceback
import types
import contextlib
import shutil
import subprocess
import warnings


# Keep realtime extraction conservative in an automation context. Some GRIB/HDF/BLAS
# stacks are unstable when helper code spins up extra workers during import/extraction.
for _k in [
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "LOKY_MAX_CPU_COUNT",
]:
    os.environ.setdefault(_k, "1")

try:
    import faulthandler
    faulthandler.enable()
except Exception:
    pass

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from pyflextrkr_hrrr import prepare_and_run_pyflextrkr

# Headless-safe plotting for automation.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Patch

try:
    import joblib
except Exception as exc:
    raise RuntimeError("joblib is required to load model/scaler artifacts.") from exc

try:
    from scipy import ndimage as ndi
    from scipy.spatial import cKDTree
    HAS_SCIPY = True
except Exception as exc:
    raise RuntimeError("scipy is required for MCS connected components and radius/PP operations.") from exc

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except Exception:
    HAS_CARTOPY = False

# Optional readers/fetchers are imported inside their functions so that the script still
# runs for force-trigger cases without xarray/geopandas/requests installed.


# ======================================================================================
# User/project defaults
# ======================================================================================

DEFAULT_PROJECT_DIRS = [
    "/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj",
    "/home/tyreekfrazier/ISU_Research/fall_2025_ml_proj",
    "/home/tyreekfrazier/ISU_RESEARCH_LOCAL_RUN/fall_2025_ml_proj",
]

RUN_VERSION_TAG = "v33"
DEFAULT_RADII_KM = [40, 60, 75, 100]
R60KM_V2_LABEL = "r60kmV2"
R60KM_V2_RADIUS_KM = 60
R60KM_V2_PROB_COL = "ML_r60kmV2_Prob"
R60KM_V2_EXPERIMENT_TAG = "v33_r60kmV2_expanded40uniontarget_r60features_apcp13p7cv_domain"
R60KM_V2_MANIFEST_NAME = "active_artifacts_v33_r60kmV2_expanded40uniontarget_r60features_radiusstats_logloss_apcp13p7cv_domain.json"
DEFAULT_EXTENT = [-105.0, -80.5, 30.0, 50.0]
EARTH_RADIUS_KM = 6371.0
PREDICT_CHUNK_SIZE = 250_000

# HRRR simulated-IR trigger defaults are defined in the HRRR detector section below.

FORECAST_COL = "ML_Forecast_Prob"
WPC_COL = "WPC_ERO_Risk"

# ERO categories/colors requested for operational output.
RISK_THRESHOLDS = [(0.05, ">5%"), (0.15, ">15%"), (0.40, ">40%"), (0.70, ">70%")]
RISK_LABELS = ["<5%", ">5%", ">15%", ">40%", ">70%"]
RISK_BOUNDS = [0.00, 0.05, 0.15, 0.40, 0.70, 1.01]
RISK_COLORS = {
    "<5%": "#FFFFFF",
    ">5%": "#5ED135",
    ">15%": "#F1EE36",
    ">40%": "#D34737",
    ">70%": "#DD4DDD",
}

MCS_BT_THRESHOLD_K_DEFAULT = 241.0
MCS_MIN_AREA_KM2_DEFAULT = 6.0e4
MCS_CLOUD_DURATION_HOURS_DEFAULT = 3
MCS_STRUCTURAL_DURATION_HOURS_DEFAULT = 4
RAP_STRUCTURAL_DURATION_HOURS_DEFAULT = 2
MCS_TRACK_OVERLAP_THRESHOLD_DEFAULT = 0.5
MCS_PRECIPITATION_THRESHOLD_DBZ_DEFAULT = 25.0
MCS_PRECIPITATION_MAJOR_AXIS_KM_DEFAULT = 100.0
MCS_CONVECTIVE_THRESHOLD_DBZ_DEFAULT = 45.0

UFVS_BASE_URL = "https://ftp-wpc.ncep.noaa.gov/erickson/FFaIR/UFVS"
UFVS_PREFIX_TO_COL = {
    "ST4gFFG": "UFVS_STAGE4_FFG",
    "ST4gARI": "UFVS_STAGE4_ARI",
    "USGS": "UFVS_USGS",
    "LSRFLASH": "UFVS_LSR_FLASH",
    "LSRREG": "UFVS_LSR_REGULAR",
}
REALTIME_PP_SOURCE_PREFIXES = ["ST4gFFG", "ST4gARI", "USGS", "LSRFLASH"]
WPC_IEM_OUTLOOK_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/outlooks.py"

SCRIPT_VERBOSE = False


# ======================================================================================
# Small utilities
# ======================================================================================


def log(msg: str, *, verbose_only: bool = False) -> None:
    """Timestamped automation log.

    Quiet mode is the default because this script is intended for cron/systemd.
    Use --verbose to show per-file/per-forecast-hour diagnostics.
    """
    if verbose_only and not SCRIPT_VERBOSE:
        return
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}", flush=True)


def vlog(msg: str) -> None:
    log(msg, verbose_only=True)


def date8(value) -> str:
    m = re.search(r"(20\d{6})", str(value))
    if not m:
        raise ValueError(f"Could not parse YYYYMMDD date from {value!r}")
    return m.group(1)


def valid_period_12z(date: str) -> tuple[datetime, datetime, str]:
    """Return the operational product valid window for a case date.

    The public ML/WPC product should be described as valid from 12Z on the
    case date to 12Z on the following day. The HRRR cycle used for the trigger
    is only an internal trigger-detail field and should not be presented as the
    ML product cycle.
    """
    d = date8(date)
    start = datetime.strptime(d + "12", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    label = f"{start:%Y-%m-%d} 12Z to {end:%Y-%m-%d} 12Z"
    return start, end, label


def extent_dict(extent=None) -> dict[str, float]:
    if extent is None:
        extent = DEFAULT_EXTENT
    if isinstance(extent, dict):
        return {k: float(v) for k, v in extent.items()}
    if len(extent) != 4:
        raise ValueError("extent must be [lon_min, lon_max, lat_min, lat_max]")
    return {"lon_min": float(extent[0]), "lon_max": float(extent[1]), "lat_min": float(extent[2]), "lat_max": float(extent[3])}


def normalize_lon(lon):
    lon = np.asarray(lon, dtype=float)
    return np.where(lon > 180.0, lon - 360.0, lon)


def collapse_duplicate_local_run_path(s: str) -> str:
    """Collapse the accidental ISU_Research_LOCAL_RUN_LOCAL_RUN path variant."""
    # This exact duplicate was produced when /home/.../ISU_Research was replaced
    # inside /home/.../ISU_Research_LOCAL_RUN. Keep this as a defensive cleanup.
    prev = None
    while prev != s:
        prev = s
        s = s.replace("ISU_Research_LOCAL_RUN_LOCAL_RUN", "ISU_Research_LOCAL_RUN")
        s = s.replace("ISU_RESEARCH_LOCAL_RUN_LOCAL_RUN", "ISU_RESEARCH_LOCAL_RUN")
    return s


def replace_root_prefix_only(s: str, original_root: str | Path, local_root: str | Path) -> str:
    """Replace the original root only when it is a real path prefix.

    Critical detail: /home/.../ISU_Research is a string prefix of
    /home/.../ISU_Research_LOCAL_RUN, but it is NOT a path prefix. The old
    script used plain str.replace and produced ISU_Research_LOCAL_RUN_LOCAL_RUN.
    """
    s = collapse_duplicate_local_run_path(str(s))
    o = os.path.normpath(str(original_root))
    l = os.path.normpath(str(local_root))
    sn = os.path.normpath(s) if not re.search(r"[*?[]", s) else s

    # Already local; do not patch again.
    if sn == l or sn.startswith(l + os.sep):
        return s

    # Only replace if original_root is a path component boundary prefix.
    if sn == o:
        return l
    if sn.startswith(o + os.sep):
        return l + sn[len(o):]
    return s


def path_replace_root(p: str | Path | None, original_root: str | Path, local_root: str | Path) -> str | None:
    if p is None:
        return None
    s = collapse_duplicate_local_run_path(str(p))
    if os.path.exists(s):
        return s
    alt = replace_root_prefix_only(s, original_root, local_root)
    if alt != s and os.path.exists(alt):
        return alt
    return alt


def parquet_columns(path: str | Path) -> list[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise RuntimeError("pyarrow is required to inspect/read parquet files.") from exc
    return list(pq.ParquetFile(path).schema.names)


def read_json(path: str | Path):
    with open(path, "r") as f:
        return json.load(f)


def load_feature_names(path: str | Path) -> list[str]:
    obj = read_json(path)
    if isinstance(obj, list):
        return [str(x) for x in obj]
    if isinstance(obj, dict):
        for key in ["feature_names", "features", "columns"]:
            if key in obj and isinstance(obj[key], list):
                return [str(x) for x in obj[key]]
    raise RuntimeError(f"Could not parse feature names JSON: {path}")


def keyed_for_merge(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["Date"] = out["Date"].astype(str).str.slice(0, 8)
    out["Lat"] = pd.to_numeric(out["Lat"], errors="coerce")
    out["Lon"] = pd.to_numeric(out["Lon"], errors="coerce")
    out["__LatKey"] = out["Lat"].round(5)
    out["__LonKey"] = out["Lon"].round(5)
    return out


def merge_grid_by_date_latlon(left: pd.DataFrame, right: pd.DataFrame, columns_to_add=None) -> pd.DataFrame:
    if right is None or len(right) == 0:
        return left.copy()
    a = keyed_for_merge(left)
    b = keyed_for_merge(right)
    if columns_to_add is None:
        columns_to_add = [c for c in b.columns if c not in ["Date", "Lat", "Lon", "__LatKey", "__LonKey"] and c not in a.columns]
    keep = ["Date", "__LatKey", "__LonKey"] + [c for c in columns_to_add if c in b.columns]
    b = b[keep].drop_duplicates(subset=["Date", "__LatKey", "__LonKey"])
    out = a.merge(b, on=["Date", "__LatKey", "__LonKey"], how="left")
    return out.drop(columns=["__LatKey", "__LonKey"], errors="ignore")


def latlon_to_unit_xyz(lat, lon) -> np.ndarray:
    lat_rad = np.deg2rad(np.asarray(lat, dtype=float))
    lon_rad = np.deg2rad(np.asarray(lon, dtype=float))
    cos_lat = np.cos(lat_rad)
    return np.column_stack([cos_lat * np.cos(lon_rad), cos_lat * np.sin(lon_rad), np.sin(lat_rad)]).astype(np.float64)


def km_to_unit_sphere_chord_radius(radius_km: float) -> float:
    angular_radius = float(radius_km) / EARTH_RADIUS_KM
    return 2.0 * np.sin(angular_radius / 2.0)


def expand_binary_mask_radius_km(mask, tree, xyz, radius_km=40.0, chunk_size=1024) -> np.ndarray:
    mask = np.asarray(mask, dtype=bool)
    if mask.size == 0:
        return mask.copy()
    if not mask.any():
        return np.zeros_like(mask, dtype=bool)
    if mask.all():
        return np.ones_like(mask, dtype=bool)
    yes_idx = np.flatnonzero(mask)
    out = np.zeros_like(mask, dtype=bool)
    chord_radius = km_to_unit_sphere_chord_radius(radius_km)
    for start in range(0, len(yes_idx), int(chunk_size)):
        idx_chunk = yes_idx[start:start + int(chunk_size)]
        neighbors = tree.query_ball_point(xyz[idx_chunk], r=chord_radius, return_sorted=False)
        for n in neighbors:
            if len(n):
                out[np.asarray(n, dtype=np.int64)] = True
    return out


# ======================================================================================
# Project/artifact discovery
# ======================================================================================


def project_dir_score(p: str | Path) -> int:
    p = os.path.abspath(os.path.expanduser(str(p)))
    score = 0
    if os.path.isdir(p):
        score += 10
    if os.path.exists(os.path.join(p, "df_pp_viewer_with_wpc_ero_day1.parquet")):
        score += 10
    if os.path.isdir(os.path.join(p, "prob_flood_models")):
        score += 10
    pred_dir = os.path.join(p, "v33_singletarget_radius_sensitivity_viewer_prediction_cache")
    if os.path.isdir(pred_dir):
        score += 5 + min(20, len(glob.glob(os.path.join(pred_dir, "*.parquet"))) * 5)
    if glob.glob(os.path.join(p, "pixel_domain_forecasts_rap09z_iem_mrms_ffg_*v33*.parquet")):
        score += 15
    return score


def choose_project_dir(cli_project_dir: str | None) -> Path:
    if cli_project_dir:
        p = Path(os.path.expanduser(cli_project_dir)).resolve()
        if not p.exists():
            raise FileNotFoundError(f"--project-dir does not exist: {p}")
        log(f"Using explicit PROJECT_DIR={p}")
        return p
    scored = [(Path(p), project_dir_score(p)) for p in DEFAULT_PROJECT_DIRS]
    def tb(item):
        p, s = item
        local_bonus = 1 if "ISU_Research_LOCAL_RUN" in str(p) else 0
        badcase_penalty = -1 if "ISU_RESEARCH_LOCAL_RUN" in str(p) else 0
        return (s, local_bonus, badcase_penalty)
    best, best_score = max(scored, key=tb)
    log("Candidate PROJECT_DIR scores: " + "; ".join([f"{s}:{p}" for p, s in scored]))
    log(f"Using PROJECT_DIR={best} (score={best_score})")
    return best


def experiment_tag_for_radius(radius_km: int | float) -> str:
    r = int(round(float(radius_km)))
    return f"{RUN_VERSION_TAG}_r{r}km_singletarget_radiusstats_mse_apcp13p7cv_domain"


def target_output_tag_for_radius(radius_km: int | float) -> str:
    r = int(round(float(radius_km)))
    return f"r{r}km_singletarget_radiusstats_target_{RUN_VERSION_TAG}_apcp13p7cv_domain"


def manifest_candidates_for_radius(project_dir: Path, radius_km: int | float) -> list[str]:
    r = int(round(float(radius_km)))
    exp = experiment_tag_for_radius(r)
    model_cache_dir = project_dir / "prob_flood_models"
    patterns = [
        model_cache_dir / f"active_artifacts_{exp}.json",
        project_dir / f"prob_flood_models_{exp}" / f"active_artifacts_{exp}.json",
    ]
    hits = []
    for p in patterns:
        hits.extend(glob.glob(str(p)))
    return [p for p in hits if os.path.exists(p)]


def master_candidates_for_radius(project_dir: Path, radius_km: int | float) -> list[str]:
    r = int(round(float(radius_km)))
    exp = experiment_tag_for_radius(r)
    target_out = target_output_tag_for_radius(r)
    candidates = [
        project_dir / f"pixel_domain_forecasts_rap09z_iem_mrms_ffg_{target_out}.parquet",
        project_dir / f"pixel_domain_forecasts_rap09z_iem_mrms_ffg_{exp}.parquet",
    ]
    patterns = [
        project_dir / f"**/pixel_domain_forecasts_rap09z_iem_mrms_ffg_{target_out}.parquet",
        project_dir / f"**/pixel_domain_forecasts_*{target_out}*.parquet",
        project_dir / f"**/pixel_domain_forecasts_*{exp}*.parquet",
        project_dir / f"**/pixel_domain_forecasts_*r{r}km*singletarget*target_{RUN_VERSION_TAG}*.parquet",
    ]
    for pat in patterns:
        candidates.extend(glob.glob(str(pat), recursive=True))
    seen, existing = set(), []
    for p in candidates:
        p = os.path.abspath(str(p))
        if p in seen:
            continue
        seen.add(p)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            existing.append(p)
    return sorted(existing, key=os.path.getmtime, reverse=True)


def find_artifacts_for_radius(project_dir: Path, radius_km: int | float, original_root: Path, local_root: Path) -> dict:
    r = int(round(float(radius_km)))
    exp = experiment_tag_for_radius(r)
    target_tag = f"r{r}km"
    target_out = target_output_tag_for_radius(r)
    model_cache_dir = project_dir / "prob_flood_models"

    manifest_path = None
    manifest = {}
    cands = manifest_candidates_for_radius(project_dir, r)
    if cands:
        manifest_path = sorted(cands, key=os.path.getmtime)[-1]
        manifest = read_json(manifest_path)

    master_hits = master_candidates_for_radius(project_dir, r)
    master_path = master_hits[0] if master_hits else str(project_dir / f"pixel_domain_forecasts_rap09z_iem_mrms_ffg_{target_out}.parquet")

    model_path = manifest.get("current_model_alias") or manifest.get("model_path")
    scaler_path = manifest.get("current_scaler_alias") or manifest.get("scaler_path")
    features_path = manifest.get("current_feature_names_alias") or manifest.get("feature_names_path")

    model_path = path_replace_root(model_path, original_root, local_root)
    scaler_path = path_replace_root(scaler_path, original_root, local_root)
    features_path = path_replace_root(features_path, original_root, local_root)

    model_roots = [
        model_cache_dir,
        project_dir / f"prob_flood_models_{exp}",
    ]

    if not model_path or not os.path.exists(model_path):
        hits = []
        for root in model_roots:
            hits += glob.glob(str(root / f"current_{RUN_VERSION_TAG}_{target_tag}_XGBoost_model.pkl"))
            hits += glob.glob(str(root / f"rawprob_localoptuna_{exp}_rap09z_iem_mrms_ffg_XGBoost.pkl"))
            hits += glob.glob(str(root / f"*{exp}*XGBoost*.pkl"))
        hits = [p for p in hits if os.path.exists(p) and os.path.getsize(p) > 0]
        if hits:
            model_path = sorted(hits, key=os.path.getmtime)[-1]

    if not scaler_path or not os.path.exists(scaler_path):
        hits = []
        for root in model_roots:
            hits += glob.glob(str(root / f"current_{RUN_VERSION_TAG}_{target_tag}_scaler.pkl"))
            hits += glob.glob(str(root / f"prob_scaler_localoptuna_{exp}.pkl"))
            hits += glob.glob(str(root / f"*scaler*{exp}*.pkl"))
        hits = [p for p in hits if os.path.exists(p) and os.path.getsize(p) > 0]
        if hits:
            scaler_path = sorted(hits, key=os.path.getmtime)[-1]

    if not features_path or not os.path.exists(features_path):
        hits = []
        for root in model_roots:
            hits += glob.glob(str(root / f"current_{RUN_VERSION_TAG}_{target_tag}_feature_names.json"))
            hits += glob.glob(str(root / f"feature_names_localoptuna_{exp}.json"))
            hits += glob.glob(str(root / f"*feature*{exp}*.json"))
        hits = [p for p in hits if os.path.exists(p) and os.path.getsize(p) > 0]
        if hits:
            features_path = sorted(hits, key=os.path.getmtime)[-1]

    missing = []
    for label, path in [("manifest", manifest_path), ("master parquet", master_path), ("model", model_path), ("scaler", scaler_path), ("feature names", features_path)]:
        if not path or not os.path.exists(path):
            missing.append(label)
    if missing:
        raise RuntimeError(
            f"Missing artifacts for radius {r} km: {missing}\n"
            f"Expected model experiment tag: {exp}\n"
            f"Expected master target-output tag: {target_out}\n"
            f"Checked model roots: {[str(x) for x in model_roots]}"
        )

    return {
        "radius_km": r,
        "experiment_tag": exp,
        "target_output_tag": target_out,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "master_path": str(master_path),
        "model_path": str(model_path),
        "scaler_path": str(scaler_path),
        "features_path": str(features_path),
    }


def find_r60km_v2_artifacts(project_dir: Path, original_root: Path, local_root: Path) -> dict:
    """Load the distinct r60kmV2 artifact set without colliding with regular r60."""
    model_cache_dir = project_dir / "prob_flood_models"
    manifest_path = model_cache_dir / R60KM_V2_MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {R60KM_V2_LABEL} artifact manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    paths = {
        "model_path": manifest.get("current_model_alias") or manifest.get("model_path"),
        "scaler_path": manifest.get("current_scaler_alias") or manifest.get("scaler_path"),
        "features_path": manifest.get("current_feature_names_alias") or manifest.get("feature_names_path"),
    }
    for key, value in list(paths.items()):
        paths[key] = path_replace_root(value, original_root, local_root)
    missing = [key for key, value in paths.items() if not value or not os.path.exists(value)]
    if missing:
        raise RuntimeError(
            f"Missing {R60KM_V2_LABEL} artifacts {missing}; manifest={manifest_path}"
        )
    return {
        "radius_km": R60KM_V2_RADIUS_KM,
        "model_label": R60KM_V2_LABEL,
        "experiment_tag": manifest.get("run_tag", R60KM_V2_EXPERIMENT_TAG),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        **{key: str(value) for key, value in paths.items()},
    }


def model_positive_class_probability(model, X_scaled: np.ndarray) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        p2 = np.asarray(model.predict_proba(X_scaled))
        if p2.ndim == 2 and p2.shape[1] >= 2:
            return p2[:, 1].astype(np.float32)
        if p2.ndim == 1:
            return p2.astype(np.float32)
        raise RuntimeError(f"Unexpected predict_proba output shape: {p2.shape}")
    return np.asarray(model.predict(X_scaled), dtype=np.float32)


# Compatibility class for old joblib/pickle wrappers.
class ClippedRegressionProbabilityWrapper:
    def __init__(self, base_model=None):
        self.base_model = base_model
    def predict(self, X):
        p = self.base_model.predict(X)
        return np.clip(np.asarray(p, dtype=np.float32), 0.0, 1.0)
    def predict_proba(self, X):
        p = self.predict(X)
        return np.column_stack([1.0 - p, p]).astype(np.float32)
    @property
    def feature_importances_(self):
        return getattr(self.base_model, "feature_importances_")
    def get_booster(self):
        return self.base_model.get_booster()


# ======================================================================================
# Realtime feature builder via generated training scripts
# ======================================================================================


def install_dummy_ray_for_import_only() -> None:
    if "ray" in sys.modules:
        return
    class _DummyRemoteFunction:
        def __init__(self, func): self.func = func
        def remote(self, *a, **k): return self.func(*a, **k)
        def options(self, *a, **k): return self
        def __call__(self, *a, **k): return self.func(*a, **k)
    class _DummyRay(types.ModuleType):
        def __init__(self):
            super().__init__("ray")
            self.ObjectRef = object
        def remote(self, *args, **kwargs):
            if args and callable(args[0]) and len(args) == 1 and not kwargs:
                return _DummyRemoteFunction(args[0])
            return lambda f: _DummyRemoteFunction(f)
        def init(self, *a, **k): return None
        def shutdown(self, *a, **k): return None
        def is_initialized(self): return False
        def get(self, obj): return obj
        def put(self, obj): return obj
        def wait(self, refs, num_returns=1, timeout=None): return list(refs)[:num_returns], list(refs)[num_returns:]
        def cancel(self, *a, **k): return None
        def cluster_resources(self): return {"CPU": 1}
        def available_resources(self): return {"CPU": 1}
    sys.modules["ray"] = _DummyRay()


def candidate_training_scripts_for_radius(script_dir: Path, radius_km: int | float) -> list[Path]:
    r = int(round(float(radius_km)))
    patterns = [
        script_dir / "generated_v33_radius_sensitivity_slimmaster_rowsample" / f"*r{r}km*.py",
        script_dir / "generated_v33_radius_sensitivity_slimmaster" / f"*r{r}km*.py",
        script_dir / "generated_v33_radius_sensitivity" / f"*r{r}km*.py",
        script_dir / f"*v33*r{r}km*radiusstats*.py",
    ]
    out = []
    for pat in patterns:
        out.extend([Path(p) for p in glob.glob(str(pat))])
    seen, clean = set(), []
    for p in out:
        rp = p.resolve()
        if rp.exists() and rp not in seen:
            seen.add(rp)
            clean.append(rp)
    # Feature extraction is shared by same-radius model variants. Prefer the
    # original single-target helper so adding r60kmV2 cannot silently change the
    # operational R60 feature builder selected by filesystem/glob order.
    return sorted(
        clean,
        key=lambda p: (
            0 if "singletarget_radiusstats" in p.name and "V2" not in p.name else 1,
            p.name,
        ),
    )


def patch_module_paths_to_local_run(mod, original_root: Path, local_root: Path, nam_dir_override: str | None = None):
    """Patch generated-helper absolute paths without double-patching LOCAL_RUN paths."""
    for name, val in list(vars(mod).items()):
        try:
            if isinstance(val, str):
                fixed = replace_root_prefix_only(val, original_root, local_root)
                if fixed != val:
                    setattr(mod, name, fixed)
            elif isinstance(val, Path):
                fixed = replace_root_prefix_only(str(val), original_root, local_root)
                if fixed != str(val):
                    setattr(mod, name, Path(fixed))
        except Exception:
            pass
    for flag in ["STRICT_PROJECT_MOUNT_CHECK", "STRICT_PROJECT_MOUNT_CHECK_FOR_DATA_WRITES"]:
        if hasattr(mod, flag):
            try:
                setattr(mod, flag, False)
            except Exception:
                pass
    if nam_dir_override is not None:
        # Keep the generated-helper naming for now. The helper may still call this
        # argument nam_dir even though the files are RAP background files.
        setattr(mod, "RAP_DIR", str(Path(nam_dir_override).expanduser()))
    elif hasattr(mod, "RAP_DIR"):
        # Normalize a pre-existing generated-helper RAP_DIR, especially the
        # ISU_Research_LOCAL_RUN_LOCAL_RUN bug.
        setattr(mod, "RAP_DIR", replace_root_prefix_only(str(getattr(mod, "RAP_DIR")), original_root, local_root))
    return mod


_IMPORTED_TRAINING_MODULES: dict[str, types.ModuleType] = {}


@contextlib.contextmanager
def sibling_module_import_path(script: Path):
    """Temporarily expose a generated helper's directory for sibling imports.

    ``spec_from_file_location`` loads the requested file directly, but unlike
    normal ``python path/to/script.py`` execution it does not add that file's
    parent directory to ``sys.path``.  Generated helpers can therefore fail on
    imports such as ``from mode_case_catalog import ...`` even when the sibling
    module is present.  Keep the path scoped to module execution so unrelated
    imports in the long-running realtime process are unaffected.
    """
    parent = str(Path(script).resolve().parent)
    inserted = parent not in sys.path
    if inserted:
        sys.path.insert(0, parent)
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass


def load_training_module_for_realtime(radius_km: int | float, script_dir: Path, explicit_script: str | None, original_root: Path, local_root: Path, nam_dir_override: str | None = None):
    r = int(round(float(radius_km)))
    if explicit_script:
        script = Path(explicit_script).expanduser().resolve()
        if not script.exists():
            raise FileNotFoundError(script)
    else:
        cands = candidate_training_scripts_for_radius(script_dir, r)
        if not cands:
            raise FileNotFoundError(
                f"Could not find generated v33 training/realtime helper for r{r}km under {script_dir}. "
                "Pass --training-script-by-radius r:path or --script-dir."
            )
        script = cands[0]
    key = f"{script.resolve()}|nam={nam_dir_override or ''}"
    if key in _IMPORTED_TRAINING_MODULES:
        return _IMPORTED_TRAINING_MODULES[key]
    install_dummy_ray_for_import_only()
    mod_name = f"hml_realtime_r{r}_{abs(hash(key))}"
    spec = importlib.util.spec_from_file_location(mod_name, str(script))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import training helper: {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        with sibling_module_import_path(script):
            spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(mod_name, None)
        raise
    mod = patch_module_paths_to_local_run(mod, original_root=original_root, local_root=local_root, nam_dir_override=nam_dir_override)
    _IMPORTED_TRAINING_MODULES[key] = mod
    log(f"Loaded realtime feature helper r{r}km: {script}", verbose_only=True)
    if hasattr(mod, "RAP_DIR"):
        log(f"  helper RAP_DIR={getattr(mod, 'RAP_DIR')}", verbose_only=True)
    return mod


def dummy_hydro_target_dict(mod, n: int, radius_km: int | float) -> dict[str, np.ndarray]:
    r = int(round(float(radius_km)))
    out = {}
    for col in [
        getattr(mod, "TARGET_COLUMN", "Obs_MRMS_FFG_Exceeded_Point"),
        "Obs_FFG_NumDurationsExceeded",
        getattr(mod, "TRAIN_TARGET_COLUMN", f"Target_MRMS_FFG_Exceeded_R{r}km"),
        f"Target_MRMS_FFG_Exceeded_R{r}km",
        f"Obs_MRMS_FFG_R{r}km_NeighborCount",
        f"Obs_MRMS_FFG_Exceeded_R{r}km_EventCount",
        f"Obs_MRMS_FFG_Exceeded_R{r}km_Fraction",
    ]:
        if "Fraction" in col:
            out[col] = np.zeros(n, dtype=np.float32)
        elif "Count" in col or "NumDurations" in col:
            out[col] = np.zeros(n, dtype=np.int16)
        else:
            out[col] = np.zeros(n, dtype=np.int8)
    return out


@dataclass
class RuntimePaths:
    project_dir: Path
    script_dir: Path
    cache_dir: Path
    feature_cache_dir: Path
    prediction_cache_dir: Path
    verified_cache_dir: Path
    ufvs_cache_dir: Path
    wpc_cache_dir: Path
    pp_cache_dir: Path
    outdir: Path
    original_root: Path
    local_root: Path


def make_runtime_paths(args) -> RuntimePaths:
    project_dir = choose_project_dir(args.project_dir)
    script_dir = Path(args.script_dir).expanduser().resolve() if args.script_dir else (project_dir.parent / "mesoanalysis" / "gempak-scripts")
    if not script_dir.exists():
        fallback = Path("/home/tyreekfrazier/ISU_Research_LOCAL_RUN/mesoanalysis/gempak-scripts")
        if fallback.exists():
            script_dir = fallback
    cache_dir = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else (project_dir / "v33_realtime_radiusstats_forecasts")
    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else (cache_dir / "mcs_triggered_figures")
    rp = RuntimePaths(
        project_dir=project_dir,
        script_dir=script_dir,
        cache_dir=cache_dir,
        feature_cache_dir=cache_dir / "features",
        prediction_cache_dir=cache_dir / "predictions",
        verified_cache_dir=cache_dir / "verified",
        ufvs_cache_dir=cache_dir / "ufvs_raw",
        wpc_cache_dir=project_dir / "realtime_wpc_ero_cache_v33",
        pp_cache_dir=project_dir / "realtime_pp_from_ufvs_cache_v33",
        outdir=outdir,
        original_root=Path(args.original_root).expanduser(),
        local_root=Path(args.local_root).expanduser(),
    )
    for p in [rp.cache_dir, rp.feature_cache_dir, rp.prediction_cache_dir, rp.verified_cache_dir, rp.ufvs_cache_dir, rp.wpc_cache_dir, rp.pp_cache_dir, rp.outdir]:
        p.mkdir(parents=True, exist_ok=True)
    return rp


def cycle_cache_token(cycle_label: str | None) -> str:
    if not cycle_label:
        return "defaultcycle"
    return re.sub(r"[^A-Za-z0-9]+", "", str(cycle_label)).lower() or "defaultcycle"


def realtime_feature_cache_path(rp: RuntimePaths, date: str, radius_km: int | float, cycle_label: str | None = None) -> Path:
    """Notebook-compatible realtime feature cache path.

    Important: the working notebook cells use filenames without a cycle token:
      realtime_features_v33_r40km_YYYYMMDD.parquet
    The HRRR cycle controls the MCS trigger, not the trained RAP-background feature table.
    Adding hrrr12z/rap09z to this cache key made the script miss existing working caches
    and forced unnecessary feature rebuilds. Keep this path legacy-compatible.
    """
    d = date8(date)
    r = int(round(float(radius_km)))
    return rp.feature_cache_dir / f"realtime_features_v33_r{r}km_{d}.parquet"


def realtime_feature_cache_candidates(rp: RuntimePaths, date: str, radius_km: int | float, cycle_label: str | None = None) -> list[Path]:
    """Return cache paths in the order they should be trusted.

    Prefer the notebook/legacy cache. Then accept older script cycle-tagged products if
    they exist, so previous runs are not wasted. Save new products to the legacy path.
    """
    d = date8(date)
    r = int(round(float(radius_km)))
    legacy = rp.feature_cache_dir / f"realtime_features_v33_r{r}km_{d}.parquet"
    cyc = cycle_cache_token(cycle_label)
    tagged = rp.feature_cache_dir / f"realtime_features_v33_r{r}km_{cyc}_{d}.parquet"
    return [legacy] + ([] if tagged == legacy else [tagged])


def realtime_prediction_cache_path(rp: RuntimePaths, date: str, radius_km: int | float, cycle_label: str | None = None) -> Path:
    """Notebook-compatible realtime prediction cache path."""
    d = date8(date)
    r = int(round(float(radius_km)))
    return rp.prediction_cache_dir / f"realtime_predictions_v33_r{r}km_{d}.parquet"


def realtime_prediction_cache_candidates(rp: RuntimePaths, date: str, radius_km: int | float, cycle_label: str | None = None) -> list[Path]:
    d = date8(date)
    r = int(round(float(radius_km)))
    legacy = rp.prediction_cache_dir / f"realtime_predictions_v33_r{r}km_{d}.parquet"
    cyc = cycle_cache_token(cycle_label)
    tagged = rp.prediction_cache_dir / f"realtime_predictions_v33_r{r}km_{cyc}_{d}.parquet"
    return [legacy] + ([] if tagged == legacy else [tagged])


def realtime_labeled_prediction_cache_path(rp: RuntimePaths, date: str, model_label: str) -> Path:
    d = date8(date)
    label = re.sub(r"[^A-Za-z0-9]+", "", str(model_label))
    return rp.prediction_cache_dir / f"realtime_predictions_v33_{label}_{d}.parquet"


def realtime_verified_cache_path(rp: RuntimePaths, date: str, radius_km: int | float, cycle_label: str | None = None) -> Path:
    d = date8(date)
    r = int(round(float(radius_km)))
    return rp.verified_cache_dir / f"realtime_verified_v33_r{r}km_{d}.parquet"


def multi_radius_cache_path(rp: RuntimePaths, date: str, available_radii: Iterable[int], cycle_label: str | None = None) -> Path:
    d = date8(date)
    rr = "_".join(f"r{int(r)}" for r in available_radii) if available_radii else "none"
    return rp.verified_cache_dir / f"realtime_verified_v33_multiradius_{rr}_{d}.parquet"




def safe_validate_grib_file_no_pygrib(path, *args, **kwargs):
    """Validate GRIB framing and isolate pygrib/ecCodes native failures.

    The generated helper's original validator appears to be used as a predicate in
    at least one path: missing/bad files should return False so the helper can
    download/retry. Raising FileNotFoundError made the standalone script skip every
    radius before the helper had a chance to fetch RAP. A real, readable GRIB returns
    True; missing/tiny/non-GRIB returns False.
    """
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return False
        size = p.stat().st_size
        if size < 1000:
            return False
        with p.open("rb") as f:
            if f.read(4) != b"GRIB":
                return False

        # If wgrib2 exists, use it as a non-Python validation path. Do not require it.
        wgrib2 = shutil.which("wgrib2")
        if wgrib2:
            proc = subprocess.run(
                [wgrib2, str(p), "-match", ":", "-count"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
            )
            if proc.returncode != 0:
                return False

        # pygrib/ecCodes can segfault instead of raising on a transient/incomplete
        # file. Open the first message in a disposable interpreter so a native
        # crash becomes a failed validation rather than terminating automation.
        probe = (
            "import pygrib,sys; "
            "g=pygrib.open(sys.argv[1]); "
            "m=g.message(1); "
            "_=m.values.shape; "
            "g.close()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe, str(p)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return proc.returncode == 0
    except Exception:
        return False


def isolated_grib_domain_mask(sample_grib_file, lat_min, lat_max, lon_min, lon_max):
    """Read a GRIB grid in a disposable process and return training-domain arrays."""
    import tempfile

    fd, npz_path = tempfile.mkstemp(prefix="realtime_rap_domain_", suffix=".npz", dir="/tmp")
    os.close(fd)
    probe = """
import numpy as np
import pygrib
import sys
g = pygrib.open(sys.argv[1])
lats, lons = g.message(1).latlons()
g.close()
np.savez_compressed(sys.argv[2], lats=lats, lons=lons)
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe, str(sample_grib_file), npz_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Isolated pygrib domain read failed rc={proc.returncode}: {proc.stderr[-1000:]}"
            )
        with np.load(npz_path) as data:
            lats = np.asarray(data["lats"])
            lons = np.asarray(data["lons"])
        lons = np.where(lons > 180.0, lons - 360.0, lons)
        mask = (
            (lats >= float(lat_min))
            & (lats <= float(lat_max))
            & (lons >= float(lon_min))
            & (lons <= float(lon_max))
        )
        return lats, lons, mask, lats[mask], lons[mask]
    finally:
        try:
            os.remove(npz_path)
        except OSError:
            pass


@contextlib.contextmanager
def helper_output_context(rp: RuntimePaths, date: str, radius_km: int, cycle_label: str | None):
    """Redirect noisy generated-helper prints to a log file unless --verbose is used."""
    if SCRIPT_VERBOSE:
        yield None
        return
    log_dir = rp.cache_dir / "helper_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    helper_log = log_dir / f"helper_r{int(radius_km)}km_{cycle_cache_token(cycle_label)}_{date8(date)}.log"
    with helper_log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write("\n" + "=" * 80 + "\n")
        fh.write(f"Started {datetime.now(timezone.utc).isoformat()} date={date8(date)} r{int(radius_km)} cycle={cycle_cache_token(cycle_label)}\n")
        fh.flush()
        with contextlib.redirect_stdout(fh), contextlib.redirect_stderr(fh):
            yield helper_log

def resolve_realtime_feature_input_dir(h, rp: RuntimePaths, nam_dir_override: str | None = None) -> str:
    """Resolve the generated helper's RAP/NAM input directory robustly.

    The v33 helper still calls this RAP_DIR/nam_dir because the trained predictors
    were built from RAP-background fields. This is separate from the HRRR simulated-IR
    MCS trigger used to decide whether to run the plotter.
    """
    candidates: list[Path] = []
    if nam_dir_override:
        candidates.append(Path(nam_dir_override).expanduser())

    helper_raw = getattr(h, "RAP_DIR", None)
    if helper_raw is not None:
        fixed = path_replace_root(helper_raw, rp.original_root, rp.local_root)
        if fixed is not None:
            candidates.append(Path(fixed).expanduser())

    # Common local locations. The first one matches this project layout.
    candidates.extend([
        rp.local_root / "RAP_BACKGROUND",
        rp.project_dir.parent / "RAP_BACKGROUND",
        rp.original_root / "RAP_BACKGROUND",
        rp.project_dir / "RAP_BACKGROUND",
    ])

    # De-duplicate while preserving order.
    seen = set()
    clean: list[Path] = []
    for c in candidates:
        c = Path(collapse_duplicate_local_run_path(str(c))).expanduser()
        key = str(c)
        if key not in seen:
            seen.add(key)
            clean.append(c)

    # Prefer an existing directory. If none exists, create the local-root fallback so
    # helper download/ensure functions have a sane target.
    for c in clean:
        if c.exists() and c.is_dir():
            setattr(h, "RAP_DIR", str(c))
            log(f"Using realtime feature input directory: {c}")
            return str(c)

    fallback = rp.local_root / "RAP_BACKGROUND"
    fallback.mkdir(parents=True, exist_ok=True)
    setattr(h, "RAP_DIR", str(fallback))
    log(f"Created realtime feature input directory: {fallback}")
    return str(fallback)


def build_realtime_features(
    date: str,
    radius_km: int | float,
    rp: RuntimePaths,
    force_features: bool = False,
    training_script: str | None = None,
    nam_dir_override: str | None = None,
    cycle_label: str | None = None,
) -> pd.DataFrame:
    d = date8(date)
    r = int(round(float(radius_km)))
    path = realtime_feature_cache_path(rp, d, r, cycle_label)
    if not force_features:
        for cand in realtime_feature_cache_candidates(rp, d, r, cycle_label):
            if cand.exists() and cand.stat().st_size > 1024:
                log(f"Using existing realtime feature cache: {cand}")
                return pd.read_parquet(cand)

    h = load_training_module_for_realtime(
        radius_km=r,
        script_dir=rp.script_dir,
        explicit_script=training_script,
        original_root=rp.original_root,
        local_root=rp.local_root,
        nam_dir_override=nam_dir_override,
    )

    original = {}
    def patch(name, replacement):
        if hasattr(h, name):
            original[name] = getattr(h, name)
            setattr(h, name, replacement)
    def dummy_hydro(date_str, lats_1d, lons_1d, *args, **kwargs):
        return dummy_hydro_target_dict(h, len(lats_1d), r)
    def dummy_lsr(date_str, lats_1d, lons_1d, *args, **kwargs):
        target = np.zeros(len(lats_1d), dtype=np.int8)
        if kwargs.get("return_report_count"):
            return target, np.zeros(len(lats_1d), dtype=np.int16)
        return target

    patch("fetch_mrms_ffg_exceedance_target", dummy_hydro)
    patch("fetch_iem_flash_flood_reports_pixel", dummy_lsr)
    patch("fetch_iem_flood_reports_pixel", dummy_lsr)
    # Avoid a known pygrib/eccodes segfault in the generated helper validation path.
    patch("validate_nam_forecast_file", safe_validate_grib_file_no_pygrib)
    patch("validate_forecast_file", safe_validate_grib_file_no_pygrib)
    patch(
        "get_nam_domain_mask",
        lambda sample: isolated_grib_domain_mask(
            sample,
            getattr(h, "TRAIN_DOMAIN_LAT_MIN"),
            getattr(h, "TRAIN_DOMAIN_LAT_MAX"),
            getattr(h, "TRAIN_DOMAIN_LON_MIN"),
            getattr(h, "TRAIN_DOMAIN_LON_MAX"),
        ),
    )

    try:
        log(f"Building realtime features for {d} r{r}km")
        nam_dir = resolve_realtime_feature_input_dir(h, rp, nam_dir_override=nam_dir_override)
        log(f"Feature helper source for model predictors is RAP background fields: nam_dir/RAP_DIR={nam_dir}", verbose_only=True)
        with helper_output_context(rp, d, r, cycle_label) as helper_log:
            if helper_log is not None:
                log(f"Generated helper output for r{r}km is being written to: {helper_log}")
            if hasattr(h, "_prepare_domain_vars_for_extraction"):
                domain_vars = h._prepare_domain_vars_for_extraction([d])
            else:
                if not hasattr(h, "ensure_nam_forecast_file") or not hasattr(h, "get_nam_domain_mask"):
                    raise RuntimeError("Training helper lacks _prepare_domain_vars_for_extraction and ensure_nam_forecast_file/get_nam_domain_mask.")
                sample = h.ensure_nam_forecast_file(d, 0, nam_dir)
                domain_vars = h.get_nam_domain_mask(sample)
            if nam_dir is None:
                raise RuntimeError("Training helper lacks RAP_DIR and no --nam-dir was supplied.")
            df = h.process_daily_pixel_data(date_str=d, nam_dir=nam_dir, domain_vars=domain_vars, is_test_set=True)
        if df is None or len(df) == 0:
            raise RuntimeError(f"Realtime feature builder returned no rows for {d} r{r}km.")
        df = df.copy()
        df["Date"] = d
        if "Year" not in df.columns:
            df["Year"] = d[:4]
        df["Realtime_Cycle_Label"] = cycle_label or ""
    finally:
        for name, fn in original.items():
            setattr(h, name, fn)

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log(f"Saved realtime features: {path} rows={len(df):,} cols={len(df.columns):,}")
    return df


def strict_realtime_model_matrix(
    df: pd.DataFrame,
    feature_names: list[str],
    context: str,
    diagnostic_dir: Path,
    allow_nan_fill_zero: bool = False,
) -> np.ndarray:
    missing = [c for c in feature_names if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"{context} is missing {len(missing)} trained feature columns.\n"
            f"First 80 missing: {missing[:80]}\n"
            "No missing-feature padding is allowed. Rebuild realtime features with the same v33 helper used for the model."
        )

    Xdf = df[feature_names].copy()
    Xdf = Xdf.replace([np.inf, -np.inf], np.nan)
    nan_counts = Xdf.isna().sum()
    bad = nan_counts[nan_counts > 0].sort_values(ascending=False)
    if len(bad):
        diag = pd.DataFrame({"feature": bad.index, "nan_or_inf_count": bad.values})
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        diag_path = diagnostic_dir / f"bad_realtime_features_{re.sub(r'[^A-Za-z0-9]+','_',context)}.csv"
        diag.to_csv(diag_path, index=False)
        if not allow_nan_fill_zero:
            raise RuntimeError(
                f"{context} has NaN/inf in {len(bad)} trained feature columns.\n"
                f"Diagnostic written to: {diag_path}\n"
                f"Top bad columns: {diag.head(20).to_dict('records')}\n"
                "Aborting rather than silently filling model predictors."
            )
        log(f"WARNING: {context} has NaN/inf trained predictors; filling with 0.0 because --allow-feature-nan-fill-zero was set. Diagnostic: {diag_path}")
        Xdf = Xdf.fillna(0.0)

    return Xdf.to_numpy(dtype=np.float32, copy=True)


def predict_realtime_case(
    date: str,
    radius_km: int | float,
    rp: RuntimePaths,
    force_predict: bool = False,
    force_features: bool = False,
    training_script: str | None = None,
    nam_dir_override: str | None = None,
    cycle_label: str | None = None,
    allow_feature_nan_fill_zero: bool = False,
) -> pd.DataFrame:
    d = date8(date)
    r = int(round(float(radius_km)))
    out_path = realtime_prediction_cache_path(rp, d, r, cycle_label)
    if not force_predict:
        for cand in realtime_prediction_cache_candidates(rp, d, r, cycle_label):
            if cand.exists() and cand.stat().st_size > 1024:
                log(f"Using existing realtime prediction cache: {cand}")
                return pd.read_parquet(cand)

    df = build_realtime_features(
        d,
        r,
        rp=rp,
        force_features=force_features,
        training_script=training_script,
        nam_dir_override=nam_dir_override,
        cycle_label=cycle_label,
    )
    art = find_artifacts_for_radius(rp.project_dir, r, original_root=rp.original_root, local_root=rp.local_root)
    feature_names = load_feature_names(art["features_path"])
    X_raw = strict_realtime_model_matrix(
        df,
        feature_names,
        context=f"realtime_{d}_r{r}km_{cycle_cache_token(cycle_label)}",
        diagnostic_dir=rp.cache_dir / "diagnostics",
        allow_nan_fill_zero=allow_feature_nan_fill_zero,
    )
    scaler = joblib.load(art["scaler_path"])
    model = joblib.load(art["model_path"])
    X_scaled = scaler.transform(X_raw).astype(np.float32, copy=False)
    p = np.clip(model_positive_class_probability(model, X_scaled), 0.0, 1.0).astype(np.float32)

    keep = [c for c in ["Date", "Year", "Lat", "Lon"] if c in df.columns]
    for extra in ["APCP_RunTotal_0_24h", "Forecast_APCP_24h_Total_to_Guidance_FFG_24h_Ratio", "Realtime_Cycle_Label"]:
        if extra in df.columns and extra not in keep:
            keep.append(extra)
    out = df[keep].copy()
    out["Date"] = d
    out["Year"] = d[:4]
    out["ML_Target_Radius_km"] = int(r)
    out["ML_Forecast_Prob"] = p
    out["ML_Experiment_Tag"] = art.get("experiment_tag", experiment_tag_for_radius(r))
    out["ML_Model_Path"] = art["model_path"]
    out["ML_Feature_Names_Path"] = art["features_path"]
    out["Prediction_Created_UTC"] = datetime.now(timezone.utc).isoformat()
    out["Realtime_Cycle_Label"] = cycle_label or ""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    log(
        f"Saved realtime predictions: {out_path} rows={len(out):,} "
        f"mean={float(np.nanmean(p)):.6f} p95={float(np.nanpercentile(p,95)):.6f} "
        f"p99={float(np.nanpercentile(p,99)):.6f} max={float(np.nanmax(p)):.6f}"
    )
    for thr, lab in RISK_THRESHOLDS:
        log(f"  frac {lab:>4s} = {float(np.nanmean(p >= thr)):.6f}")
    return out


def predict_realtime_r60km_v2_case(
    date: str,
    rp: RuntimePaths,
    force_predict: bool = False,
    force_features: bool = False,
    training_script: str | None = None,
    nam_dir_override: str | None = None,
    cycle_label: str | None = None,
    allow_feature_nan_fill_zero: bool = False,
) -> pd.DataFrame:
    """Predict the separately versioned r60kmV2 model using shared R60 features."""
    d = date8(date)
    out_path = realtime_labeled_prediction_cache_path(rp, d, R60KM_V2_LABEL)
    if not force_predict and out_path.exists() and out_path.stat().st_size > 1024:
        log(f"Using existing realtime {R60KM_V2_LABEL} prediction cache: {out_path}")
        return pd.read_parquet(out_path)

    df = build_realtime_features(
        d,
        R60KM_V2_RADIUS_KM,
        rp=rp,
        force_features=force_features,
        training_script=training_script,
        nam_dir_override=nam_dir_override,
        cycle_label=cycle_label,
    )
    art = find_r60km_v2_artifacts(
        rp.project_dir,
        original_root=rp.original_root,
        local_root=rp.local_root,
    )
    feature_names = load_feature_names(art["features_path"])
    X_raw = strict_realtime_model_matrix(
        df,
        feature_names,
        context=f"realtime_{d}_{R60KM_V2_LABEL}_{cycle_cache_token(cycle_label)}",
        diagnostic_dir=rp.cache_dir / "diagnostics",
        allow_nan_fill_zero=allow_feature_nan_fill_zero,
    )
    scaler = joblib.load(art["scaler_path"])
    model = joblib.load(art["model_path"])
    X_scaled = scaler.transform(X_raw).astype(np.float32, copy=False)
    probability = np.clip(
        model_positive_class_probability(model, X_scaled), 0.0, 1.0
    ).astype(np.float32)

    keep = [c for c in ["Date", "Year", "Lat", "Lon", "Realtime_Cycle_Label"] if c in df.columns]
    out = df[keep].copy()
    out["Date"] = d
    out["Year"] = d[:4]
    out["ML_Target_Radius_km"] = R60KM_V2_RADIUS_KM
    out["ML_Model_Label"] = R60KM_V2_LABEL
    out["ML_Forecast_Prob"] = probability
    out["ML_Experiment_Tag"] = art["experiment_tag"]
    out["ML_Model_Path"] = art["model_path"]
    out["ML_Feature_Names_Path"] = art["features_path"]
    out["Prediction_Created_UTC"] = datetime.now(timezone.utc).isoformat()
    out["Realtime_Cycle_Label"] = cycle_label or ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    log(
        f"Saved realtime {R60KM_V2_LABEL} predictions: {out_path} rows={len(out):,} "
        f"mean={float(np.nanmean(probability)):.6f} p95={float(np.nanpercentile(probability,95)):.6f} "
        f"p99={float(np.nanpercentile(probability,99)):.6f} max={float(np.nanmax(probability)):.6f}"
    )
    return out


# ======================================================================================
# Multi-radius member merge
# ======================================================================================


def radius_prob_col(radius_km: int | float) -> str:
    return f"ML_r{int(round(float(radius_km)))}_Prob"


def radius_cols_in_df(df: pd.DataFrame, radii: list[int] | None = None) -> list[str]:
    """Return individual-radius ML probability columns in radius order."""
    if radii is not None:
        ordered = []
        for radius in radii:
            ordered.append(radius_prob_col(radius))
            if int(radius) == R60KM_V2_RADIUS_KM:
                ordered.append(R60KM_V2_PROB_COL)
        return [c for c in ordered if c in df.columns]
    cols = [c for c in df.columns if re.match(r"^ML_r(?:\d+|60kmV2)_Prob$", str(c))]
    order = {"ML_r40_Prob": 0, "ML_r60_Prob": 1, R60KM_V2_PROB_COL: 2, "ML_r75_Prob": 3, "ML_r100_Prob": 4}
    return sorted(cols, key=lambda c: (order.get(c, 99), c))


ENSEMBLE_MEAN_COL = "ML_Ensemble_Mean"


def add_ensemble_mean(df: pd.DataFrame, radii: list[int] | None = None) -> pd.DataFrame:
    """Add the pointwise mean of the available radius configurations."""
    out = df.copy()
    member_cols = radius_cols_in_df(out, radii)
    if not member_cols:
        return out
    members = out[member_cols].apply(pd.to_numeric, errors="coerce")
    out[ENSEMBLE_MEAN_COL] = members.mean(axis=1, skipna=True)
    return out


def print_probability_summary(df: pd.DataFrame, cols: list[str], label="probability summary") -> None:
    log(label)
    for c in cols:
        if c not in df.columns:
            continue
        p = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
        if not np.isfinite(p).any():
            log(f"  {c:24s}: no finite values")
            continue
        bits = [f">={thr:g}:{np.nanmean(p >= thr):.4f}" for thr in [0.05, 0.15, 0.40, 0.70]]
        log(f"  {c:24s}: mean={np.nanmean(p):.5f} p95={np.nanpercentile(p,95):.5f} p99={np.nanpercentile(p,99):.5f} max={np.nanmax(p):.5f} | " + ", ".join(bits))


def build_predict_verify_realtime_multi_radius(
    date: str,
    radii: list[int],
    rp: RuntimePaths,
    force_predict: bool = False,
    force_features: bool = False,
    force_ufvs: bool = False,
    force_wpc: bool = False,
    include_ufvs: bool = False,
    include_regular_flood_lsr: bool = False,
    include_wpc: bool = True,
    pp_expansion_radius_km: float = 40.0,
    pp_smooth_radius_km: float = 100.0,
    training_script_by_radius: dict[int, str] | None = None,
    nam_dir_override: str | None = None,
    cycle_label: str | None = None,
    allow_feature_nan_fill_zero: bool = False,
    include_r60km_v2: bool = True,
) -> pd.DataFrame:
    """Build/predict each requested radius and merge them onto one grid.

    The ensemble mean averages all available radius configurations pointwise.
    """
    d = date8(date)
    requested = [int(round(float(r))) for r in radii]
    training_script_by_radius = training_script_by_radius or {}
    base = None
    available, missing = [], []
    for r in requested:
        try:
            pred = predict_realtime_case(
                d,
                radius_km=r,
                rp=rp,
                force_predict=force_predict,
                force_features=force_features,
                training_script=training_script_by_radius.get(r),
                nam_dir_override=nam_dir_override,
                cycle_label=cycle_label,
                allow_feature_nan_fill_zero=allow_feature_nan_fill_zero,
            )
            pred = pred.copy()
            pred["Date"] = pred["Date"].astype(str).str.slice(0, 8)
            pcol = radius_prob_col(r)
            keep = [c for c in ["Date", "Year", "Lat", "Lon", "Realtime_Cycle_Label"] if c in pred.columns]
            if "Year" not in keep:
                pred["Year"] = d[:4]
                keep.append("Year")
            one = pred[keep].copy()
            one[pcol] = pd.to_numeric(pred["ML_Forecast_Prob"], errors="coerce").astype(np.float32)
            one[f"ML_r{r}_Model_Path"] = pred.get("ML_Model_Path", "")
            one[f"ML_r{r}_Feature_Names_Path"] = pred.get("ML_Feature_Names_Path", "")
            if base is None:
                base = one
            else:
                base = merge_grid_by_date_latlon(base, one, columns_to_add=[pcol, f"ML_r{r}_Model_Path", f"ML_r{r}_Feature_Names_Path"])
            available.append(r)
            log(f"Realtime radius member r{r}km loaded: {len(pred):,} rows")
            del pred, one
            gc.collect()
        except Exception as exc:
            missing.append((r, repr(exc)))
            log(f"Skipping realtime radius r{r}km: {exc}")
            if SCRIPT_VERBOSE:
                traceback.print_exc(limit=3)

    available_models = [f"r{r}km" for r in available]
    if include_r60km_v2:
        try:
            pred = predict_realtime_r60km_v2_case(
                d,
                rp=rp,
                force_predict=force_predict,
                force_features=force_features,
                training_script=training_script_by_radius.get(R60KM_V2_RADIUS_KM),
                nam_dir_override=nam_dir_override,
                cycle_label=cycle_label,
                allow_feature_nan_fill_zero=allow_feature_nan_fill_zero,
            )
            pred = pred.copy()
            pred["Date"] = pred["Date"].astype(str).str.slice(0, 8)
            keep = [c for c in ["Date", "Year", "Lat", "Lon", "Realtime_Cycle_Label"] if c in pred.columns]
            if "Year" not in keep:
                pred["Year"] = d[:4]
                keep.append("Year")
            one = pred[keep].copy()
            one[R60KM_V2_PROB_COL] = pd.to_numeric(pred["ML_Forecast_Prob"], errors="coerce").astype(np.float32)
            one["ML_r60kmV2_Model_Path"] = pred.get("ML_Model_Path", "")
            one["ML_r60kmV2_Feature_Names_Path"] = pred.get("ML_Feature_Names_Path", "")
            v2_columns = [R60KM_V2_PROB_COL, "ML_r60kmV2_Model_Path", "ML_r60kmV2_Feature_Names_Path"]
            if base is None:
                base = one
            else:
                base = merge_grid_by_date_latlon(base, one, columns_to_add=v2_columns)
            available_models.append(R60KM_V2_LABEL)
            log(f"Realtime model member {R60KM_V2_LABEL} loaded: {len(pred):,} rows")
            del pred, one
            gc.collect()
        except Exception as exc:
            missing.append((R60KM_V2_LABEL, repr(exc)))
            log(f"Skipping realtime model {R60KM_V2_LABEL}: {exc}")
            if SCRIPT_VERBOSE:
                traceback.print_exc(limit=3)

    if base is None or not available_models:
        detail = "\n".join([f"  {label}: {err}" for label, err in missing])
        raise RuntimeError("No realtime radius members could be loaded.\n" + detail)

    # Preserve the available radius list so downstream plotting/status uses the
    # members that actually ran rather than requested-but-missing members.
    base.attrs["available_radii"] = available
    base.attrs["available_models"] = available_models
    radius_cols = radius_cols_in_df(base, available)
    base = add_ensemble_mean(base, available)

    if include_wpc:
        base = add_wpc_ero_to_realtime_from_iem(base, date=d, rp=rp, force_wpc=force_wpc)
    if include_ufvs:
        base = add_ufvs_and_realtime_pp(
            base,
            date=d,
            rp=rp,
            force_ufvs=force_ufvs,
            include_regular_flood_lsr=include_regular_flood_lsr,
            pp_expansion_radius_km=pp_expansion_radius_km,
            pp_smooth_radius_km=pp_smooth_radius_km,
        )
    summary_cols = radius_cols + ([ENSEMBLE_MEAN_COL] if ENSEMBLE_MEAN_COL in base.columns else []) + ([WPC_COL] if WPC_COL in base.columns else [])
    print_probability_summary(base, summary_cols, "Realtime radius-member probability summary")
    cache_path = multi_radius_cache_path(rp, d, available, cycle_label)
    base.to_parquet(cache_path, index=False)
    log(f"Saved realtime multi-radius cache: {cache_path}")
    return base


def verify_existing_realtime_predictions(
    date: str,
    radii: list[int],
    rp: RuntimePaths,
    force_ufvs: bool = True,
    include_regular_flood_lsr: bool = False,
    pp_expansion_radius_km: float = 40.0,
    pp_smooth_radius_km: float = 100.0,
    cycle_label: str | None = None,
) -> Path | None:
    """Attach UFVS/PP verification to existing forecasts without rebuilding features."""
    d = date8(date)
    base = None
    available = []
    for radius in [int(round(float(r))) for r in radii]:
        candidates = [
            p for p in realtime_prediction_cache_candidates(rp, d, radius, cycle_label)
            if p.exists() and p.stat().st_size > 1024
        ]
        if not candidates:
            log(f"Verification skipped missing r{radius} prediction cache for {d}")
            continue
        pred = pd.read_parquet(candidates[0])
        pred["Date"] = pred["Date"].astype(str).str.slice(0, 8)
        pcol = radius_prob_col(radius)
        keep = [c for c in ["Date", "Year", "Lat", "Lon"] if c in pred.columns]
        one = pred[keep].copy()
        one[pcol] = pd.to_numeric(pred["ML_Forecast_Prob"], errors="coerce").astype(np.float32)
        if base is None:
            base = one
        else:
            base = merge_grid_by_date_latlon(base, one, columns_to_add=[pcol])
        available.append(radius)

    v2_path = realtime_labeled_prediction_cache_path(rp, d, R60KM_V2_LABEL)
    if v2_path.exists() and v2_path.stat().st_size > 1024:
        pred = pd.read_parquet(v2_path)
        pred["Date"] = pred["Date"].astype(str).str.slice(0, 8)
        keep = [c for c in ["Date", "Year", "Lat", "Lon"] if c in pred.columns]
        one = pred[keep].copy()
        one[R60KM_V2_PROB_COL] = pd.to_numeric(pred["ML_Forecast_Prob"], errors="coerce").astype(np.float32)
        if base is None:
            base = one
        else:
            base = merge_grid_by_date_latlon(base, one, columns_to_add=[R60KM_V2_PROB_COL])
        log(f"Verification loaded existing {R60KM_V2_LABEL} prediction cache for {d}")
    else:
        log(f"Verification skipped missing {R60KM_V2_LABEL} prediction cache for {d}")

    if base is None or not available:
        log(f"No existing prediction caches for {d}; internal verification not run.")
        return None

    base = add_ensemble_mean(base, available)

    verified = add_ufvs_and_realtime_pp(
        base,
        date=d,
        rp=rp,
        force_ufvs=force_ufvs,
        include_regular_flood_lsr=include_regular_flood_lsr,
        pp_expansion_radius_km=pp_expansion_radius_km,
        pp_smooth_radius_km=pp_smooth_radius_km,
    )
    rr = "_".join(f"r{int(r)}" for r in available)
    out_path = rp.verified_cache_dir / f"realtime_ufvs_verified_v33_multiradius_{rr}_{d}.parquet"
    verified.to_parquet(out_path, index=False)
    log(f"Saved internally verified realtime forecast: {out_path}")
    return out_path


# ======================================================================================
# WPC ERO fetch/rasterize
# ======================================================================================


def wpc_probability_from_row(row) -> float:
    text = " ".join(str(row.get(c, "")) for c in ["CATEGORY", "THRESHOLD", "LABEL", "OUTLOOK", "name"] if c in row.index).upper()
    if "HIGH" in text:
        return 0.70
    if "MDT" in text or "MOD" in text or "MODERATE" in text:
        return 0.40
    if "SLGT" in text or "SLIGHT" in text:
        return 0.15
    if "MRGL" in text or "MARG" in text or "MARGINAL" in text:
        return 0.05
    return 0.0


def iem_datetime_column(gdf, candidate_names):
    cols_upper = {str(c).upper().strip(): c for c in gdf.columns}
    for name in candidate_names:
        key = str(name).upper().strip()
        if key in cols_upper:
            col = cols_upper[key]
            s = pd.to_datetime(gdf[col], errors="coerce", utc=True)
            if s.notna().any():
                return s, col
    return pd.Series(pd.NaT, index=gdf.index, dtype="datetime64[ns, UTC]"), None


def filter_iem_wpc_ero_to_case_valid_window(gdf, date: str):
    d = date8(date)
    target_start = pd.Timestamp(datetime.strptime(d, "%Y%m%d"), tz="UTC") + pd.Timedelta(hours=12)
    target_end = target_start + pd.Timedelta(days=1)
    issue, issue_col = iem_datetime_column(gdf, ["ISSUE", "VALID", "START", "BEGINTIME"])
    expire, expire_col = iem_datetime_column(gdf, ["EXPIRE", "EXPIRATION", "END", "ENDTIME"])
    prodiss, prodiss_col = iem_datetime_column(gdf, ["PRODISS", "PRODUCTISSUANCE", "ISSUANCE"])
    if issue.notna().any() and expire.notna().any():
        tol = pd.Timedelta(minutes=2)
        exact = (issue.sub(target_start).abs() <= tol) & (expire.sub(target_end).abs() <= tol)
        if exact.any():
            sel = exact.copy()
            reason = "exact 12Z-to-12Z valid-period match"
        else:
            same_end = expire.sub(target_end).abs() <= tol
            candidates = same_end & (issue >= target_start - pd.Timedelta(hours=12)) & (issue < target_end)
            if not candidates.any():
                candidates = (issue < target_end) & (expire > target_start)
            if candidates.any():
                overlap_start = pd.Series(np.maximum(issue[candidates].astype("int64"), target_start.value), index=issue[candidates].index)
                overlap_end = pd.Series(np.minimum(expire[candidates].astype("int64"), target_end.value), index=expire[candidates].index)
                overlap_hours = (overlap_end - overlap_start) / 1e9 / 3600.0
                start_offset_hours = (issue[candidates] - target_start).abs() / pd.Timedelta(hours=1)
                score = overlap_hours - 0.25 * start_offset_hours
                best_index = score.idxmax()
                best_issue = issue.loc[best_index]
                best_expire = expire.loc[best_index]
                sel = (issue == best_issue) & (expire == best_expire)
                reason = "best overlapping valid-period fallback"
            else:
                log(f"Warning: no WPC ERO valid-period rows overlap {target_start} to {target_end}; falling back to unfiltered rows.")
                sel = pd.Series(True, index=gdf.index)
                reason = "unfiltered fallback"
        latest_prod = pd.NaT
        if prodiss.notna().any() and sel.any():
            latest_prod = prodiss[sel].max()
            sel = sel & (prodiss == latest_prod)
        out = gdf.loc[sel].copy()
        out.attrs["wpc_target_valid_start"] = str(target_start)
        out.attrs["wpc_target_valid_end"] = str(target_end)
        out.attrs["wpc_selected_reason"] = reason
        if len(out):
            out.attrs["wpc_selected_valid_start"] = str(issue.loc[out.index].min())
            out.attrs["wpc_selected_valid_end"] = str(expire.loc[out.index].max())
            out.attrs["wpc_selected_prodiss"] = str(latest_prod) if pd.notna(latest_prod) else "unknown"
            log(f"Selected WPC ERO: target={target_start} to {target_end}; selected={out.attrs['wpc_selected_valid_start']} to {out.attrs['wpc_selected_valid_end']}; prodiss={out.attrs['wpc_selected_prodiss']}; reason={reason}; rows={len(out)}")
        return out
    log("Warning: WPC ERO shapefile lacks parseable ISSUE/EXPIRE; falling back to latest PRODISS.")
    if prodiss.notna().any():
        latest = prodiss.max()
        gdf = gdf[prodiss == latest].copy()
        gdf.attrs["wpc_selected_prodiss"] = str(latest)
    return gdf


def download_iem_wpc_ero_gdf(
    date: str,
    rp: RuntimePaths,
    force: bool = False,
    outlook_day: int = 1,
    issuance_date: str | None = None,
):
    """Fetch the outlook valid for ``date`` without conflating issue and valid dates."""
    try:
        import requests
        import geopandas as gpd
    except Exception as exc:
        log(f"WPC ERO fetch skipped: requests/geopandas unavailable ({exc}).")
        return None
    d = date8(date)
    day = int(outlook_day)
    if day not in (1, 2):
        raise ValueError(f"Only WPC ERO Day 1 or Day 2 is supported, got {day}")
    issue_d = date8(issuance_date) if issuance_date is not None else (
        (datetime.strptime(d, "%Y%m%d") - timedelta(days=day - 1)).strftime("%Y%m%d")
    )
    target_dt = datetime.strptime(d, "%Y%m%d")
    issue_dt = datetime.strptime(issue_d, "%Y%m%d")
    target_start = pd.Timestamp(target_dt, tz="UTC") + pd.Timedelta(hours=12)
    target_end = target_start + pd.Timedelta(days=1)
    sts = (issue_dt - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%MZ")
    ets = (issue_dt + timedelta(days=1, hours=18)).strftime("%Y-%m-%dT%H:%MZ")
    cache_base = rp.wpc_cache_dir / f"iem_wpc_ero_day{day}_issue{issue_d}_valid{d}_12to12"
    geom_candidates = ["cookie", "cutter", "nonoverlap", None, "layers", "layer", "1", "0"]
    last_error = None
    for geom in geom_candidates:
        suffix = "default" if geom is None else str(geom)
        zip_path = cache_base.with_name(cache_base.name + f"_{suffix}.zip")
        params = {"type": "E", "d": str(day), "sts": sts, "ets": ets}
        if geom is not None:
            params["geom"] = geom
        try:
            if force or (not zip_path.exists()) or zip_path.stat().st_size < 128:
                log(f"Fetching WPC ERO from IEM params={params}")
                resp = requests.get(WPC_IEM_OUTLOOK_URL, params=params, timeout=45)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code} for geom={geom}"
                    continue
                content = resp.content
                if not content.startswith(b"PK"):
                    last_error = f"IEM response for geom={geom} was not zip; first bytes={content[:40]!r}"
                    continue
                zip_path.write_bytes(content)
            try:
                gdf = gpd.read_file(f"zip://{zip_path}")
            except Exception:
                gdf = gpd.read_file(str(zip_path))
            if gdf is None or gdf.empty:
                last_error = f"empty shapefile for geom={geom}"
                continue
            cols_upper = {str(c).upper().strip(): c for c in gdf.columns}
            if "TYPE" in cols_upper:
                c = cols_upper["TYPE"]
                gdf = gdf[gdf[c].astype(str).str.upper().str.contains("E", na=False)].copy()
            if "DAY" in cols_upper:
                c = cols_upper["DAY"]
                gdf = gdf[pd.to_numeric(gdf[c], errors="coerce").fillna(-999).astype(int) == day].copy()
            if gdf.empty:
                last_error = f"no day-{day} WPC ERO rows after filtering for geom={geom}"
                continue
            gdf = filter_iem_wpc_ero_to_case_valid_window(gdf, d)
            if gdf is None or gdf.empty:
                last_error = f"no WPC polygons matched target valid window {target_start} to {target_end} for geom={geom}"
                continue
            if gdf.crs is not None:
                try:
                    gdf = gdf.to_crs(epsg=4326)
                except Exception as exc:
                    log(f"Warning: could not reproject WPC CRS {gdf.crs}: {exc}")
            gdf["__WPC_ERO_Risk"] = gdf.apply(wpc_probability_from_row, axis=1).astype(float)
            gdf = gdf[gdf["__WPC_ERO_Risk"] > 0].copy()
            if gdf.empty:
                last_error = f"WPC polygons existed but no recognized risk categories for geom={geom}"
                continue
            gdf.attrs["wpc_outlook_day"] = day
            gdf.attrs["wpc_issue_date"] = issue_d
            log(f"Loaded {len(gdf)} WPC ERO Day {day} polygons; issue={issue_d} valid={d}")
            return gdf
        except Exception as exc:
            last_error = repr(exc)
            continue
    log(f"No WPC ERO polygons could be loaded for {d}. Last error: {last_error}")
    return None


def rasterize_wpc_gdf_to_grid(gdf, df_grid: pd.DataFrame) -> np.ndarray:
    out = np.zeros(len(df_grid), dtype=np.float32)
    if gdf is None or len(gdf) == 0 or len(df_grid) == 0:
        return out
    lon = pd.to_numeric(df_grid["Lon"], errors="coerce").to_numpy(float)
    lat = pd.to_numeric(df_grid["Lat"], errors="coerce").to_numpy(float)
    finite = np.isfinite(lon) & np.isfinite(lat)
    if not finite.any():
        return out
    try:
        import shapely
        contains_xy = getattr(shapely, "contains_xy", None)
    except Exception:
        contains_xy = None
    from shapely.geometry import Point
    from shapely.prepared import prep
    for _, row in gdf.sort_values("__WPC_ERO_Risk").iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        risk = float(row["__WPC_ERO_Risk"])
        minx, miny, maxx, maxy = geom.bounds
        bbox = finite & (lon >= minx) & (lon <= maxx) & (lat >= miny) & (lat <= maxy)
        idx = np.flatnonzero(bbox)
        if idx.size == 0:
            continue
        try:
            if contains_xy is not None:
                inside = contains_xy(geom, lon[idx], lat[idx])
            else:
                pg = prep(geom)
                inside = np.array([pg.contains(Point(x, y)) or pg.touches(Point(x, y)) for x, y in zip(lon[idx], lat[idx])], dtype=bool)
        except Exception:
            pg = prep(geom)
            inside = np.array([pg.contains(Point(x, y)) or pg.touches(Point(x, y)) for x, y in zip(lon[idx], lat[idx])], dtype=bool)
        if np.any(inside):
            out[idx[inside]] = np.maximum(out[idx[inside]], risk)
    return out


def add_wpc_ero_to_realtime_from_iem(
    df: pd.DataFrame,
    date: str,
    rp: RuntimePaths,
    force_wpc: bool = False,
    outlook_day: int = 1,
    issuance_date: str | None = None,
) -> pd.DataFrame:
    out = df.copy()
    d = date8(date)
    day = int(outlook_day)
    issue_d = date8(issuance_date) if issuance_date is not None else (
        (datetime.strptime(d, "%Y%m%d") - timedelta(days=day - 1)).strftime("%Y%m%d")
    )
    wpc_cache = rp.wpc_cache_dir / f"wpc_ero_day{day}_issue{issue_d}_valid{d}_12to12_{len(out)}rows.parquet"
    if wpc_cache.exists() and wpc_cache.stat().st_size > 1024 and not force_wpc:
        tmp = pd.read_parquet(wpc_cache)
        if WPC_COL in tmp.columns and len(tmp) == len(out):
            out[WPC_COL] = pd.to_numeric(tmp[WPC_COL], errors="coerce").fillna(0).to_numpy(np.float32)
            for meta_col in ["WPC_ERO_Target_Valid_Start", "WPC_ERO_Target_Valid_End", "WPC_ERO_Selected_Valid_Start", "WPC_ERO_Selected_Valid_End", "WPC_ERO_Product_Issuance"]:
                if meta_col in tmp.columns:
                    out[meta_col] = tmp[meta_col].astype(str).iloc[0]
            log(f"Loaded cached WPC ERO grid: {wpc_cache}")
            return out
    gdf = download_iem_wpc_ero_gdf(
        d,
        rp=rp,
        force=force_wpc,
        outlook_day=day,
        issuance_date=issue_d,
    )
    if gdf is None or len(gdf) == 0:
        log(f"WPC ERO unavailable for {d}; continuing without WPC panel.")
        return out
    out[WPC_COL] = rasterize_wpc_gdf_to_grid(gdf, out)
    out["WPC_ERO_Target_Valid_Start"] = str(gdf.attrs.get("wpc_target_valid_start", ""))
    out["WPC_ERO_Target_Valid_End"] = str(gdf.attrs.get("wpc_target_valid_end", ""))
    out["WPC_ERO_Selected_Valid_Start"] = str(gdf.attrs.get("wpc_selected_valid_start", ""))
    out["WPC_ERO_Selected_Valid_End"] = str(gdf.attrs.get("wpc_selected_valid_end", ""))
    out["WPC_ERO_Product_Issuance"] = str(gdf.attrs.get("wpc_selected_prodiss", ""))
    out["WPC_ERO_Outlook_Day"] = day
    out["Forecast_Issue_Date"] = issue_d
    out[["Date", "Lat", "Lon", WPC_COL, "WPC_ERO_Target_Valid_Start", "WPC_ERO_Target_Valid_End", "WPC_ERO_Selected_Valid_Start", "WPC_ERO_Selected_Valid_End", "WPC_ERO_Product_Issuance", "WPC_ERO_Outlook_Day", "Forecast_Issue_Date"]].to_parquet(wpc_cache, index=False)
    log(f"Saved WPC ERO raster cache: {wpc_cache}")
    log(f"WPC risk pixels: >5%={(out[WPC_COL] >= 0.05).sum():,}, >15%={(out[WPC_COL] >= 0.15).sum():,}, >40%={(out[WPC_COL] >= 0.40).sum():,}, >70%={(out[WPC_COL] >= 0.70).sum():,}")
    return out


# ======================================================================================
# UFVS/PP verification, optional
# ======================================================================================


def ufvs_window_strings(date: str):
    d = date8(date)
    start = datetime.strptime(d, "%Y%m%d")
    end = start + timedelta(days=1)
    return [(f"{d}12", f"{end.strftime('%Y%m%d')}12"), (f"{d}16", f"{end.strftime('%Y%m%d')}12")]


def ufvs_raw_cache_path(rp: RuntimePaths, prefix: str, date: str, start_stamp: str, end_stamp: str) -> Path:
    return rp.ufvs_cache_dir / f"{prefix}_s{start_stamp}_e{end_stamp}.txt"


def parse_ufvs_text_points(text, prefix: str) -> pd.DataFrame:
    vals = re.findall(r"[-+]?\d+(?:\.\d+)?", str(text))
    nums = [float(v) for v in vals]
    pts = []
    for a, b in zip(nums[0::2], nums[1::2]):
        if 15.0 <= a <= 60.0 and -130.0 <= b <= -60.0:
            pts.append((a, b))
        elif 15.0 <= b <= 60.0 and -130.0 <= a <= -60.0:
            pts.append((b, a))
    out = pd.DataFrame(pts, columns=["Lat", "Lon"])
    if len(out):
        out = out.drop_duplicates(subset=["Lat", "Lon"]).reset_index(drop=True)
    out["source"] = prefix
    return out


def fetch_ufvs_points(date: str, prefix: str, rp: RuntimePaths, force: bool = False, timeout: int = 10) -> pd.DataFrame:
    try:
        import requests
    except Exception as exc:
        log(f"UFVS fetch skipped; requests unavailable: {exc}")
        return pd.DataFrame(columns=["Lat", "Lon", "source", "ufvs_file"])
    last_error = None
    for start_stamp, end_stamp in ufvs_window_strings(date):
        cache_path = ufvs_raw_cache_path(rp, prefix, date, start_stamp, end_stamp)
        url = f"{UFVS_BASE_URL}/{prefix}_s{start_stamp}_e{end_stamp}.txt"
        try:
            source_available = False
            if cache_path.exists() and cache_path.stat().st_size > 0 and not force:
                text = cache_path.read_text(errors="ignore")
                source_available = True
            else:
                log(f"Fetching UFVS {prefix}: {url}")
                resp = requests.get(url, timeout=timeout)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {url}"
                    continue
                text = resp.text
                cache_path.write_text(text)
                source_available = True
            pts = parse_ufvs_text_points(text, prefix)
            pts["ufvs_file"] = cache_path.name
            pts.attrs["ufvs_available"] = source_available
            return pts
        except Exception as exc:
            last_error = repr(exc)
    log(f"{date8(date)} {prefix}: no UFVS file found/parsed. Last error: {last_error}")
    return pd.DataFrame(columns=["Lat", "Lon", "source", "ufvs_file"])


def filter_points_to_extent(pts: pd.DataFrame, extent=None) -> pd.DataFrame:
    if pts is None or len(pts) == 0:
        return pd.DataFrame(columns=["Lat", "Lon"])
    ed = extent_dict(extent)
    lat = pd.to_numeric(pts["Lat"], errors="coerce")
    lon = pd.to_numeric(pts["Lon"], errors="coerce")
    return pts[(lon >= ed["lon_min"]) & (lon <= ed["lon_max"]) & (lat >= ed["lat_min"]) & (lat <= ed["lat_max"])].copy()


def event_mask_from_points(df_grid: pd.DataFrame, pts: pd.DataFrame, max_dist_km=20.0) -> np.ndarray:
    mask = np.zeros(len(df_grid), dtype=bool)
    if pts is None or len(pts) == 0 or len(df_grid) == 0:
        return mask
    grid_xyz = latlon_to_unit_xyz(df_grid["Lat"].to_numpy(float), df_grid["Lon"].to_numpy(float))
    pts_xyz = latlon_to_unit_xyz(pts["Lat"].to_numpy(float), pts["Lon"].to_numpy(float))
    tree = cKDTree(grid_xyz)
    dist, loc = tree.query(pts_xyz, k=1)
    max_chord = km_to_unit_sphere_chord_radius(max_dist_km)
    good = np.isfinite(dist) & (dist <= max_chord)
    if np.any(good):
        mask[np.unique(loc[good])] = True
    return mask


def smooth_grid_values_by_km(df_grid: pd.DataFrame, values: np.ndarray, smooth_radius_km=100.0, chunk_size=1500, cutoff_sigma=3.0) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.size == 0 or not np.isfinite(values).any() or np.nanmax(values) <= 0:
        return np.zeros_like(values, dtype=np.float32)
    if smooth_radius_km is None or float(smooth_radius_km) <= 0:
        return np.clip(values, 0.0, 1.0).astype(np.float32)
    sigma_km = float(smooth_radius_km)
    max_km = float(cutoff_sigma) * sigma_km
    max_chord = km_to_unit_sphere_chord_radius(max_km)
    lat = df_grid["Lat"].to_numpy(float)
    lon = df_grid["Lon"].to_numpy(float)
    xyz = latlon_to_unit_xyz(lat, lon)
    tree = cKDTree(xyz)
    binary = (np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0) > 0).astype(np.float32)
    out = np.zeros(len(binary), dtype=np.float32)
    for start in range(0, len(binary), int(chunk_size)):
        end = min(start + int(chunk_size), len(binary))
        neigh = tree.query_ball_point(xyz[start:end], r=max_chord)
        for ii, idx in enumerate(neigh):
            if not idx:
                out[start + ii] = 0.0
                continue
            idx = np.asarray(idx, dtype=np.int64)
            cd = np.linalg.norm(xyz[idx] - xyz[start + ii], axis=1)
            cd = np.clip(cd, 0.0, 2.0)
            dist_km = EARTH_RADIUS_KM * (2.0 * np.arcsin(cd / 2.0))
            w = np.exp(-0.5 * (dist_km / sigma_km) ** 2).astype(np.float32)
            den = float(np.sum(w))
            out[start + ii] = 0.0 if den <= 0 else float(np.sum(w * binary[idx]) / den)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def pp_from_points(df_grid: pd.DataFrame, pts: pd.DataFrame, expansion_radius_km=40.0, smooth_radius_km=100.0, max_nearest_dist_km=20.0) -> tuple[np.ndarray, dict]:
    if pts is None or len(pts) == 0:
        return np.zeros(len(df_grid), dtype=np.float32), {"raw_points": 0, "nearest_event_pixels": 0, "expanded_pixels": 0, "smoothed": False}
    xyz = latlon_to_unit_xyz(df_grid["Lat"].to_numpy(float), df_grid["Lon"].to_numpy(float))
    tree = cKDTree(xyz)
    event = event_mask_from_points(df_grid, pts, max_dist_km=max_nearest_dist_km)
    expanded = expand_binary_mask_radius_km(event, tree, xyz, radius_km=float(expansion_radius_km)).astype(np.float32)
    smoothed = smooth_grid_values_by_km(df_grid, expanded, smooth_radius_km=float(smooth_radius_km))
    meta = {"raw_points": int(len(pts)), "nearest_event_pixels": int(event.sum()), "expanded_pixels": int(expanded.sum()), "smoothed": bool(smooth_radius_km is not None and float(smooth_radius_km) > 0)}
    return np.clip(smoothed, 0.0, 1.0).astype(np.float32), meta


def add_ufvs_and_realtime_pp(
    df: pd.DataFrame,
    date: str,
    rp: RuntimePaths,
    force_ufvs: bool = False,
    include_regular_flood_lsr: bool = False,
    extent=DEFAULT_EXTENT,
    pp_expansion_radius_km=40.0,
    pp_smooth_radius_km=100.0,
    max_nearest_dist_km=25.0,
) -> pd.DataFrame:
    out = df.copy()
    d = date8(date)
    ed = extent_dict(extent)
    prefixes = list(REALTIME_PP_SOURCE_PREFIXES)
    if include_regular_flood_lsr and "LSRREG" not in prefixes:
        prefixes.append("LSRREG")
    pp_cache = rp.pp_cache_dir / f"pp_ufvs_{d}_expand{int(round(float(pp_expansion_radius_km)))}km_smooth{int(round(float(pp_smooth_radius_km)))}km_{len(out)}rows.parquet"
    if pp_cache.exists() and pp_cache.stat().st_size > 1024 and not force_ufvs:
        cached = pd.read_parquet(pp_cache)
        add_cols = [c for c in cached.columns if c.startswith("UFVS_") or c.startswith("PP_")]
        if add_cols and len(cached) == len(out):
            for c in add_cols:
                out[c] = cached[c].to_numpy()
            log(f"Loaded cached realtime UFVS/PP grid: {pp_cache}")
            return out
    grid_domain = out[
        (pd.to_numeric(out["Lon"], errors="coerce") >= ed["lon_min"]) &
        (pd.to_numeric(out["Lon"], errors="coerce") <= ed["lon_max"]) &
        (pd.to_numeric(out["Lat"], errors="coerce") >= ed["lat_min"]) &
        (pd.to_numeric(out["Lat"], errors="coerce") <= ed["lat_max"])
    ].copy()
    if grid_domain.empty:
        log(f"No grid rows inside requested extent for {d}; cannot map UFVS/PP.")
        return out
    log(f"Starting UFVS/PP processing for {d}: prefixes={prefixes}, domain_rows={len(grid_domain):,}")
    prefix_to_points = {}
    summary = []
    available_sources = 0
    for prefix in prefixes:
        col = UFVS_PREFIX_TO_COL.get(prefix, f"UFVS_{prefix}")
        fetched = fetch_ufvs_points(d, prefix, rp=rp, force=force_ufvs)
        source_available = bool(fetched.attrs.get("ufvs_available", False))
        available_sources += int(source_available)
        pts = filter_points_to_extent(fetched, extent=extent)
        prefix_to_points[prefix] = pts
        flags_domain = event_mask_from_points(grid_domain, pts, max_dist_km=max_nearest_dist_km).astype(np.int8)
        out[col] = 0
        out.loc[grid_domain.index, col] = flags_domain
        summary.append({"prefix": prefix, "column": col, "available": source_available, "points_in_extent": int(len(pts)), "nearest_event_pixels": int(flags_domain.sum())})
        log(f"  {prefix}: points_in_extent={len(pts):,}, nearest_event_pixels={int(flags_domain.sum()):,}")
    if available_sources == 0:
        raise RuntimeError(f"UFVS verification is not available yet for {d}; no verification cache was written.")
    ufvs_cols = [UFVS_PREFIX_TO_COL[p] for p in prefixes if p in UFVS_PREFIX_TO_COL and UFVS_PREFIX_TO_COL[p] in out.columns]
    if ufvs_cols:
        out["UFVS_ANY"] = (out[ufvs_cols].apply(pd.to_numeric, errors="coerce").fillna(0).max(axis=1) > 0).astype(np.int8)
    point_sets = {
        "PP_Stage IV > FFG": prefix_to_points.get("ST4gFFG", pd.DataFrame(columns=["Lat", "Lon"])),
        "PP_Stage IV ARI": prefix_to_points.get("ST4gARI", pd.DataFrame(columns=["Lat", "Lon"])),
        "PP_USGS": prefix_to_points.get("USGS", pd.DataFrame(columns=["Lat", "Lon"])),
        "PP_Flash LSR": prefix_to_points.get("LSRFLASH", pd.DataFrame(columns=["Lat", "Lon"])),
    }
    if include_regular_flood_lsr:
        point_sets["PP_Flood LSR"] = prefix_to_points.get("LSRREG", pd.DataFrame(columns=["Lat", "Lon"]))
    all_pts = [v for v in point_sets.values() if v is not None and len(v) > 0]
    union_pts = pd.concat(all_pts, ignore_index=True).drop_duplicates(subset=["Lat", "Lon"]) if all_pts else pd.DataFrame(columns=["Lat", "Lon"])
    point_sets["PP_Any flood proxy"] = union_pts
    any_pp_created = False
    pp_meta = []
    for pp_col, pts in point_sets.items():
        vals_domain, meta = pp_from_points(grid_domain, pts, expansion_radius_km=pp_expansion_radius_km, smooth_radius_km=pp_smooth_radius_km, max_nearest_dist_km=max_nearest_dist_km)
        out[pp_col] = 0.0
        out.loc[grid_domain.index, pp_col] = vals_domain.astype(np.float32)
        any_pp_created = any_pp_created or (meta["raw_points"] > 0)
        pp_meta.append({"pp_column": pp_col, **meta})
        log(f"  {pp_col}: {meta}")
    if not any_pp_created:
        log(f"No UFVS verification points found in domain for {d}; PP fields are zero/omitted on plot.")
    save_cols = ["Date", "Lat", "Lon"] + [c for c in out.columns if c.startswith("UFVS_") or c.startswith("PP_")]
    out[save_cols].to_parquet(pp_cache, index=False)
    meta_path = pp_cache.with_suffix(".summary.json")
    meta_path.write_text(json.dumps({"ufvs_summary": summary, "pp_meta": pp_meta}, indent=2))
    log(f"Saved realtime UFVS/PP cache: {pp_cache}")
    return out


# ======================================================================================
# HRRR simulated-IR reader and MCS detector
# ======================================================================================


@dataclass
class MCSObject:
    label: int
    area_km2: float
    pixel_count: int
    min_bt_k: float
    mean_bt_k: float
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass
class MCSDetectionResult:
    triggered: bool
    threshold_k: float
    min_area_km2: float
    n_objects_total: int
    n_objects_passing: int
    largest_area_km2: float
    selected: MCSObject | None
    mask_path: str | None = None
    source: str = "HRRR simulated IR"
    hrrr_cycle: str | None = None
    selected_fhr: int | None = None
    selected_valid_utc: str | None = None
    ir_trigger: bool | None = None
    ir_duration_met: bool | None = None
    structural_duration_met: bool | None = None
    max_ir_duration_hours: int | None = None
    max_joint_duration_hours: int | None = None
    qpf6_trigger: bool | None = None
    qpf6_threshold_met: bool | None = None
    mcs_method: str | None = None
    pyflextrkr_package_version: str | None = None
    pyflextrkr_upstream_commit: str | None = None
    pyflextrkr_result_path: str | None = None
    pyflextrkr_config_path: str | None = None
    pyflextrkr_input_manifest_path: str | None = None
    pyflextrkr_official_steps_completed: list[str] | None = None
    summary_path: str | None = None
    ir_debug_image: str | None = None
    qpf6_debug_image: str | None = None
    hrrr_triggered: bool | None = None
    model_results: dict | None = None


HRRR_NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
HRRR_AWS_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_DEFAULT_CYCLE = "12"
HRRR_DEFAULT_FHR_START = 0
HRRR_DEFAULT_FHR_END = 24
HRRR_DEFAULT_CELL_AREA_KM2 = 9.0
HRRR_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 180
HRRR_DEFAULT_MAX_DOWNLOAD_ATTEMPTS = 4
HRRR_DEFAULT_DOWNLOAD_RETRY_SECONDS = 20
HRRR_DEFAULT_NOMADS_PAUSE_SECONDS = 0.25
RAP_AWS_BASE = "https://noaa-rap-pds.s3.amazonaws.com"
RAP_DEFAULT_CYCLE = "09"
RAP_DEFAULT_FHR_START = 3
RAP_DEFAULT_FHR_END = 27
RAP_DEFAULT_CELL_AREA_KM2 = 169.0
QPF6_THRESHOLD_MM_DEFAULT = 50.8
QPF6_AREA_THRESHOLD_KM2_DEFAULT = 0.0


def hrrr_valid_time_utc(run_date: str, cycle: str, fhr: int) -> datetime:
    init = datetime.strptime(date8(run_date) + f"{int(cycle):02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return init + timedelta(hours=int(fhr))


def hrrr_filename(cycle: str, fhr: int) -> str:
    return f"hrrr.t{int(cycle):02d}z.wrfsfcf{int(fhr):02d}.grib2"


def hrrr_bbox_query_params(extent=DEFAULT_EXTENT) -> list[tuple[str, str]]:
    ed = extent_dict(extent)
    return [
        ("subregion", ""),
        ("leftlon", str(ed["lon_min"])),
        ("rightlon", str(ed["lon_max"])),
        ("toplat", str(ed["lat_max"])),
        ("bottomlat", str(ed["lat_min"])),
    ]


def hrrr_nomads_url(run_date: str, cycle: str, fhr: int, var_names, level_name: str, extent=DEFAULT_EXTENT) -> str:
    if isinstance(var_names, str):
        var_names = [var_names]
    params = [
        ("dir", f"/hrrr.{date8(run_date)}/conus"),
        ("file", hrrr_filename(cycle, fhr)),
    ]
    for v in var_names:
        params.append((f"var_{v}", "on"))
    params.append((level_name, "on"))
    params.extend(hrrr_bbox_query_params(extent))
    return HRRR_NOMADS_BASE + "?" + urllib.parse.urlencode(params)


def hrrr_aws_url(run_date: str, cycle: str, fhr: int) -> str:
    return (
        f"{HRRR_AWS_BASE}/hrrr.{date8(run_date)}/conus/"
        f"{hrrr_filename(cycle, fhr)}"
    )


def rap_filename(cycle: str, fhr: int) -> str:
    return f"rap.t{int(cycle):02d}z.awp130pgrbf{int(fhr):02d}.grib2"


def rap_aws_url(run_date: str, cycle: str, fhr: int) -> str:
    return (
        f"{RAP_AWS_BASE}/rap.{date8(run_date)}/"
        f"{rap_filename(cycle, fhr)}"
    )


def selected_hrrr_source(run_date: str, requested: str) -> str:
    if requested != "auto":
        return requested
    run_day = datetime.strptime(date8(run_date), "%Y%m%d").date()
    age_days = (datetime.now(timezone.utc).date() - run_day).days
    return "nomads" if age_days <= 2 else "aws"


def is_complete_grib_file(path: str | Path) -> bool:
    """Accept complete GRIB messages, including the tiny all-zero APCP f00 record."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 20:
        return False
    with path.open("rb") as handle:
        if handle.read(4) != b"GRIB":
            return False
        handle.seek(-4, 2)
        return handle.read(4) == b"7777"


def download_indexed_grib_records(
    session,
    data_url: str,
    selections: list[tuple[str, str]],
    out_path: str | Path,
    *,
    timeout: int,
    overwrite: bool = False,
) -> Path:
    """Download selected GRIB records with byte ranges from a NOAA archive."""
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and is_complete_grib_file(out_path):
        return out_path
    idx_url = data_url + ".idx"
    cache = getattr(session, "_xgbffp_grib_idx_cache", {})
    if idx_url not in cache:
        response = session.get(idx_url, timeout=int(timeout))
        response.raise_for_status()
        cache[idx_url] = response.text
        session._xgbffp_grib_idx_cache = cache
    entries = []
    for line in cache[idx_url].splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        try:
            entries.append({"start": int(parts[1]), "variable": parts[3], "level": parts[4], "line": line})
        except ValueError:
            continue
    if not entries:
        raise RuntimeError(f"No parsable GRIB index entries: {idx_url}")
    selected_entries = []
    for variable, level_text in selections:
        match = next(
            (
                entry
                for entry in entries
                if entry["variable"].upper() == variable.upper()
                and level_text.lower() in entry["level"].lower()
            ),
            None,
        )
        if match is None:
            raise RuntimeError(f"{variable}:{level_text} is absent from {idx_url}")
        selected_entries.append(match)
    content_length = None
    chunks = []
    for selected in selected_entries:
        index = entries.index(selected)
        if index + 1 < len(entries):
            end = int(entries[index + 1]["start"]) - 1
        else:
            if content_length is None:
                head = session.head(data_url, timeout=int(timeout))
                head.raise_for_status()
                content_length = int(head.headers["content-length"])
            end = content_length - 1
        response = session.get(
            data_url,
            headers={"Range": f"bytes={selected['start']}-{end}"},
            timeout=int(timeout),
        )
        if response.status_code not in (200, 206):
            raise RuntimeError(
                f"NOAA model archive returned {response.status_code} for {selected['line']}"
            )
        chunk = response.content
        if response.status_code == 200 and len(chunk) > end - int(selected["start"]) + 1:
            chunk = chunk[int(selected["start"]):end + 1]
        if chunk[:4] != b"GRIB":
            raise RuntimeError(f"Archive byte range does not begin with GRIB: {selected['line']}")
        chunks.append(chunk)
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    with part_path.open("wb") as handle:
        for chunk in chunks:
            handle.write(chunk)
    part_path.replace(out_path)
    return out_path


def download_aws_grib_records(
    session,
    run_date: str,
    cycle: str,
    fhr: int,
    selections: list[tuple[str, str]],
    out_path: str | Path,
    *,
    timeout: int,
    overwrite: bool = False,
) -> Path:
    """Download selected HRRR GRIB records with byte ranges from NOAA's archive."""
    return download_indexed_grib_records(
        session,
        hrrr_aws_url(run_date, cycle, fhr),
        selections,
        out_path,
        timeout=timeout,
        overwrite=overwrite,
    )


def download_rap_field_subset(
    session,
    run_date: str,
    cycle: str,
    fhr: int,
    out_dir: str | Path,
    field: str,
    args,
) -> tuple[Path, str]:
    field = field.upper()
    levels = {
        "REFC": "entire atmosphere",
        "SBT123": "top of atmosphere",
        "SBT124": "top of atmosphere",
    }
    if field == "SBT":
        absent = []
        for candidate in ("SBT123", "SBT124"):
            try:
                return download_rap_field_subset(
                    session, run_date, cycle, fhr, out_dir, candidate, args
                )
            except Exception as exc:
                if "is absent from" not in str(exc):
                    raise
                absent.append(str(exc))
        raise RuntimeError("RAP SBT123 and SBT124 are absent: " + "; ".join(absent))
    if field not in levels:
        raise ValueError(f"Unsupported RAP gate field: {field}")
    data_url = rap_aws_url(run_date, cycle, fhr)
    out_path = Path(out_dir) / (
        f"rap_{date8(run_date)}_t{int(cycle):02d}z_f{int(fhr):02d}_{field}.grib2"
    )
    path = download_indexed_grib_records(
        session,
        data_url,
        [(field, levels[field])],
        out_path,
        timeout=args.hrrr_download_timeout,
        overwrite=args.force_rap_download,
    )
    return path, data_url


def download_hrrr_field_subset(
    session,
    run_date: str,
    cycle: str,
    fhr: int,
    out_path: str | Path,
    *,
    nomads_variables: list[str],
    nomads_level: str,
    archive_selections: list[tuple[str, str]],
    args,
) -> tuple[Path, str]:
    source = selected_hrrr_source(run_date, args.hrrr_data_source)
    nomads_url = hrrr_nomads_url(
        run_date, cycle, fhr, nomads_variables, nomads_level, extent=args.extent
    )
    if source == "nomads":
        try:
            return download_nomads_subset(
                session,
                nomads_url,
                out_path,
                timeout=args.hrrr_download_timeout,
                max_attempts=args.hrrr_max_download_attempts,
                retry_seconds=args.hrrr_download_retry_seconds,
                pause_seconds=args.hrrr_nomads_pause_seconds,
                overwrite=args.force_hrrr_download,
            ), nomads_url
        except Exception:
            if args.hrrr_data_source != "auto":
                raise
            log(f"NOMADS unavailable for {date8(run_date)} f{fhr:02d}; trying NOAA AWS archive.")
    path = download_aws_grib_records(
        session,
        run_date,
        cycle,
        fhr,
        archive_selections,
        out_path,
        timeout=args.hrrr_download_timeout,
        overwrite=args.force_hrrr_download,
    )
    return path, hrrr_aws_url(run_date, cycle, fhr)


def download_nomads_subset(
    session,
    url: str,
    out_path: str | Path,
    timeout: int = HRRR_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    max_attempts: int = HRRR_DEFAULT_MAX_DOWNLOAD_ATTEMPTS,
    retry_seconds: int = HRRR_DEFAULT_DOWNLOAD_RETRY_SECONDS,
    pause_seconds: float = HRRR_DEFAULT_NOMADS_PAUSE_SECONDS,
    overwrite: bool = False,
) -> Path:
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and is_complete_grib_file(out_path):
        vlog(f"Using cached HRRR subset: {out_path}")
        return out_path
    part_path = out_path.with_suffix(out_path.suffix + ".part")
    last_error = None
    for attempt in range(1, int(max_attempts) + 1):
        try:
            vlog(f"HRRR download attempt {attempt}/{max_attempts}: {url}")
            if part_path.exists():
                part_path.unlink()
            with session.get(url, timeout=int(timeout), stream=True) as r:
                vlog(
                    "  NOMADS status={} content-type={} content-length={}".format(
                        r.status_code,
                        r.headers.get("content-type"),
                        r.headers.get("content-length"),
                    )
                )
                if r.status_code != 200:
                    raise RuntimeError(f"NOMADS status {r.status_code}")
                nbytes = 0
                with part_path.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            nbytes += len(chunk)
            if not is_complete_grib_file(part_path):
                head = part_path.read_bytes()[:500] if part_path.exists() else b""
                raise RuntimeError(
                    f"Downloaded file is not a complete GRIB message: {nbytes} bytes. "
                    f"Head={head!r}"
                )
            part_path.replace(out_path)
            vlog(f"  saved HRRR subset: {out_path} ({nbytes} bytes)")
            if pause_seconds and pause_seconds > 0:
                import time
                time.sleep(float(pause_seconds))
            return out_path
        except Exception as exc:
            last_error = exc
            log(f"HRRR download failed for {out_path.name}: {exc}")
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass
            if attempt < int(max_attempts):
                import time
                time.sleep(float(retry_seconds))
    raise RuntimeError(f"Failed HRRR subset download after {max_attempts} attempts. Last error: {last_error}")


def download_hrrr_ir_subset(session, run_date: str, cycle: str, fhr: int, out_dir: str | Path, args) -> tuple[Path, str]:
    out_path = Path(out_dir) / f"hrrr_{date8(run_date)}_t{int(cycle):02d}z_f{int(fhr):02d}_SBT123_SBT124.grib2"
    return download_hrrr_field_subset(
        session,
        run_date,
        cycle,
        fhr,
        out_path,
        nomads_variables=["SBT123", "SBT124"],
        nomads_level="lev_top_of_atmosphere",
        archive_selections=[("SBT123", "top of atmosphere")],
        args=args,
    )


def download_hrrr_refc_subset(session, run_date: str, cycle: str, fhr: int, out_dir: str | Path, args) -> tuple[Path, str]:
    out_path = Path(out_dir) / f"hrrr_{date8(run_date)}_t{int(cycle):02d}z_f{int(fhr):02d}_REFC.grib2"
    return download_hrrr_field_subset(
        session,
        run_date,
        cycle,
        fhr,
        out_path,
        nomads_variables=["REFC"],
        nomads_level="lev_entire_atmosphere",
        archive_selections=[("REFC", "entire atmosphere")],
        args=args,
    )


def download_hrrr_apcp_subset(session, run_date: str, cycle: str, fhr: int, out_dir: str | Path, args) -> tuple[Path, str]:
    out_path = Path(out_dir) / f"hrrr_{date8(run_date)}_t{int(cycle):02d}z_f{int(fhr):02d}_APCP.grib2"
    return download_hrrr_field_subset(
        session,
        run_date,
        cycle,
        fhr,
        out_path,
        nomads_variables=["APCP"],
        nomads_level="lev_surface",
        archive_selections=[("APCP", "surface")],
        args=args,
    )


def read_grib_all_fields(path: str | Path) -> list[dict]:
    path = Path(path).expanduser()
    fields: list[dict] = []
    # xarray/cfgrib path. This is usually available in the same environment used by the notebook.
    try:
        import xarray as xr
        try:
            ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
            if "latitude" in ds:
                lat = np.asarray(ds["latitude"].values, dtype=float)
            elif "lat" in ds:
                lat = np.asarray(ds["lat"].values, dtype=float)
            else:
                raise RuntimeError("No latitude coordinate found by cfgrib.")
            if "longitude" in ds:
                lon = np.asarray(ds["longitude"].values, dtype=float)
            elif "lon" in ds:
                lon = np.asarray(ds["lon"].values, dtype=float)
            else:
                raise RuntimeError("No longitude coordinate found by cfgrib.")
            lon = normalize_lon(lon)
            for vname in list(ds.data_vars):
                fields.append({
                    "name": str(vname),
                    "data": np.asarray(ds[vname].squeeze().values, dtype=float),
                    "lat": lat,
                    "lon": lon,
                    "attrs": dict(ds[vname].attrs),
                })
            ds.close()
            if fields:
                return fields
        except Exception as exc:
            warnings.warn(f"xarray/cfgrib failed for {path}: {exc}")
    except Exception:
        pass

    # pygrib fallback.
    try:
        import pygrib
        grbs = pygrib.open(str(path))
        try:
            for grb in grbs:
                data = np.asarray(grb.values, dtype=float)
                lat, lon = grb.latlons()
                lon = normalize_lon(lon)
                attrs = {
                    "shortName": getattr(grb, "shortName", None),
                    "name": getattr(grb, "name", None),
                    "units": getattr(grb, "units", None),
                    "stepRange": getattr(grb, "stepRange", None),
                    "forecastTime": getattr(grb, "forecastTime", None),
                }
                fields.append({
                    "name": str(getattr(grb, "shortName", getattr(grb, "name", "unknown"))),
                    "data": data,
                    "lat": lat,
                    "lon": lon,
                    "attrs": attrs,
                })
        finally:
            grbs.close()
        if fields:
            return fields
    except Exception as exc:
        warnings.warn(f"pygrib failed for {path}: {exc}")

    raise RuntimeError(f"Could not read any fields from GRIB file: {path}. Need xarray+cfgrib or pygrib.")


def select_ir_field(fields: list[dict]) -> dict:
    for wanted in ["SBT123", "SBT124"]:
        for f in fields:
            name = str(f.get("name", "")).upper()
            attrs_text = " ".join(str(v).upper() for v in f.get("attrs", {}).values())
            if wanted in name or wanted in attrs_text:
                return f
    return fields[0]


def select_apcp_field(fields: list[dict]) -> dict:
    for f in fields:
        name = str(f.get("name", "")).upper()
        attrs_text = " ".join(str(v).upper() for v in f.get("attrs", {}).values())
        if "APCP" in name or "APCP" in attrs_text or "PRECIP" in attrs_text:
            return f
    return fields[0]


def select_refc_field(fields: list[dict]) -> dict:
    for f in fields:
        name = str(f.get("name", "")).upper()
        attrs_text = " ".join(str(v).upper() for v in f.get("attrs", {}).values())
        if "REFC" in name or "COMPOSITE RADAR REFLECTIVITY" in attrs_text:
            return f
    return fields[0]


def threshold_mask(field, threshold: float, operator: str) -> np.ndarray:
    field = np.asarray(field, dtype=float)
    op = str(operator).strip()
    if op == ">":
        return np.isfinite(field) & (field > float(threshold))
    if op == ">=":
        return np.isfinite(field) & (field >= float(threshold))
    if op == "<":
        return np.isfinite(field) & (field < float(threshold))
    if op == "<=":
        return np.isfinite(field) & (field <= float(threshold))
    raise ValueError(f"Unsupported threshold operator: {operator!r}. Use <, <=, >, or >=.")


def subset_field_to_extent(field, lat, lon, extent=DEFAULT_EXTENT):
    """Crop and mask HRRR grids to one source-independent forecast-domain grid."""
    values = np.asarray(field, dtype=float)
    latitudes = np.asarray(lat, dtype=float)
    longitudes = normalize_lon(np.asarray(lon, dtype=float))
    if latitudes.ndim == 1 and longitudes.ndim == 1:
        longitudes, latitudes = np.meshgrid(longitudes, latitudes)
    if values.shape != latitudes.shape or values.shape != longitudes.shape:
        raise ValueError(
            f"Field/coordinate shape mismatch: values={values.shape} "
            f"lat={latitudes.shape} lon={longitudes.shape}"
        )
    lon_min, lon_max, lat_min, lat_max = map(float, extent)
    inside = (
        np.isfinite(latitudes)
        & np.isfinite(longitudes)
        & (latitudes >= lat_min)
        & (latitudes <= lat_max)
        & (longitudes >= lon_min)
        & (longitudes <= lon_max)
    )
    rows = np.flatnonzero(np.any(inside, axis=1))
    columns = np.flatnonzero(np.any(inside, axis=0))
    if not len(rows) or not len(columns):
        raise ValueError(f"HRRR grid does not intersect requested extent {extent}")
    row_slice = slice(int(rows[0]), int(rows[-1]) + 1)
    column_slice = slice(int(columns[0]), int(columns[-1]) + 1)
    inside = inside[row_slice, column_slice]
    return (
        np.where(inside, values[row_slice, column_slice], np.nan),
        latitudes[row_slice, column_slice],
        longitudes[row_slice, column_slice],
    )


def largest_component(mask: np.ndarray, cell_area_km2: float = HRRR_DEFAULT_CELL_AREA_KM2) -> dict:
    """Return area stats for the single largest contiguous true region.

    The HRRR MCS trigger must be based on one connected cold-cloud object, not
    the domain-wide sum of every pixel colder than the BT threshold.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.size == 0 or not np.any(mask):
        return {
            "max_area_km2": 0.0,
            "n_components": 0,
            "labels": np.zeros_like(mask, dtype=np.int32),
            "largest_label": 0,
            "largest_pixel_count": 0,
        }
    labels, n_components = ndi.label(mask, structure=np.ones((3, 3), dtype=int))
    counts = np.bincount(labels.ravel())
    if len(counts) <= 1:
        return {
            "max_area_km2": 0.0,
            "n_components": int(n_components),
            "labels": labels,
            "largest_label": 0,
            "largest_pixel_count": 0,
        }
    component_counts = counts[1:]
    largest_label = int(np.argmax(component_counts) + 1)
    largest_pixel_count = int(component_counts[largest_label - 1])
    return {
        "max_area_km2": float(largest_pixel_count * float(cell_area_km2)),
        "n_components": int(n_components),
        "labels": labels,
        "largest_label": largest_label,
        "largest_pixel_count": largest_pixel_count,
    }


def parse_step_range(attrs: dict) -> tuple[int, int] | None:
    raw = None
    for key in ["GRIB_stepRange", "stepRange"]:
        if key in attrs and attrs[key] is not None:
            raw = str(attrs[key])
            break
    if raw is None:
        return None
    nums = re.findall(r"\d+", raw)
    if len(nums) == 1:
        end = int(nums[0])
        return max(0, end - 1), end
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
    return None


def infer_apcp_mode(apcp_attrs_by_fhr: dict[int, dict]) -> str:
    parsed = {fhr: parse_step_range(attrs) for fhr, attrs in apcp_attrs_by_fhr.items()}
    parsed_good = {k: v for k, v in parsed.items() if v is not None}
    if parsed_good:
        spans = np.array([end - start for start, end in parsed_good.values()], dtype=float)
        starts = np.array([start for start, end in parsed_good.values()], dtype=float)
        if np.nanmedian(spans) <= 1.5:
            return "hourly_increment"
        if np.mean(starts == 0) >= 0.7:
            return "run_total"
    return "unknown"


def build_qpf6(apcp_by_fhr: dict[int, np.ndarray], apcp_attrs_by_fhr: dict[int, dict], fhr_start: int, fhr_end: int):
    mode = infer_apcp_mode(apcp_attrs_by_fhr)
    log(f"APCP accumulation mode inferred as: {mode}")
    qpf6_by_fhr = {}
    if mode == "run_total":
        for fhr in range(max(int(fhr_start), 6), int(fhr_end) + 1):
            if fhr not in apcp_by_fhr:
                continue
            if (fhr - 6) in apcp_by_fhr:
                qpf6 = apcp_by_fhr[fhr] - apcp_by_fhr[fhr - 6]
            elif fhr == 6:
                qpf6 = apcp_by_fhr[fhr]
            else:
                continue
            qpf6_by_fhr[fhr] = np.maximum(qpf6, 0.0)
    else:
        for fhr in range(max(int(fhr_start), 6), int(fhr_end) + 1):
            needed = list(range(fhr - 5, fhr + 1))
            if not all(k in apcp_by_fhr for k in needed):
                continue
            qpf6_by_fhr[fhr] = np.sum([apcp_by_fhr[k] for k in needed], axis=0)
    return qpf6_by_fhr, mode


def write_mcs_mask_npz(path: Path, mask: np.ndarray, lat: np.ndarray, lon: np.ndarray, bt: np.ndarray | None, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"mask": mask.astype(np.uint8), "lat": lat.astype(np.float32), "lon": lon.astype(np.float32)}
    if bt is not None:
        kwargs["bt"] = bt.astype(np.float32)
    kwargs["meta_json"] = np.array(json.dumps(meta, default=str))
    np.savez_compressed(path, **kwargs)


def read_mcs_mask_npz(path: str | Path):
    z = np.load(Path(path).expanduser())
    mask = np.asarray(z["mask"]).astype(bool)
    lat = np.asarray(z["lat"], dtype=float)
    lon = normalize_lon(np.asarray(z["lon"], dtype=float))
    bt = np.asarray(z["bt"], dtype=float) if "bt" in z.files else None
    meta = json.loads(str(z["meta_json"])) if "meta_json" in z.files else {}
    if lat.ndim == 1 and lon.ndim == 1:
        lon2, lat2 = np.meshgrid(lon, lat)
        lat, lon = lat2, lon2
    return mask, lat, lon, bt, meta


def mcs_object_from_label(label: int, labels: np.ndarray, bt: np.ndarray, lat: np.ndarray, lon: np.ndarray, area_km2: float | None = None, cell_area_km2: float = HRRR_DEFAULT_CELL_AREA_KM2) -> MCSObject | None:
    if label is None or int(label) <= 0:
        return None
    m = labels == int(label)
    if not np.any(m):
        return None
    btv = bt[m]
    lats = lat[m]
    lons = lon[m]
    if area_km2 is None:
        area_km2 = float(np.sum(m) * float(cell_area_km2))
    return MCSObject(
        label=int(label),
        area_km2=float(area_km2),
        pixel_count=int(np.sum(m)),
        min_bt_k=float(np.nanmin(btv)),
        mean_bt_k=float(np.nanmean(btv)),
        lat_min=float(np.nanmin(lats)),
        lat_max=float(np.nanmax(lats)),
        lon_min=float(np.nanmin(lons)),
        lon_max=float(np.nanmax(lons)),
    )


def plot_hrrr_trigger_debug(
    field: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    threshold_mask_in: np.ndarray,
    selected_mask: np.ndarray,
    out_path: str | Path,
    title: str,
    cbar_label: str,
    cmap: str = "gray_r",
    vmin=None,
    vmax=None,
):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_CARTOPY:
        fig = plt.figure(figsize=(16, 10))
        ax = plt.axes(projection=ccrs.PlateCarree())
        transform = ccrs.PlateCarree()
    else:
        fig, ax = plt.subplots(figsize=(16, 10))
        transform = None
    if HAS_CARTOPY:
        pcm = ax.pcolormesh(lon, lat, field, shading="auto", cmap=cmap, vmin=vmin, vmax=vmax, transform=transform)
    else:
        pcm = ax.pcolormesh(lon, lat, field, shading="auto", cmap=cmap, vmin=vmin, vmax=vmax)
    if np.any(threshold_mask_in):
        kwargs = {"levels": [0.5], "colors": "black", "linewidths": 1.8}
        if HAS_CARTOPY:
            kwargs["transform"] = transform
        ax.contour(lon, lat, threshold_mask_in.astype(int), **kwargs)
    if selected_mask is not None and np.any(selected_mask):
        kwargs = {"levels": [0.5], "colors": "magenta", "linewidths": 3.0}
        if HAS_CARTOPY:
            kwargs["transform"] = transform
        ax.contour(lon, lat, selected_mask.astype(int), **kwargs)
    setup_map_ax(ax, DEFAULT_EXTENT)
    ax.set_title(title, fontsize=14)
    cbar = fig.colorbar(pcm, ax=ax, shrink=0.82, pad=0.02)
    cbar.set_label(cbar_label)
    fig.savefig(out_path, dpi=175, bbox_inches="tight")
    plt.close(fig)
    log(f"Saved HRRR trigger debug image: {out_path}")
    return str(out_path)


def read_local_bt_grid(path: str | Path, bt_var: str | None = None, lat_var: str | None = None, lon_var: str | None = None):
    """Read a local NPZ or HRRR GRIB subset as a BT/lat/lon grid."""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".npz":
        z = np.load(path)
        keys = set(z.files)
        btk = bt_var or ("bt" if "bt" in keys else "BT" if "BT" in keys else "brightness_temperature" if "brightness_temperature" in keys else None)
        if btk is None:
            raise RuntimeError(f"NPZ file must contain bt/BT/brightness_temperature or pass --bt-var. Keys={sorted(keys)}")
        latk = lat_var or ("lat" if "lat" in keys else "Lat" if "Lat" in keys else None)
        lonk = lon_var or ("lon" if "lon" in keys else "Lon" if "Lon" in keys else None)
        if latk is None or lonk is None:
            raise RuntimeError(f"NPZ file must contain lat/lon or pass --lat-var/--lon-var. Keys={sorted(keys)}")
        bt = np.asarray(z[btk], dtype=float)
        lat = np.asarray(z[latk], dtype=float)
        lon = normalize_lon(np.asarray(z[lonk], dtype=float))
        if lat.ndim == 1 and lon.ndim == 1:
            lon2, lat2 = np.meshgrid(lon, lat)
            lat, lon = lat2, lon2
        return bt, lat, lon
    fields = read_grib_all_fields(path)
    selected = select_ir_field(fields)
    return selected["data"], selected["lat"], selected["lon"]


def run_hrrr_mcs_detection(args, rp: RuntimePaths) -> tuple[MCSDetectionResult, np.ndarray | None, np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    d = date8(args.date)
    cycle = f"{int(args.hrrr_cycle):02d}"
    fhr_start = int(args.fhr_start)
    fhr_end = int(args.fhr_end)
    cache_dir = Path(args.hrrr_trigger_cache_dir).expanduser() if args.hrrr_trigger_cache_dir else (rp.cache_dir / "hrrr_mcs_trigger_inputs" / f"{d}_{cycle}z")
    cache_dir.mkdir(parents=True, exist_ok=True)
    summary_path = cache_dir / f"hrrr_mcs_trigger_summary_{d}_{cycle}z.json"
    mask_path = rp.outdir / (
        f"hrrr_mcs_mask_{d}_{cycle}z_bt{args.bt_threshold_operator}"
        f"{str(args.bt_threshold_k).replace('.','p')}_area{int(args.min_mcs_area_km2)}"
        f"_cloud{int(args.mcs_cloud_duration_hours)}h"
        f"_structure{int(args.mcs_structural_duration_hours)}h"
        f"_pfaxis{int(args.precipitation_major_axis_km)}"
        f"_convective{int(args.convective_threshold_dbz)}.npz"
    )

    summary = {
        "run_date": d,
        "hrrr_cycle": cycle,
        "fhr_start": fhr_start,
        "fhr_end": fhr_end,
        "bbox": extent_dict(args.extent),
        "ir_bt_threshold_k": float(args.bt_threshold_k),
        "ir_threshold_operator": str(args.bt_threshold_operator),
        "ir_area_threshold_km2": float(args.min_mcs_area_km2),
        "ir_duration_threshold_hours": int(args.mcs_cloud_duration_hours),
        "structural_duration_threshold_hours": int(args.mcs_structural_duration_hours),
        "track_overlap_threshold": float(args.track_overlap_threshold),
        "precipitation_threshold_dbz": float(args.precipitation_threshold_dbz),
        "precipitation_major_axis_threshold_km": float(args.precipitation_major_axis_km),
        "convective_threshold_dbz": float(args.convective_threshold_dbz),
        "qpf6_threshold_mm": float(args.qpf6_threshold_mm),
        "qpf6_area_threshold_km2": float(args.qpf6_area_threshold_km2),
        "cell_area_km2_used": float(args.hrrr_cell_area_km2),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "ir_records": [],
        "reflectivity_records": [],
        "qpf6_records": [],
        "download_errors": [],
    }

    log("================================================================================")
    log("HRRR MCS trigger detection")
    log("================================================================================")
    log(f"Date={d} HRRR cycle={cycle}Z fhr={fhr_start:02d}-{fhr_end:02d}")
    log(
        f"Actual PyFLEXTRKR trigger: SBT {args.bt_threshold_operator} {float(args.bt_threshold_k):.1f} K, "
        f"cloud shield > {float(args.min_mcs_area_km2):.0f} km^2, "
        f">= {float(args.precipitation_threshold_dbz):.0f} dBZ PF major axis > "
        f"{float(args.precipitation_major_axis_km):.0f} km, "
        f"convective REFC > {float(args.convective_threshold_dbz):.0f} dBZ, "
        f"cloud duration >= {int(args.mcs_cloud_duration_hours)} h, "
        f"PF/convective duration >= {int(args.mcs_structural_duration_hours)} h"
    )
    log(f"Example HRRR SBT URL: {hrrr_nomads_url(d, cycle, fhr_start, ['SBT123', 'SBT124'], 'lev_top_of_atmosphere', extent=args.extent)}", verbose_only=True)

    ir_by_fhr: dict[int, np.ndarray] = {}
    ir_name_by_fhr: dict[int, str] = {}
    lat = None
    lon = None

    # Optional single local HRRR SBT subset or NPZ for testing. Normal automation uses NOMADS f00-f24.
    if args.ir_path:
        bt, lat, lon = read_local_bt_grid(args.ir_path, bt_var=args.bt_var, lat_var=args.lat_var, lon_var=args.lon_var)
        ir_by_fhr[fhr_start] = bt
        ir_name_by_fhr[fhr_start] = Path(args.ir_path).name
        log(f"Using local HRRR/BT file for detection: {args.ir_path}")
    else:
        try:
            import requests
        except Exception as exc:
            raise RuntimeError("requests is required for automatic HRRR/NOMADS trigger downloads.") from exc
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 hrrr-mcs-trigger-ml-plotter/1.0"})
        for fhr in range(fhr_start, fhr_end + 1):
            try:
                path, url = download_hrrr_ir_subset(session, d, cycle, fhr, cache_dir, args)
                fields = read_grib_all_fields(path)
                selected = select_ir_field(fields)
                data = np.asarray(selected["data"], dtype=float)
                if data.ndim != 2:
                    data = np.squeeze(data)
                if data.ndim != 2:
                    raise RuntimeError(f"Selected IR field has shape {data.shape}; expected 2D.")
                field_lat = selected["lat"]
                field_lon = selected["lon"]
                data, field_lat, field_lon = subset_field_to_extent(
                    data, field_lat, field_lon, args.extent
                )
                ir_by_fhr[fhr] = data
                ir_name_by_fhr[fhr] = str(selected.get("name", "SBT"))
                if lat is None:
                    lat = field_lat
                    lon = field_lon
                vlog(f"Selected HRRR IR field f{fhr:02d}: {selected.get('name')} valid={hrrr_valid_time_utc(d, cycle, fhr).isoformat()}")
            except Exception as exc:
                log(f"WARNING: HRRR IR failed for f{fhr:02d}: {exc}")
                summary["download_errors"].append({"field": "SBT123/SBT124", "fhr": int(fhr), "error": str(exc)})

    log(f"HRRR SBT fields ready: {len(ir_by_fhr)}/{fhr_end - fhr_start + 1} forecast hours ({cycle}Z f{fhr_start:02d}-f{fhr_end:02d})")

    if not ir_by_fhr:
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        raise RuntimeError("No HRRR SBT123/SBT124 fields were successfully downloaded/read for MCS detection.")

    reflectivity_by_fhr: dict[int, np.ndarray] = {}
    reflectivity_name_by_fhr: dict[int, str] = {}
    try:
        import requests
    except Exception as exc:
        raise RuntimeError("requests is required for HRRR reflectivity downloads.") from exc
    reflectivity_session = requests.Session()
    reflectivity_session.headers.update({"User-Agent": "Mozilla/5.0 hrrr-mcs-trigger-ml-plotter/1.0"})
    for fhr in range(fhr_start, fhr_end + 1):
        try:
            path, url = download_hrrr_refc_subset(
                reflectivity_session, d, cycle, fhr, cache_dir, args
            )
            fields = read_grib_all_fields(path)
            selected = select_refc_field(fields)
            data = np.asarray(selected["data"], dtype=float).squeeze()
            if data.ndim != 2:
                raise RuntimeError(f"Selected REFC field has shape {data.shape}; expected 2D.")
            data, _, _ = subset_field_to_extent(
                data, selected["lat"], selected["lon"], args.extent
            )
            reflectivity_by_fhr[fhr] = data
            reflectivity_name_by_fhr[fhr] = str(selected.get("name", "REFC"))
        except Exception as exc:
            log(f"WARNING: HRRR REFC failed for f{fhr:02d}: {exc}")
            summary["download_errors"].append({"field": "REFC", "fhr": int(fhr), "error": str(exc)})
    log(
        f"HRRR REFC fields ready: {len(reflectivity_by_fhr)}/"
        f"{fhr_end - fhr_start + 1} forecast hours ({cycle}Z f{fhr_start:02d}-f{fhr_end:02d})"
    )

    best_ir = None
    for fhr, bt in ir_by_fhr.items():
        mask_all = threshold_mask(bt, args.bt_threshold_k, args.bt_threshold_operator)
        comp = largest_component(mask_all, cell_area_km2=args.hrrr_cell_area_km2)
        rec = {
            "fhr": int(fhr),
            "valid_utc": hrrr_valid_time_utc(d, cycle, fhr).isoformat(),
            "ir_field": ir_name_by_fhr.get(fhr),
            "min_bt_k": float(np.nanmin(bt)),
            "max_bt_k": float(np.nanmax(bt)),
            "threshold_area_total_km2": float(np.sum(mask_all) * float(args.hrrr_cell_area_km2)),
            "max_ir_component_area_km2": float(comp["max_area_km2"]),
            "n_ir_components": int(comp["n_components"]),
            "trigger": bool(comp["max_area_km2"] > float(args.min_mcs_area_km2)),
        }
        summary["ir_records"].append(rec)
        if best_ir is None or rec["max_ir_component_area_km2"] > best_ir["record"]["max_ir_component_area_km2"]:
            best_ir = {"fhr": fhr, "field": bt, "mask_all": mask_all, "component": comp, "record": rec}

    pyflex = prepare_and_run_pyflextrkr(
        ir_by_fhr,
        reflectivity_by_fhr,
        lat,
        lon,
        run_date=d,
        cycle=cycle,
        case_dir=cache_dir,
        extent=tuple(args.extent),
        bt_threshold_k=float(args.bt_threshold_k),
        cloud_area_threshold_km2=float(args.min_mcs_area_km2),
        precipitation_threshold_dbz=float(args.precipitation_threshold_dbz),
        precipitation_major_axis_threshold_km=float(args.precipitation_major_axis_km),
        convective_threshold_dbz=float(args.convective_threshold_dbz),
        cloud_duration_hours=int(args.mcs_cloud_duration_hours),
        structural_duration_hours=int(args.mcs_structural_duration_hours),
        overlap_threshold=float(args.track_overlap_threshold),
        cell_area_km2=float(args.hrrr_cell_area_km2),
        force=bool(args.force_pyflextrkr or args.force_hrrr_download),
        source_model="HRRR",
        ir_required=True,
    )
    summary["reflectivity_records"] = [
        {
            "fhr": int(fhr),
            "valid_utc": hrrr_valid_time_utc(d, cycle, fhr).isoformat(),
            "reflectivity_field": reflectivity_name_by_fhr.get(fhr),
            "max_reflectivity_dbz": float(np.nanmax(reflectivity_by_fhr[fhr])),
            "pyflextrkr_input_available": True,
        }
        for fhr in sorted(reflectivity_by_fhr)
    ]
    summary["mcs_method"] = "actual_pyflextrkr"
    summary["pyflextrkr_package_version"] = pyflex.package_version
    summary["pyflextrkr_result_path"] = pyflex.result_path
    summary["pyflextrkr_config_path"] = pyflex.config_path
    summary["pyflextrkr_input_manifest_path"] = pyflex.input_manifest_path
    summary["pyflextrkr_robust_stats_path"] = pyflex.robust_stats_path
    summary["pyflextrkr_official_steps_completed"] = pyflex.official_steps_completed
    summary["pyflextrkr_upstream_commit"] = pyflex.upstream_commit
    summary["selected_fhr"] = pyflex.selected_fhr
    summary["max_ir_duration_hours"] = int(pyflex.max_ir_duration_hours)
    summary["max_joint_duration_hours"] = int(pyflex.max_joint_duration_hours)
    summary["ir_duration_met"] = bool(pyflex.ir_duration_met)
    summary["structural_duration_met"] = bool(pyflex.structural_duration_met)
    ir_trigger = bool(pyflex.ir_duration_met)

    # Optional HRRR 6-h QPF trigger/diagnostic. This is off by default so the MCS trigger is the SBT object.
    qpf_trigger = False
    qpf_threshold_met = False
    best_qpf = None
    if args.include_qpf_trigger or args.include_qpf_debug:
        try:
            import requests
        except Exception as exc:
            raise RuntimeError("requests is required for HRRR APCP/QPF downloads.") from exc
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 hrrr-mcs-trigger-ml-plotter/1.0"})
        apcp_by_fhr = {}
        apcp_attrs_by_fhr = {}
        for fhr in range(0, fhr_end + 1):
            try:
                path, url = download_hrrr_apcp_subset(session, d, cycle, fhr, cache_dir, args)
                fields = read_grib_all_fields(path)
                selected = select_apcp_field(fields)
                data = np.asarray(selected["data"], dtype=float)
                if data.ndim != 2:
                    data = np.squeeze(data)
                if data.ndim != 2:
                    raise RuntimeError(f"Selected APCP field has shape {data.shape}; expected 2D.")
                data, _, _ = subset_field_to_extent(
                    data, selected["lat"], selected["lon"], args.extent
                )
                apcp_by_fhr[fhr] = data
                apcp_attrs_by_fhr[fhr] = selected.get("attrs", {})
                vlog(f"Selected HRRR APCP field f{fhr:02d}: {selected.get('name')}")
            except Exception as exc:
                log(f"WARNING: HRRR APCP failed for f{fhr:02d}: {exc}")
                summary["download_errors"].append({"field": "APCP", "fhr": int(fhr), "error": str(exc)})
        if apcp_by_fhr:
            qpf6_by_fhr, apcp_mode = build_qpf6(apcp_by_fhr, apcp_attrs_by_fhr, fhr_start, fhr_end)
            summary["apcp_accumulation_mode"] = apcp_mode
            for fhr, qpf6 in qpf6_by_fhr.items():
                mask_qpf = np.isfinite(qpf6) & (qpf6 >= float(args.qpf6_threshold_mm))
                comp = largest_component(mask_qpf, cell_area_km2=args.hrrr_cell_area_km2)
                rec = {
                    "fhr": int(fhr),
                    "valid_utc": hrrr_valid_time_utc(d, cycle, fhr).isoformat(),
                    "max_qpf6_mm": float(np.nanmax(qpf6)),
                    "threshold_area_total_km2": float(np.sum(mask_qpf) * float(args.hrrr_cell_area_km2)),
                    "max_qpf6_component_area_km2": float(comp["max_area_km2"]),
                    "n_qpf6_components": int(comp["n_components"]),
                    "trigger": bool(comp["max_area_km2"] >= float(args.qpf6_area_threshold_km2) and np.nanmax(qpf6) >= float(args.qpf6_threshold_mm)),
                }
                summary["qpf6_records"].append(rec)
                if best_qpf is None or rec["max_qpf6_component_area_km2"] > best_qpf["record"]["max_qpf6_component_area_km2"]:
                    best_qpf = {"fhr": fhr, "field": qpf6, "mask": mask_qpf, "component": comp, "record": rec}
            qpf_threshold_met = bool(any(r["trigger"] for r in summary["qpf6_records"]))
            qpf_trigger = bool(args.include_qpf_trigger and qpf_threshold_met)

    selected_mask = np.zeros_like(best_ir["mask_all"], dtype=bool)
    selected_obj = None
    selected_fhr = pyflex.selected_fhr
    if selected_fhr in ir_by_fhr:
        # This mask is a plotting aid only. The eligibility decision above is
        # made exclusively by the official PyFLEXTRKR robust-MCS output.
        selected_mask = threshold_mask(
            ir_by_fhr[selected_fhr], args.bt_threshold_k, args.bt_threshold_operator
        )
        selected_obj = mcs_object_from_label(
            1,
            selected_mask.astype(np.int32),
            ir_by_fhr[selected_fhr],
            lat,
            lon,
            area_km2=float(np.sum(selected_mask) * float(args.hrrr_cell_area_km2)),
            cell_area_km2=args.hrrr_cell_area_km2,
        )

    # QPF is diagnostic only. A forecast is eligible only when the tracked IR
    # shield and collocated simulated-reflectivity lifecycle both qualify.
    triggered = bool(pyflex.detected)
    summary["ir_trigger"] = bool(ir_trigger)
    summary["qpf6_trigger"] = bool(qpf_trigger)
    summary["qpf6_threshold_met"] = bool(qpf_threshold_met)
    summary["mcs_detected"] = bool(triggered)
    summary["best_ir"] = best_ir["record"] if best_ir is not None else None
    summary["best_qpf6"] = best_qpf["record"] if best_qpf is not None else None

    ir_debug = None
    debug_fhr = selected_fhr if selected_fhr in ir_by_fhr else (best_ir["fhr"] if best_ir else None)
    if debug_fhr is not None and args.save_hrrr_debug_plots:
        debug_bt = ir_by_fhr[debug_fhr]
        debug_mask_all = threshold_mask(debug_bt, args.bt_threshold_k, args.bt_threshold_operator)
        valid_str = hrrr_valid_time_utc(d, cycle, debug_fhr).strftime("%Y-%m-%d %HZ")
        ir_debug = cache_dir / f"hrrr_{d}_{cycle}z_f{debug_fhr:02d}_simulated_ir_trigger_debug.png"
        plot_hrrr_trigger_debug(
            field=debug_bt,
            lat=lat,
            lon=lon,
            threshold_mask_in=debug_mask_all,
            selected_mask=selected_mask,
            out_path=ir_debug,
            title=(
                f"HRRR simulated IR lifecycle | {d} {cycle}Z f{debug_fhr:02d} | valid {valid_str}\n"
                f"SBT {args.bt_threshold_operator} {float(args.bt_threshold_k):.0f} K | "
                f"PyFLEXTRKR duration = {pyflex.max_ir_duration_hours} h"
            ),
            cbar_label="Brightness temperature (K)",
            cmap="gray_r",
            vmin=190,
            vmax=320,
        )
        summary["best_ir_debug_image"] = str(ir_debug)

    reflectivity_debug = None
    if debug_fhr in reflectivity_by_fhr and args.save_hrrr_debug_plots:
        reflectivity = reflectivity_by_fhr[debug_fhr]
        reflectivity_mask = np.isfinite(reflectivity) & (
            reflectivity > float(args.convective_threshold_dbz)
        )
        reflectivity_debug = cache_dir / (
            f"hrrr_{d}_{cycle}z_f{debug_fhr:02d}_simulated_reflectivity_trigger_debug.png"
        )
        plot_hrrr_trigger_debug(
            field=reflectivity,
            lat=lat,
            lon=lon,
            threshold_mask_in=reflectivity_mask,
            selected_mask=selected_mask & reflectivity_mask,
            out_path=reflectivity_debug,
            title=(
                f"HRRR simulated reflectivity lifecycle | {d} {cycle}Z f{debug_fhr:02d} | valid {valid_str}\n"
                f"REFC > {float(args.convective_threshold_dbz):.0f} dBZ | "
                f"PyFLEXTRKR robust duration = {pyflex.max_joint_duration_hours} h"
            ),
            cbar_label="Composite reflectivity (dBZ)",
            cmap="turbo",
            vmin=-10,
            vmax=70,
        )
        summary["best_reflectivity_debug_image"] = str(reflectivity_debug)

    qpf_debug = None
    if best_qpf is not None and args.save_hrrr_debug_plots:
        valid_str = hrrr_valid_time_utc(d, cycle, best_qpf["fhr"]).strftime("%Y-%m-%d %HZ")
        qpf_debug = cache_dir / f"hrrr_{d}_{cycle}z_f{best_qpf['fhr']:02d}_qpf6_trigger_debug.png"
        qpf_vmax = max(60.0, float(np.nanpercentile(best_qpf["field"], 99.5)))
        qpf_largest_label = int(best_qpf["component"].get("largest_label", 0))
        qpf_selected_mask = (best_qpf["component"]["labels"] == qpf_largest_label) if qpf_largest_label > 0 else np.zeros_like(best_qpf["mask"], dtype=bool)
        plot_hrrr_trigger_debug(
            field=best_qpf["field"],
            lat=lat,
            lon=lon,
            threshold_mask_in=best_qpf["mask"],
            selected_mask=qpf_selected_mask,
            out_path=qpf_debug,
            title=(
                f"HRRR 6-h QPF trigger | {d} {cycle}Z ending f{best_qpf['fhr']:02d} | valid {valid_str}\n"
                f"6-h QPF >= {float(args.qpf6_threshold_mm):.1f} mm | "
                f"largest component = {best_qpf['record']['max_qpf6_component_area_km2']:.0f} km$^2$"
            ),
            cbar_label="6-h QPF (mm)",
            cmap="turbo",
            vmin=0,
            vmax=qpf_vmax,
        )
        summary["best_qpf6_debug_image"] = str(qpf_debug)

    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    log(f"Saved HRRR trigger summary JSON: {summary_path}")
    write_mcs_mask_npz(mask_path, selected_mask, lat, lon, best_ir["field"], {"summary": summary})

    n_passing = int(pyflex.max_joint_duration_hours)
    res = MCSDetectionResult(
        triggered=triggered,
        threshold_k=float(args.bt_threshold_k),
        min_area_km2=float(args.min_mcs_area_km2),
        n_objects_total=int(best_ir["component"].get("n_components", 0)) if best_ir else 0,
        n_objects_passing=n_passing,
        largest_area_km2=float(best_ir["record"].get("max_ir_component_area_km2", 0.0)) if best_ir else 0.0,
        selected=selected_obj,
        mask_path=str(mask_path),
        source="Actual PyFLEXTRKR using HRRR SBT and REFC",
        hrrr_cycle=cycle,
        selected_fhr=int(selected_fhr) if selected_fhr is not None else None,
        selected_valid_utc=(
            hrrr_valid_time_utc(d, cycle, selected_fhr).isoformat()
            if selected_fhr is not None
            else None
        ),
        ir_trigger=bool(ir_trigger),
        ir_duration_met=bool(pyflex.ir_duration_met),
        structural_duration_met=bool(pyflex.structural_duration_met),
        max_ir_duration_hours=int(pyflex.max_ir_duration_hours),
        max_joint_duration_hours=int(pyflex.max_joint_duration_hours),
        qpf6_trigger=bool(qpf_trigger),
        qpf6_threshold_met=bool(qpf_threshold_met),
        mcs_method="actual_pyflextrkr",
        pyflextrkr_package_version=pyflex.package_version,
        pyflextrkr_upstream_commit=pyflex.upstream_commit,
        pyflextrkr_result_path=pyflex.result_path,
        pyflextrkr_config_path=pyflex.config_path,
        pyflextrkr_input_manifest_path=pyflex.input_manifest_path,
        pyflextrkr_official_steps_completed=pyflex.official_steps_completed,
        summary_path=str(summary_path),
        ir_debug_image=str(ir_debug) if ir_debug else None,
        qpf6_debug_image=str(qpf_debug) if qpf_debug else None,
    )
    log(
        f"Actual PyFLEXTRKR result (v{pyflex.package_version}): "
        f"ir_duration_met={pyflex.ir_duration_met} "
        f"structural_duration_met={pyflex.structural_duration_met} "
        f"qpf6_threshold_met={qpf_threshold_met} triggered={triggered} "
        f"max_ir_duration={pyflex.max_ir_duration_hours}h "
        f"max_joint_duration={pyflex.max_joint_duration_hours}h"
    )
    return res, selected_mask, lat, lon, ir_by_fhr.get(debug_fhr)


def run_rap_mcs_detection(args, rp: RuntimePaths) -> dict:
    """Legacy RAP research diagnostic; not called by the operational HRRR gate."""
    d = date8(args.date)
    cycle = f"{int(args.rap_cycle):02d}"
    fhr_start = int(args.rap_fhr_start)
    fhr_end = int(args.rap_fhr_end)
    expected_fhrs = list(range(fhr_start, fhr_end + 1))
    cache_dir = (
        Path(args.rap_trigger_cache_dir).expanduser()
        if args.rap_trigger_cache_dir
        else rp.cache_dir / "rap_mcs_trigger_inputs" / f"{d}_{cycle}z"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    summary_path = cache_dir / f"rap_mcs_trigger_summary_{d}_{cycle}z.json"
    summary = {
        "run_date": d,
        "rap_cycle": cycle,
        "fhr_start": fhr_start,
        "fhr_end": fhr_end,
        "valid_start_utc": hrrr_valid_time_utc(d, cycle, fhr_start).isoformat(),
        "valid_end_utc": hrrr_valid_time_utc(d, cycle, fhr_end).isoformat(),
        "bbox": extent_dict(args.extent),
        "ir_area_threshold_km2": float(args.min_mcs_area_km2),
        "ir_duration_threshold_hours": int(args.mcs_cloud_duration_hours),
        "structural_duration_threshold_hours": int(args.rap_structural_duration_hours),
        "precipitation_threshold_dbz": float(args.precipitation_threshold_dbz),
        "precipitation_major_axis_threshold_km": float(args.precipitation_major_axis_km),
        "convective_threshold_dbz": float(args.convective_threshold_dbz),
        "cell_area_km2_used": float(args.rap_cell_area_km2),
        "ir_records": [],
        "reflectivity_records": [],
        "download_errors": [],
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    log("================================================================================")
    log("RAP MCS trigger detection")
    log("================================================================================")
    log(
        f"Date={d} RAP cycle={cycle}Z fhr={fhr_start:02d}-{fhr_end:02d}; "
        "valid window must match HRRR 12Z f00-f24"
    )

    try:
        import requests
    except Exception as exc:
        raise RuntimeError("requests is required for RAP MCS-gate downloads") from exc
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 xgbffp-rap-mcs-gate/1.0"})

    reflectivity_by_fhr: dict[int, np.ndarray] = {}
    ir_by_fhr: dict[int, np.ndarray] = {}
    lat = None
    lon = None
    ir_absent_fhrs: list[int] = []
    ir_other_errors: list[tuple[int, Exception]] = []

    for fhr in expected_fhrs:
        try:
            path, _ = download_rap_field_subset(
                session, d, cycle, fhr, cache_dir, "REFC", args
            )
            selected = select_refc_field(read_grib_all_fields(path))
            data, field_lat, field_lon = subset_field_to_extent(
                np.asarray(selected["data"], dtype=float).squeeze(),
                selected["lat"],
                selected["lon"],
                args.extent,
            )
            if data.ndim != 2:
                raise RuntimeError(f"RAP REFC f{fhr:02d} has non-2D shape {data.shape}")
            reflectivity_by_fhr[fhr] = data
            if lat is None:
                lat, lon = field_lat, field_lon
            summary["reflectivity_records"].append({
                "fhr": fhr,
                "valid_utc": hrrr_valid_time_utc(d, cycle, fhr).isoformat(),
                "max_reflectivity_dbz": float(np.nanmax(data)),
            })
        except Exception as exc:
            summary["download_errors"].append(
                {"field": "REFC", "fhr": fhr, "error": str(exc)}
            )

        try:
            path, _ = download_rap_field_subset(
                session, d, cycle, fhr, cache_dir, "SBT", args
            )
            selected = select_ir_field(read_grib_all_fields(path))
            data, _, _ = subset_field_to_extent(
                np.asarray(selected["data"], dtype=float).squeeze(),
                selected["lat"],
                selected["lon"],
                args.extent,
            )
            if data.ndim != 2:
                raise RuntimeError(f"RAP SBT f{fhr:02d} has non-2D shape {data.shape}")
            ir_by_fhr[fhr] = data
        except Exception as exc:
            if "is absent from" in str(exc):
                ir_absent_fhrs.append(fhr)
            else:
                ir_other_errors.append((fhr, exc))
            summary["download_errors"].append(
                {"field": "SBT123/SBT124", "fhr": fhr, "error": str(exc)}
            )

    missing_refc = sorted(set(expected_fhrs) - set(reflectivity_by_fhr))
    if missing_refc:
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        raise RuntimeError(
            "RAP structural gate is incomplete; missing REFC hours: "
            + ", ".join(f"f{fhr:02d}" for fhr in missing_refc)
        )
    if ir_other_errors:
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        raise RuntimeError(
            "RAP SBT could not be evaluated because of download/read failures: "
            + "; ".join(f"f{fhr:02d}: {exc}" for fhr, exc in ir_other_errors[:5])
        )
    if ir_by_fhr and len(ir_by_fhr) != len(expected_fhrs):
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        raise RuntimeError(
            "RAP SBT is only partially available; refusing to bypass the IR criterion"
        )

    ir_available = len(ir_by_fhr) == len(expected_fhrs)
    if not ir_available and len(ir_absent_fhrs) != len(expected_fhrs):
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        raise RuntimeError("RAP SBT availability could not be determined safely")
    summary["ir_available"] = ir_available
    summary["ir_required"] = ir_available
    summary["ir_requirement_mode"] = (
        "full_simulated_satellite" if ir_available else "excluded_field_absent"
    )

    if ir_available:
        for fhr in expected_fhrs:
            bt = ir_by_fhr[fhr]
            comp = largest_component(
                threshold_mask(bt, args.bt_threshold_k, args.bt_threshold_operator),
                cell_area_km2=args.rap_cell_area_km2,
            )
            summary["ir_records"].append({
                "fhr": fhr,
                "valid_utc": hrrr_valid_time_utc(d, cycle, fhr).isoformat(),
                "min_bt_k": float(np.nanmin(bt)),
                "max_ir_component_area_km2": float(comp["max_area_km2"]),
            })

    pyflex = prepare_and_run_pyflextrkr(
        ir_by_fhr,
        reflectivity_by_fhr,
        lat,
        lon,
        run_date=d,
        cycle=cycle,
        case_dir=cache_dir,
        extent=tuple(args.extent),
        bt_threshold_k=float(args.bt_threshold_k),
        cloud_area_threshold_km2=float(args.min_mcs_area_km2),
        precipitation_threshold_dbz=float(args.precipitation_threshold_dbz),
        precipitation_major_axis_threshold_km=float(args.precipitation_major_axis_km),
        convective_threshold_dbz=float(args.convective_threshold_dbz),
        cloud_duration_hours=int(args.mcs_cloud_duration_hours),
        structural_duration_hours=int(args.rap_structural_duration_hours),
        overlap_threshold=float(args.track_overlap_threshold),
        cell_area_km2=float(args.rap_cell_area_km2),
        force=bool(args.force_pyflextrkr or args.force_rap_download),
        source_model="RAP",
        ir_required=ir_available,
    )
    rap_triggered = bool(
        pyflex.structural_duration_met
        and (pyflex.ir_duration_met if ir_available else True)
    )
    summary.update({
        "mcs_method": "actual_pyflextrkr",
        "mcs_detected": rap_triggered,
        "ir_duration_met": bool(pyflex.ir_duration_met) if ir_available else None,
        "structural_duration_met": bool(pyflex.structural_duration_met),
        "max_ir_duration_hours": int(pyflex.max_ir_duration_hours) if ir_available else None,
        "max_joint_duration_hours": int(pyflex.max_joint_duration_hours),
        "selected_fhr": pyflex.selected_fhr,
        "pyflextrkr_package_version": pyflex.package_version,
        "pyflextrkr_upstream_commit": pyflex.upstream_commit,
        "pyflextrkr_result_path": pyflex.result_path,
        "pyflextrkr_config_path": pyflex.config_path,
        "pyflextrkr_input_manifest_path": pyflex.input_manifest_path,
        "pyflextrkr_robust_stats_path": pyflex.robust_stats_path,
        "pyflextrkr_official_steps_completed": pyflex.official_steps_completed,
    })
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    log(
        f"RAP PyFLEXTRKR result: ir_available={ir_available} "
        f"ir_duration_met={summary['ir_duration_met']} "
        f"structural_duration_met={pyflex.structural_duration_met} "
        f"triggered={rap_triggered}"
    )
    return summary


def finalize_hrrr_only_gate(
    hrrr_result: MCSDetectionResult,
    mask: np.ndarray | None,
    lat: np.ndarray | None,
    lon: np.ndarray | None,
    bt: np.ndarray | None,
) -> MCSDetectionResult:
    """Use HRRR as the sole MCS gate and persist child-safe metadata."""
    hrrr_triggered = bool(hrrr_result.triggered)
    model_results = {
        "hrrr": {
            "triggered": hrrr_triggered,
            "ir_available": True,
            "ir_required": True,
            "ir_duration_met": hrrr_result.ir_duration_met,
            "structural_duration_met": hrrr_result.structural_duration_met,
            "max_ir_duration_hours": hrrr_result.max_ir_duration_hours,
            "max_joint_duration_hours": hrrr_result.max_joint_duration_hours,
            "cycle": hrrr_result.hrrr_cycle,
        },
    }
    hrrr_result.triggered = hrrr_triggered
    hrrr_result.hrrr_triggered = hrrr_triggered
    hrrr_result.model_results = model_results
    hrrr_result.source = "HRRR-only actual PyFLEXTRKR gate using SBT and REFC"

    if hrrr_result.summary_path:
        summary_path = Path(hrrr_result.summary_path)
        summary = json.loads(summary_path.read_text())
        summary["hrrr_mcs_detected"] = hrrr_triggered
        summary.pop("rap_mcs_detection", None)
        summary.pop("dual_model_gate", None)
        summary["mcs_gate"] = {
            "required_models": ["HRRR"],
            "hrrr_triggered": hrrr_triggered,
            "mcs_detected": hrrr_triggered,
        }
        summary["mcs_detected"] = hrrr_triggered
        summary_path.write_text(json.dumps(summary, indent=2, default=str))
        if hrrr_result.mask_path and mask is not None and lat is not None and lon is not None:
            write_mcs_mask_npz(
                Path(hrrr_result.mask_path), mask, lat, lon, bt, {"summary": summary}
            )
    return hrrr_result


def get_mcs_detection(args, rp: RuntimePaths):
    if args.mcs_mask_path:
        mask, lat, lon, bt, meta = read_mcs_mask_npz(args.mcs_mask_path)
        lifecycle_summary = meta.get("summary", {}) if isinstance(meta, dict) else {}
        area = float(np.sum(mask) * float(args.hrrr_cell_area_km2)) if mask.any() else 0.0
        obj = None
        if mask.any():
            obj = MCSObject(
                label=1,
                area_km2=area,
                pixel_count=int(mask.sum()),
                min_bt_k=float(np.nanmin(bt[mask])) if bt is not None else float("nan"),
                mean_bt_k=float(np.nanmean(bt[mask])) if bt is not None else float("nan"),
                lat_min=float(np.nanmin(lat[mask])), lat_max=float(np.nanmax(lat[mask])),
                lon_min=float(np.nanmin(lon[mask])), lon_max=float(np.nanmax(lon[mask])),
            )
        res = MCSDetectionResult(
            triggered=bool(
                (
                    lifecycle_summary.get("mcs_method") == "actual_pyflextrkr"
                    and lifecycle_summary.get("mcs_detected") is True
                )
                or args.force_trigger
            ),
            threshold_k=float(args.bt_threshold_k),
            min_area_km2=float(args.min_mcs_area_km2),
            n_objects_total=1 if mask.any() else 0,
            n_objects_passing=1 if area > args.min_mcs_area_km2 else 0,
            largest_area_km2=area,
            selected=obj,
            mask_path=str(args.mcs_mask_path),
            source="saved PyFLEXTRKR-qualified HRRR mask",
            hrrr_cycle=lifecycle_summary.get("hrrr_cycle"),
            selected_fhr=lifecycle_summary.get(
                "selected_fhr",
                lifecycle_summary.get("best_lifecycle_track", {}).get("fhr"),
            ),
            ir_trigger=lifecycle_summary.get("ir_trigger"),
            ir_duration_met=lifecycle_summary.get("ir_duration_met"),
            structural_duration_met=lifecycle_summary.get("structural_duration_met"),
            max_ir_duration_hours=lifecycle_summary.get("max_ir_duration_hours"),
            max_joint_duration_hours=lifecycle_summary.get("max_joint_duration_hours"),
            qpf6_trigger=lifecycle_summary.get("qpf6_trigger"),
            qpf6_threshold_met=lifecycle_summary.get("qpf6_threshold_met"),
            mcs_method=lifecycle_summary.get("mcs_method"),
            pyflextrkr_package_version=lifecycle_summary.get("pyflextrkr_package_version"),
            pyflextrkr_upstream_commit=lifecycle_summary.get("pyflextrkr_upstream_commit"),
            pyflextrkr_result_path=lifecycle_summary.get("pyflextrkr_result_path"),
            pyflextrkr_config_path=lifecycle_summary.get("pyflextrkr_config_path"),
            pyflextrkr_input_manifest_path=lifecycle_summary.get("pyflextrkr_input_manifest_path"),
            pyflextrkr_official_steps_completed=lifecycle_summary.get("pyflextrkr_official_steps_completed"),
            hrrr_triggered=(lifecycle_summary.get("mcs_gate") or {}).get(
                "hrrr_triggered",
                lifecycle_summary.get("hrrr_mcs_detected", lifecycle_summary.get("mcs_detected")),
            ),
            model_results={
                "hrrr": {
                    "triggered": (lifecycle_summary.get("mcs_gate") or {}).get(
                        "hrrr_triggered",
                        lifecycle_summary.get("hrrr_mcs_detected", lifecycle_summary.get("mcs_detected")),
                    ),
                    "ir_duration_met": lifecycle_summary.get("ir_duration_met"),
                    "structural_duration_met": lifecycle_summary.get("structural_duration_met"),
                },
            },
        )
        return res, mask, lat, lon, bt
    if args.force_trigger and not args.ir_path and not args.run_hrrr_detector:
        res = MCSDetectionResult(True, float(args.bt_threshold_k), float(args.min_mcs_area_km2), 0, 0, 0.0, None, None, source="force-trigger")
        return res, None, None, None, None
    if not args.run_hrrr_detector and not args.ir_path:
        raise RuntimeError("No MCS source supplied. Use default --run-hrrr-detector, pass --mcs-mask-path, or use --force-trigger.")
    hrrr_result, mask, lat, lon, bt = run_hrrr_mcs_detection(args, rp)
    finalized = finalize_hrrr_only_gate(hrrr_result, mask, lat, lon, bt)
    return finalized, mask, lat, lon, bt



# ======================================================================================
# Plotting
# ======================================================================================


def risk_category_labels(values):
    v = np.asarray(values, dtype=float)
    labels = np.full(v.shape, "<5%", dtype=object)
    labels[v >= 0.05] = ">5%"
    labels[v >= 0.15] = ">15%"
    labels[v >= 0.40] = ">40%"
    labels[v >= 0.70] = ">70%"
    return labels


def setup_map_ax(ax, extent=DEFAULT_EXTENT):
    if HAS_CARTOPY and hasattr(ax, "set_extent"):
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.coastlines(resolution="50m", linewidth=0.6)
        ax.add_feature(cfeature.STATES.with_scale("50m"), linewidth=0.35, edgecolor="0.35")
        ax.add_feature(cfeature.BORDERS.with_scale("50m"), linewidth=0.45, edgecolor="0.25")
    else:
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")


def scatter_categorical(ax, lon, lat, values, point_size=7.0, alpha=0.85, show_below_5=False):
    labels = risk_category_labels(values)
    transform = ccrs.PlateCarree() if HAS_CARTOPY else None
    for lab in RISK_LABELS:
        if lab == "<5%" and not show_below_5:
            continue
        m = labels == lab
        if not np.any(m):
            continue
        kwargs = dict(s=point_size, c=RISK_COLORS[lab], alpha=alpha, label=lab, linewidths=0.0)
        if transform is not None:
            kwargs["transform"] = transform
        ax.scatter(lon[m], lat[m], **kwargs)


def overlay_mcs_contour(ax, mcs_mask, mcs_lat, mcs_lon, label="MCS cold-cloud object"):
    if mcs_mask is None or mcs_lat is None or mcs_lon is None:
        return False
    if not np.asarray(mcs_mask).any():
        return False
    transform = ccrs.PlateCarree() if HAS_CARTOPY else None
    kwargs = {"levels": [0.5], "colors": "black", "linewidths": 1.8, "alpha": 0.95}
    if transform is not None:
        kwargs["transform"] = transform
    try:
        cs = ax.contour(mcs_lon, mcs_lat, np.asarray(mcs_mask, dtype=float), **kwargs)
        # Avoid label spam; legend handle is added globally.
        return True
    except Exception as exc:
        log(f"Warning: failed to overlay MCS contour: {exc}")
        return False


def build_plot_panels(
    df: pd.DataFrame,
    radii: list[int],
    include_wpc: bool,
    include_ufvs: bool,
    include_pp: bool,
):
    """Build the operational panel list: radius members, ensemble mean, and WPC."""
    panels = []
    for r in radii:
        c = radius_prob_col(r)
        if c in df.columns:
            panels.append((f"ML r{int(r)} km", c))
        if int(r) == R60KM_V2_RADIUS_KM and R60KM_V2_PROB_COL in df.columns:
            panels.append(("ML r60kmV2 density-weighted", R60KM_V2_PROB_COL))
    if not panels:
        # Last-resort automatic discovery if args.radii does not match columns.
        for c in radius_cols_in_df(df):
            if c == R60KM_V2_PROB_COL:
                panels.append(("ML r60kmV2 density-weighted", c))
            else:
                r = int(re.search(r"r(\d+)", c).group(1))
                panels.append((f"ML r{r} km", c))
    if ENSEMBLE_MEAN_COL in df.columns:
        panels.append(("ML Ensemble Mean", ENSEMBLE_MEAN_COL))
    if include_wpc and WPC_COL in df.columns and pd.to_numeric(df[WPC_COL], errors="coerce").fillna(0).max() > 0:
        panels.append(("WPC ERO", WPC_COL))
    elif include_wpc and WPC_COL in df.columns:
        # Keep a WPC panel even when it is all zero/missing so automation output
        # makes it obvious that WPC was checked but no category overlapped the grid.
        panels.append(("WPC ERO", WPC_COL))
    if include_pp:
        for c in ["PP_Any flood proxy", "PP_Stage IV > FFG", "PP_Stage IV ARI", "PP_USGS", "PP_Flash LSR"]:
            if c in df.columns and pd.to_numeric(df[c], errors="coerce").fillna(0).max() > 0:
                panels.append((c.replace("PP_", "PP: "), c))
                break
    if include_ufvs:
        for c in ["UFVS_ANY", "UFVS_STAGE4_FFG", "UFVS_STAGE4_ARI", "UFVS_USGS", "UFVS_LSR_FLASH"]:
            if c in df.columns and pd.to_numeric(df[c], errors="coerce").fillna(0).max() > 0:
                panels.append((c, c))
                break
    return panels


def plot_realtime_ero_panels(
    df: pd.DataFrame,
    date: str,
    rp: RuntimePaths,
    radii=None,
    extent=DEFAULT_EXTENT,
    mcs_mask=None,
    mcs_lat=None,
    mcs_lon=None,
    mcs_info: MCSDetectionResult | None = None,
    include_wpc: bool = True,
    include_ufvs: bool = False,
    include_pp: bool = False,
    show_below_5: bool = False,
    point_size: float = 7.0,
    alpha: float = 0.85,
    cycle_label: str | None = None,
    overlay_mcs_contour_on_public_plot: bool = False,
    output_filename: str | None = None,
    figure_title: str | None = None,
) -> Path:
    d = date8(date)
    radii = radii or DEFAULT_RADII_KM
    sub = df[df["Date"].astype(str).str.slice(0, 8) == d].copy()
    if sub.empty:
        raise RuntimeError(f"No dataframe rows for date={d}")
    if ENSEMBLE_MEAN_COL not in sub.columns:
        sub = add_ensemble_mean(sub, radii)
    panels = build_plot_panels(sub, radii=radii, include_wpc=include_wpc, include_ufvs=include_ufvs, include_pp=include_pp)
    if not panels:
        raise RuntimeError("No panels to plot.")
    ncols = min(3, max(1, len(panels)))
    nrows = int(math.ceil(len(panels) / ncols))
    proj = ccrs.PlateCarree() if HAS_CARTOPY else None
    subplot_kw = {"projection": proj} if HAS_CARTOPY else {}
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.8 * ncols, 5.6 * nrows), subplot_kw=subplot_kw, constrained_layout=True)
    axes = np.atleast_1d(axes).ravel()
    lon = pd.to_numeric(sub["Lon"], errors="coerce").to_numpy(float)
    lat = pd.to_numeric(sub["Lat"], errors="coerce").to_numpy(float)
    for ax, (title, col) in zip(axes, panels):
        setup_map_ax(ax, extent)
        vals = pd.to_numeric(sub[col], errors="coerce").fillna(0).clip(0, 1).to_numpy(float)
        scatter_categorical(ax, lon, lat, vals, point_size=point_size, alpha=alpha, show_below_5=show_below_5)
        # Public forecast product: radius-member ML probabilities plus WPC only.
        # Never overlay internal generation-gate masks/contours on this graphic.
        ax.set_title(f"{title}")
    for ax in axes[len(panels):]:
        ax.set_visible(False)
    handles = [Patch(facecolor=RISK_COLORS[lab], edgecolor="0.3", label=lab) for lab in RISK_LABELS if show_below_5 or lab != "<5%"]
    fig.legend(handles=handles, loc="lower center", ncols=min(len(handles), 6), frameon=True)
    _, _, valid_label = valid_period_12z(d)
    fig.suptitle(figure_title or f"Realtime ML flood probabilities | Valid {valid_label}", fontsize=15)
    outpath = rp.outdir / (output_filename or f"realtime_ml_public_{d}_valid12to12_radii_wpc.png")
    fig.savefig(outpath, dpi=175, bbox_inches="tight")
    plt.close(fig)
    log(f"Saved ML probability plot: {outpath}")
    return outpath


# ======================================================================================
# CLI and top-level workflow
# ======================================================================================


def parse_training_script_by_radius(items: list[str] | None) -> dict[int, str]:
    out = {}
    if not items:
        return out
    for item in items:
        if ":" not in item:
            raise ValueError(f"--training-script-by-radius entries must look like 40:generated_v33_radius_sensitivity_slimmaster_r40km.py; got {item!r}")
        r, p = item.split(":", 1)
        out[int(round(float(r)))] = p
    return out


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Standalone MCS-triggered realtime ML probability plotter")
    p.add_argument("--date", required=True, help="Case date YYYYMMDD. The forecast valid window is treated as date 12Z to next-day 12Z for WPC/UFVS.")
    p.add_argument("--project-dir", default=None, help="fall_2025_ml_proj directory. If omitted, common local paths are scored and selected.")
    p.add_argument("--script-dir", default=None, help="Directory containing generated v33 radius helper scripts. Default: PROJECT_DIR/../mesoanalysis/gempak-scripts")
    p.add_argument("--cache-dir", default=None, help="Realtime cache directory. Default: PROJECT_DIR/v33_realtime_radiusstats_forecasts")
    p.add_argument("--outdir", default=None, help="Directory for output PNG/status files. Default: CACHE_DIR/mcs_triggered_figures")
    p.add_argument("--original-root", default="/home/tyreekfrazier/ISU_Research", help="Original root to patch inside generated helper globals.")
    p.add_argument("--local-root", default="/home/tyreekfrazier/ISU_Research_LOCAL_RUN", help="Local root replacement for generated helper globals.")

    p.add_argument("--run-hrrr-detector", action=argparse.BooleanOptionalAction, default=True, help="Run HRRR SBT123/SBT124 MCS detector before ML plotting. Default: true")
    p.add_argument("--hrrr-cycle", default=HRRR_DEFAULT_CYCLE, help="HRRR cycle for trigger detection. Default: 12")
    p.add_argument("--hrrr-data-source", choices=["auto", "nomads", "aws"], default="auto", help="HRRR source. Auto uses NOMADS for recent runs and the NOAA AWS archive for older cases.")
    p.add_argument("--fhr-start", type=int, default=HRRR_DEFAULT_FHR_START, help="First HRRR forecast hour for SBT trigger scan. Default: 0")
    p.add_argument("--fhr-end", type=int, default=HRRR_DEFAULT_FHR_END, help="Last HRRR forecast hour for SBT trigger scan. Default: 24")
    p.add_argument("--ir-path", default=None, help="Optional local HRRR SBT GRIB2/NPZ file for single-file trigger testing. If omitted, HRRR SBT subsets are downloaded from NOMADS.")
    p.add_argument("--hrrr-trigger-cache-dir", default=None, help="Directory for HRRR trigger GRIB/debug files. Default: CACHE_DIR/hrrr_mcs_trigger_inputs/DATE_CYCLEz")
    p.add_argument("--force-hrrr-download", action="store_true", help="Redownload HRRR trigger subset files even if valid cached GRIB files exist.")
    p.add_argument("--force-pyflextrkr", action="store_true", help="Rebuild prepared PyFLEXTRKR inputs and rerun every official package stage.")
    p.add_argument("--hrrr-download-timeout", type=int, default=HRRR_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS, help="Timeout seconds for each HRRR NOMADS request. Default: 180")
    p.add_argument("--hrrr-max-download-attempts", type=int, default=HRRR_DEFAULT_MAX_DOWNLOAD_ATTEMPTS, help="Max attempts per HRRR subset. Default: 4")
    p.add_argument("--hrrr-download-retry-seconds", type=int, default=HRRR_DEFAULT_DOWNLOAD_RETRY_SECONDS, help="Seconds between HRRR download retries. Default: 20")
    p.add_argument("--hrrr-nomads-pause-seconds", type=float, default=HRRR_DEFAULT_NOMADS_PAUSE_SECONDS, help="Pause after successful NOMADS subset download. Default: 0.25")
    p.add_argument("--hrrr-cell-area-km2", type=float, default=HRRR_DEFAULT_CELL_AREA_KM2, help="Approximate HRRR grid-cell area used for connected object area. Default: 9 km^2")
    p.add_argument("--rap-cycle", default=RAP_DEFAULT_CYCLE, help=argparse.SUPPRESS)
    p.add_argument("--rap-fhr-start", type=int, default=RAP_DEFAULT_FHR_START, help=argparse.SUPPRESS)
    p.add_argument("--rap-fhr-end", type=int, default=RAP_DEFAULT_FHR_END, help=argparse.SUPPRESS)
    p.add_argument("--rap-trigger-cache-dir", default=None, help=argparse.SUPPRESS)
    p.add_argument("--force-rap-download", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--rap-cell-area-km2", type=float, default=RAP_DEFAULT_CELL_AREA_KM2, help=argparse.SUPPRESS)
    p.add_argument("--bt-threshold-operator", default="<", choices=["<", "<=", ">", ">="], help="Operator for HRRR SBT trigger. Default: <, i.e., SBT < 241 K")
    p.add_argument("--include-qpf-trigger", action="store_true", help="Deprecated compatibility flag. QPF is recorded diagnostically but cannot make a forecast MCS-eligible.")
    p.add_argument("--include-qpf-debug", action="store_true", help="Download HRRR APCP and save the QPF6 rainfall-only diagnostic; QPF never triggers publishing.")
    p.add_argument("--qpf6-threshold-mm", type=float, default=QPF6_THRESHOLD_MM_DEFAULT, help="HRRR 6-h QPF threshold for optional QPF trigger/debug. Default: 50.8 mm")
    p.add_argument("--qpf6-area-threshold-km2", type=float, default=QPF6_AREA_THRESHOLD_KM2_DEFAULT, help="Minimum contiguous 6-h QPF area for optional QPF trigger. Default: 0")
    p.add_argument("--save-hrrr-debug-plots", action=argparse.BooleanOptionalAction, default=True, help="Save HRRR SBT/QPF debug plots. Default: true")
    p.add_argument("--mcs-mask-path", default=None, help="Optional NPZ from a prior detector containing mask/lat/lon[/bt].")
    p.add_argument("--bt-var", default=None, help="Brightness-temperature variable name in --ir-path.")
    p.add_argument("--lat-var", default=None, help="Latitude variable name in --ir-path.")
    p.add_argument("--lon-var", default=None, help="Longitude variable name in --ir-path.")
    p.add_argument("--bt-threshold-k", type=float, default=MCS_BT_THRESHOLD_K_DEFAULT, help="MCS cold cloud threshold. Default: BT < 241 K")
    p.add_argument("--min-mcs-area-km2", type=float, default=MCS_MIN_AREA_KM2_DEFAULT, help="Tracked cold-cloud-shield area must exceed this value. Default: 60000 km^2")
    p.add_argument("--mcs-cloud-duration-hours", type=int, default=MCS_CLOUD_DURATION_HOURS_DEFAULT, help="Minimum continuous HRRR cold-cloud-shield duration. Default: 3 hourly samples")
    p.add_argument("--mcs-structural-duration-hours", type=int, default=MCS_STRUCTURAL_DURATION_HOURS_DEFAULT, help="Minimum continuous HRRR precipitation-feature and convective-feature duration. Default: 4 hourly samples")
    p.add_argument("--rap-structural-duration-hours", type=int, default=RAP_STRUCTURAL_DURATION_HOURS_DEFAULT, help=argparse.SUPPRESS)
    p.add_argument("--track-overlap-threshold", type=float, default=MCS_TRACK_OVERLAP_THRESHOLD_DEFAULT, help="Minimum consecutive-hour object overlap fraction. Default: 0.5")
    p.add_argument("--precipitation-threshold-dbz", type=float, default=MCS_PRECIPITATION_THRESHOLD_DBZ_DEFAULT, help="Composite reflectivity used to delineate precipitation features. Default: 25 dBZ")
    p.add_argument("--precipitation-major-axis-km", type=float, default=MCS_PRECIPITATION_MAJOR_AXIS_KM_DEFAULT, help="Precipitation-feature major axis must exceed this value. Default: 100 km")
    p.add_argument("--convective-threshold-dbz", type=float, default=MCS_CONVECTIVE_THRESHOLD_DBZ_DEFAULT, help="A precipitation feature must contain composite reflectivity above this value. Default: 45 dBZ")
    p.add_argument("--trigger-audit-only", action="store_true", help="Run the HRRR lifecycle audit, write status and summaries, and skip all ML prediction/plotting.")
    p.add_argument("--force-trigger", action="store_true", help="Skip/override MCS detection and run ML plotting anyway.")
    p.add_argument("--overlay-mcs-contour-on-public-plot", action="store_true", help="Deprecated/no-op. Public ML/WPC plots never include internal generation-gate contours.")

    p.add_argument("--radii", nargs="+", type=int, default=DEFAULT_RADII_KM, help="Radius members to run. Default: 40 60 75 100")
    p.add_argument("--training-script-by-radius", nargs="*", default=None, help="Optional radius:script.py overrides, e.g. 40:generated_v33_radius_sensitivity_slimmaster_r40km.py")
    p.add_argument("--nam-dir", default=None, help="Override helper RAP_DIR/nam_dir. Useful when testing a different source/cycle directory.")
    p.add_argument("--cycle-label", default="hrrr12z", help="Cache/plot label for this automated run, e.g. hrrr12z. Default: hrrr12z")

    p.add_argument("--force-features", action="store_true", help="Rebuild realtime feature cache.")
    p.add_argument("--force-predict", action="store_true", help="Rebuild realtime prediction cache.")
    p.add_argument("--force-wpc", action="store_true", help="Refetch/rebuild WPC ERO raster cache.")
    p.add_argument("--force-ufvs", action="store_true", help="Refetch/rebuild UFVS/PP cache.")
    p.add_argument("--include-wpc", action=argparse.BooleanOptionalAction, default=True, help="Fetch/rasterize WPC ERO. Default: true")
    p.add_argument("--include-ufvs", action="store_true", help="Fetch UFVS and build PP/proxy panels. Default: false/day-zero mode.")
    p.add_argument("--verification-only", action="store_true", help="Force internal UFVS/PP verification for existing prediction caches; do not run detection, feature extraction, prediction, or public plotting.")
    p.add_argument("--include-regular-flood-lsr", action="store_true", help="Include LSRREG in UFVS processing.")
    p.add_argument("--pp-expansion-radius-km", type=float, default=40.0)
    p.add_argument("--pp-smooth-radius-km", type=float, default=100.0)

    p.add_argument("--allow-feature-nan-fill-zero", action=argparse.BooleanOptionalAction, default=True, help="Match the v33 viewer model matrix by replacing NaN/inf predictors with 0.0. Default: true.")
    p.add_argument("--plot-field", default=None, help=argparse.SUPPRESS)  # deprecated; ignored. Radius panels are always plotted.
    p.add_argument("--include-members", action="store_true", help=argparse.SUPPRESS)  # deprecated; ignored.
    p.add_argument("--include-pp-panel", action="store_true", help="Plot PP panel if include-ufvs created nonzero PP.")
    p.add_argument("--show-below-5", action="store_true", help="Show <5%% pixels in white. Default hides them.")
    p.add_argument("--point-size", type=float, default=7.0)
    p.add_argument("--alpha", type=float, default=0.85)
    p.add_argument("--extent", nargs=4, type=float, default=DEFAULT_EXTENT, metavar=("LON_MIN", "LON_MAX", "LAT_MIN", "LAT_MAX"))
    p.add_argument("--status-json", default=None, help="Optional explicit status JSON path. Default is OUTDIR/status_DATE_cycle.json")
    p.add_argument("--verbose", action="store_true", help="Show per-forecast-hour HRRR, download, and generated-helper stdout diagnostics. Default: quiet automation logging.")
    return p.parse_args(argv)


def write_status(path: Path, status: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, default=str))
    log(f"Wrote status JSON: {path}")


def main(argv=None) -> int:
    args = parse_args(argv)
    global SCRIPT_VERBOSE
    SCRIPT_VERBOSE = bool(getattr(args, "verbose", False))
    d = date8(args.date)
    rp = make_runtime_paths(args)
    status_path = Path(args.status_json).expanduser().resolve() if args.status_json else (rp.outdir / f"status_{d}_{cycle_cache_token(args.cycle_label)}.json")
    valid_start, valid_end, valid_label = valid_period_12z(d)
    status = {
        "date": d,
        "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
        "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        "valid_period_label": valid_label,
        "trigger_cycle_label": args.cycle_label,
        "hrrr_trigger_cycle": f"{str(args.hrrr_cycle).zfill(2)}Z",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "project_dir": str(rp.project_dir),
        "script_dir": str(rp.script_dir),
        "triggered": False,
        "plot_path": None,
        "data_path": None,
        "mcs_detection": None,
        "error": None,
    }
    try:
        if args.verification_only:
            verified_path = verify_existing_realtime_predictions(
                date=d,
                radii=args.radii,
                rp=rp,
                force_ufvs=args.force_ufvs,
                include_regular_flood_lsr=args.include_regular_flood_lsr,
                pp_expansion_radius_km=args.pp_expansion_radius_km,
                pp_smooth_radius_km=args.pp_smooth_radius_km,
                cycle_label=args.cycle_label,
            )
            status["verification_path"] = str(verified_path) if verified_path else None
            verification_plot = None
            if verified_path:
                verified_df = pd.read_parquet(verified_path)
                verified_df = add_wpc_ero_to_realtime_from_iem(
                    verified_df,
                    date=d,
                    rp=rp,
                    force_wpc=args.force_wpc,
                )
                verification_plot = plot_realtime_ero_panels(
                    verified_df,
                    date=d,
                    rp=rp,
                    radii=args.radii,
                    include_wpc=True,
                    include_ufvs=False,
                    include_pp=True,
                    show_below_5=False,
                    output_filename=f"realtime_ml_verification_{d}_valid12to12_radii_pp.png",
                    figure_title=f"ML forecast, WPC ERO, and Practically Perfect verification | Valid {valid_label}",
                )
            status["verification_plot_path"] = str(verification_plot) if verification_plot else None
            status["finished_utc"] = datetime.now(timezone.utc).isoformat()
            write_status(status_path, status)
            return 0

        mcs_result, mcs_mask, mcs_lat, mcs_lon, mcs_bt = get_mcs_detection(args, rp)
        # force-trigger overrides only the yes/no gate; it does not invent a contour.
        triggered = bool(mcs_result.triggered or args.force_trigger)
        status["triggered"] = triggered
        status["mcs_detection"] = asdict(mcs_result)
        status["mcs_eligible"] = bool(mcs_result.triggered)
        status["mcs_classification_label"] = (
            "MCS-associated precipitation"
            if mcs_result.triggered
            else "Non-MCS-associated precipitation"
        )
        if args.trigger_audit_only:
            status["finished_utc"] = datetime.now(timezone.utc).isoformat()
            write_status(status_path, status)
            return 0
        if not triggered:
            log(
                f"No HRRR MCS trigger for {d}: total_HRRR_objects={mcs_result.n_objects_total}, "
                f"passing={mcs_result.n_objects_passing}, largest_area={mcs_result.largest_area_km2:,.0f} km^2. Exiting without ML plot."
            )
            status["finished_utc"] = datetime.now(timezone.utc).isoformat()
            write_status(status_path, status)
            return 0

        # ecCodes/pygrib can crash when RAP files are opened after the detector's
        # GRIB files in the same interpreter. Re-exec the ML stage in a clean
        # process, carrying the saved mask and its actual PyFLEXTRKR provenance.
        if args.run_hrrr_detector and os.environ.get("REALTIME_ML_STAGE_CHILD") != "1":
            child_args = []
            skip_next = False
            for token in sys.argv[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if token == "--mcs-mask-path":
                    skip_next = True
                    continue
                if token in {"--run-hrrr-detector", "--no-run-hrrr-detector", "--force-trigger"}:
                    continue
                child_args.append(token)
            child_args.append("--no-run-hrrr-detector")
            if mcs_result.mask_path:
                child_args.extend(["--mcs-mask-path", str(mcs_result.mask_path)])
            env = os.environ.copy()
            env["REALTIME_ML_STAGE_CHILD"] = "1"
            log("Starting isolated ML feature/prediction stage in a fresh Python process.")
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *child_args], env=env)
            return int(proc.returncode)

        if mcs_result.selected:
            log(f"MCS trigger TRUE: area={mcs_result.selected.area_km2:,.0f} km^2, pixels={mcs_result.selected.pixel_count:,}, minBT={mcs_result.selected.min_bt_k:.1f} K")
        else:
            log("MCS trigger TRUE by --force-trigger.")

        df = build_predict_verify_realtime_multi_radius(
            date=d,
            radii=args.radii,
            rp=rp,
            force_predict=args.force_predict,
            force_features=args.force_features,
            force_ufvs=args.force_ufvs,
            force_wpc=args.force_wpc,
            include_ufvs=args.include_ufvs,
            include_regular_flood_lsr=args.include_regular_flood_lsr,
            include_wpc=args.include_wpc,
            pp_expansion_radius_km=args.pp_expansion_radius_km,
            pp_smooth_radius_km=args.pp_smooth_radius_km,
            training_script_by_radius=parse_training_script_by_radius(args.training_script_by_radius),
            nam_dir_override=args.nam_dir,
            cycle_label=args.cycle_label,
            allow_feature_nan_fill_zero=args.allow_feature_nan_fill_zero,
        )
        available_radii = df.attrs.get("available_radii", sorted(args.radii))
        available_models = df.attrs.get("available_models", [f"r{int(r)}km" for r in available_radii])
        data_path = multi_radius_cache_path(rp, d, available_radii, args.cycle_label)
        status["available_radii"] = list(map(int, available_radii))
        status["available_models"] = list(map(str, available_models))
        status["data_path"] = str(data_path)
        plot_path = plot_realtime_ero_panels(
            df,
            date=d,
            rp=rp,
            radii=args.radii,
            extent=args.extent,
            mcs_mask=mcs_mask,
            mcs_lat=mcs_lat,
            mcs_lon=mcs_lon,
            mcs_info=mcs_result,
            include_wpc=args.include_wpc,
            include_ufvs=args.include_ufvs,
            include_pp=args.include_pp_panel,
            show_below_5=args.show_below_5,
            point_size=args.point_size,
            alpha=args.alpha,
            cycle_label=args.cycle_label,
            overlay_mcs_contour_on_public_plot=args.overlay_mcs_contour_on_public_plot,
        )
        status["plot_path"] = str(plot_path)
        status["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_status(status_path, status)
        return 0
    except Exception as exc:
        status["error"] = repr(exc)
        status["traceback"] = traceback.format_exc(limit=12)
        status["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_status(status_path, status)
        log("ERROR: " + repr(exc))
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
