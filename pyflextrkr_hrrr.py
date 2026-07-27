#!/usr/bin/env python3
"""Run the actual PyFLEXTRKR radar-MCS pipeline on model SBT and REFC fields."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import warnings

import numpy as np


DEFAULT_PYFLEXTRKR_PYTHON = Path(
    os.environ.get(
        "XGBFFP_PYFLEXTRKR_PYTHON",
        "/home/tyreekfrazier/.conda/envs/xgbffp-pyflextrkr/bin/python",
    )
)
ADAPTER_SCHEMA_VERSION = 2
PINNED_PYFLEXTRKR_COMMIT = "6a3a6435ee6b3a64ec411b9f2af38226d6f32850"
VERTICAL_LEVELS_KM = np.arange(1.0, 11.0, dtype=np.float32)


@dataclass
class PyFLEXTRKRResult:
    detected: bool
    ir_duration_met: bool
    structural_duration_met: bool
    max_ir_duration_hours: int
    max_joint_duration_hours: int
    selected_fhr: int | None
    package_version: str
    upstream_commit: str
    robust_stats_path: str | None
    result_path: str
    config_path: str
    input_manifest_path: str
    official_steps_completed: list[str]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _structural_only_tb(reflectivity: np.ndarray) -> np.ndarray:
    """Create a neutral interior cloud object solely to run PyFLEXTRKR PF stages."""
    ref = np.asarray(reflectivity)
    out = np.full(ref.shape, 300.0, dtype=float)
    if ref.ndim != 2 or min(ref.shape) < 3:
        raise ValueError("Structural-only PyFLEXTRKR input must be a 2D grid at least 3x3")
    interior = np.isfinite(ref[1:-1, 1:-1])
    out[1:-1, 1:-1] = np.where(interior, 220.0, 300.0)
    return out


def _case_signature(
    run_date: str,
    cycle: str,
    fhrs: list[int],
    shape: tuple[int, int],
    *,
    bt_threshold_k: float,
    cloud_area_threshold_km2: float,
    precipitation_threshold_dbz: float,
    precipitation_major_axis_threshold_km: float,
    convective_threshold_dbz: float,
    cloud_duration_hours: int,
    structural_duration_hours: int,
    overlap_threshold: float,
    extent: tuple[float, float, float, float],
    cell_area_km2: float,
    source_model: str,
    ir_required: bool,
) -> dict:
    return {
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "run_date": run_date,
        "cycle": cycle,
        "forecast_hours": fhrs,
        "grid_shape": list(shape),
        "bt_threshold_k": float(bt_threshold_k),
        "cloud_area_threshold_km2": float(cloud_area_threshold_km2),
        "precipitation_threshold_dbz": float(precipitation_threshold_dbz),
        "precipitation_major_axis_threshold_km": float(
            precipitation_major_axis_threshold_km
        ),
        "convective_threshold_dbz": float(convective_threshold_dbz),
        "cloud_duration_hours": int(cloud_duration_hours),
        "structural_duration_hours": int(structural_duration_hours),
        "overlap_threshold": float(overlap_threshold),
        "extent": [float(value) for value in extent],
        "cell_area_km2": float(cell_area_km2),
        "source_model": str(source_model).upper(),
        "ir_required": bool(ir_required),
        "pyflextrkr_upstream_commit": PINNED_PYFLEXTRKR_COMMIT,
        "reflectivity_representation": (
            f"{str(source_model).upper()} REFC composite replicated on compatibility height levels; "
            "used only to express reflectivity present at any vertical level"
        ),
    }


def _write_hourly_netcdf(
    path: Path,
    valid_time: datetime,
    bt: np.ndarray,
    refc: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    precipitation_threshold_dbz: float,
    source_model: str,
) -> None:
    from netCDF4 import Dataset, date2num

    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    part.unlink(missing_ok=True)
    ny, nx = bt.shape
    with Dataset(part, "w", format="NETCDF4") as ds:
        ds.createDimension("time", 1)
        ds.createDimension("level", len(VERTICAL_LEVELS_KM))
        ds.createDimension("y", ny)
        ds.createDimension("x", nx)

        time_var = ds.createVariable("time", "f8", ("time",))
        time_var.units = "seconds since 1970-01-01 00:00:00 UTC"
        time_var.calendar = "standard"
        time_var[:] = date2num([valid_time], time_var.units, time_var.calendar)
        level_var = ds.createVariable("level", "f4", ("level",))
        level_var.units = "km above mean sea level"
        level_var[:] = VERTICAL_LEVELS_KM

        lat_var = ds.createVariable("latitude", "f4", ("y", "x"), zlib=True)
        lon_var = ds.createVariable("longitude", "f4", ("y", "x"), zlib=True)
        lat_var.units = "degrees_north"
        lon_var.units = "degrees_east"
        lat_var[:] = lat.astype(np.float32)
        lon_var[:] = lon.astype(np.float32)

        chunks_2d = (1, min(ny, 256), min(nx, 256))
        tb_var = ds.createVariable(
            "tb", "f4", ("time", "y", "x"), zlib=True, complevel=3,
            chunksizes=chunks_2d, fill_value=np.float32(np.nan),
        )
        tb_var.units = "K"
        tb_var[:] = bt[np.newaxis].astype(np.float32)

        # PyFLEXTRKR's radar matcher identifies precipitation features from
        # the precipitation variable. This binary-valued proxy makes those
        # features exactly the connected model REFC >=25-dBZ regions.
        rainrate = np.where(
            np.isfinite(refc) & (refc >= float(precipitation_threshold_dbz)),
            3.0,
            0.0,
        ).astype(np.float32)
        rain_var = ds.createVariable(
            "rainrate", "f4", ("time", "y", "x"), zlib=True, complevel=3,
            chunksizes=chunks_2d, fill_value=np.float32(np.nan),
        )
        rain_var.units = "mm h-1"
        rain_var.long_name = "REFC-threshold precipitation-feature proxy"
        rain_var[:] = rainrate[np.newaxis]

        refl_var = ds.createVariable(
            "reflectivity",
            "f4",
            ("time", "level", "y", "x"),
            zlib=True,
            complevel=3,
            chunksizes=(1, 1, min(ny, 256), min(nx, 256)),
            fill_value=np.float32(np.nan),
        )
        refl_var.units = "dBZ"
        source_model = str(source_model).upper()
        refl_var.long_name = f"{source_model} composite reflectivity compatibility column"
        refl_var.comment = (
            "REFC is the column maximum, so it is repeated over compatibility "
            "levels solely to encode exceedance at any vertical level. It is not "
            "a reconstructed physical vertical profile."
        )
        refc32 = refc.astype(np.float32)
        for level_index in range(len(VERTICAL_LEVELS_KM)):
            refl_var[0, level_index, :, :] = refc32

        melt_var = ds.createVariable(
            "meltinglevelheight", "f4", ("time", "y", "x"),
            zlib=True, complevel=3, chunksizes=chunks_2d,
        )
        melt_var.units = "km above mean sea level"
        melt_var[:] = np.full((1, ny, nx), 4.0, dtype=np.float32)

        ds.title = f"{source_model} fields prepared for XGBFFP PyFLEXTRKR MCS classification"
        ds.history = f"Created {datetime.now(timezone.utc).isoformat()}"
        ds.pyflextrkr_input_contract = "tb_pf_radar3d"
        ds.refc_any_vertical_level_proxy = "true"
    part.replace(path)


def _config(
    input_dir: Path,
    output_dir: Path,
    run_date: str,
    cycle: str,
    last_fhr: int,
    extent: tuple[float, float, float, float],
    *,
    bt_threshold_k: float,
    cloud_area_threshold_km2: float,
    precipitation_threshold_dbz: float,
    precipitation_major_axis_threshold_km: float,
    convective_threshold_dbz: float,
    cloud_duration_hours: int,
    structural_duration_hours: int,
    overlap_threshold: float,
    cell_area_km2: float,
    source_model: str = "HRRR",
) -> dict:
    init = datetime.strptime(run_date + cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    end = init.timestamp() + int(last_fhr) * 3600
    end_dt = datetime.fromtimestamp(end, tz=timezone.utc)
    lon_min, lon_max, lat_min, lat_max = map(float, extent)
    return {
        "run_parallel": 0,
        "nprocesses": 1,
        "dask_tmp_dir": "/tmp",
        "startdate": init.strftime("%Y%m%d.%H%M"),
        "enddate": end_dt.strftime("%Y%m%d.%H%M"),
        "time_format": "yyyy-mo-dd_hh:mm:ss",
        "databasename": f"{str(source_model).lower()}_pyflex_",
        "clouddata_path": str(input_dir) + "/",
        "root_path": str(output_dir),
        "tracking_path_name": "tracking",
        "stats_path_name": "stats",
        "pixel_path_name": "mcstracking",
        "landmask_filename": "",
        "landmask_varname": "",
        "landfrac_thresh": [0.9, 1.0],
        "pixel_radius": float(np.sqrt(cell_area_km2)),
        "datatimeresolution": 1.0,
        "tb_varname": "tb",
        "pcp_varname": "rainrate",
        "reflectivity_varname": "reflectivity",
        "meltlevel_varname": "meltinglevelheight",
        "clouddatasource": "model",
        # PyFLEXTRKR supports the fixed-spacing model grid through its "wrf"
        # radar-source branch. The input metadata retains that HRRR REFC is
        # the actual source field.
        "radardatasource": "wrf",
        "time_dimname": "time",
        "time_coordname": "time",
        "x_dimname": "x",
        "y_dimname": "y",
        "z_dimname": "level",
        "x_coordname": "longitude",
        "y_coordname": "latitude",
        "z_coordname": "level",
        "feature_type": "tb_pf_radar3d",
        "input_format": "netcdf",
        "background_Box": 12.0,
        "ReflThresh_lowlevel_gap": 20.0,
        "strat_EchoThresh_3km": 20.0,
        "strat_EchoThresh_lt3km": 10.0,
        "col_peakedness_frac": 0.3,
        "abs_ConvThres_aml": float(convective_threshold_dbz),
        "etop25dBZ_Thresh": 10.0,
        "neighbor_CompReflThresh": float(precipitation_threshold_dbz),
        "updraft_ReflGradiant_Thresh": 8.0,
        "updraft_ReflGradiant_MaxHeight": 7.0,
        "updraft_CompRefl_Thresh": 40.0,
        "echotop_gap": 1,
        "dbz_lowlevel_asl": 2.0,
        "mincoldcorepix": 4,
        "smoothwindowdimensions": 30,
        "medfiltsize": 5,
        "geolimits": [lat_min, lon_min, lat_max, lon_max],
        "area_thresh": 36.0,
        "miss_thresh": 0.35,
        "cloudtb_core": 225.0,
        "cloudtb_cold": float(bt_threshold_k),
        "cloudtb_warm": 261.0,
        "cloudtb_cloud": 261.0,
        "absolutetb_threshs": [160.0, 330.0],
        "warmanvilexpansion": 0,
        "cloudidmethod": "label_grow",
        "linkpf": 1,
        "linkpf_varname": "reflectivity_comp",
        "pf_smooth_window": 10,
        "pf_dbz_thresh": float(precipitation_threshold_dbz),
        "pf_link_area_thresh": 300.0,
        "othresh": float(overlap_threshold),
        "timegap": 1.1,
        "nmaxlinks": 200,
        "maxnclouds": 2000,
        "duration_range": [2, 30],
        "duration_range_auto_update": True,
        "duration_range_round_base": 10,
        "remove_shorttracks": 1,
        "trackstats_dense_netcdf": 1,
        "match_pixel_dt_thresh": 60.0,
        "mcs_tb_area_thresh": float(cloud_area_threshold_km2),
        "mcs_tb_duration_thresh": int(cloud_duration_hours),
        "mcs_tb_split_duration": 12,
        "mcs_tb_merge_duration": 12,
        "mcs_tb_gap": 1,
        "mcs_pf_majoraxis_thresh": float(precipitation_major_axis_threshold_km),
        "max_pf_majoraxis_thresh": 1800.0,
        # robustmcs_radar first uses a strict total-duration comparison. A
        # threshold one hour below the requested minimum therefore enforces
        # at least that many continuous hourly PF/convective samples.
        "mcs_pf_durationthresh": float(structural_duration_hours - 1),
        "mcs_pf_majoraxis_for_lifetime": 20.0,
        "mcs_pf_gap": 1,
        "pf_rr_thres": 2.0,
        "pcp_thresh": 1.0,
        "heavy_rainrate_thresh": 10.0,
        "nmaxpf": 5,
        "mcs_core_min_area": 0.0,
        "dbz_thresh": 10.0,
        "mcs_lifecycle_thresh": int(cloud_duration_hours),
        "feature_varname": "feature_number",
        "nfeature_varname": "nfeatures",
        "featuresize_varname": "npix_feature",
        "tracks_dimname": "tracks",
        "times_dimname": "times",
        "pf_dimname": "nmaxpf",
        "fillval": -9999,
        "mcstbstats_filebase": "mcs_tracks_",
        "mcspfstats_filebase": "mcs_tracks_pf_",
        "mcsrobust_filebase": "mcs_tracks_robust_",
        "pixeltracking_filebase": "mcstrack_",
        "mcsfinal_filebase": "mcs_tracks_final_",
        "run_idfeature": True,
        "run_tracksingle": True,
        "run_gettracks": True,
        "run_trackstats": True,
        "run_identifymcs": True,
        "run_matchpf": True,
        "run_robustmcs": True,
        "run_mapfeature": False,
        "run_speed": False,
    }


def prepare_and_run_pyflextrkr(
    ir_by_fhr: dict[int, np.ndarray],
    reflectivity_by_fhr: dict[int, np.ndarray],
    lat: np.ndarray,
    lon: np.ndarray,
    *,
    run_date: str,
    cycle: str,
    case_dir: Path,
    extent: tuple[float, float, float, float],
    bt_threshold_k: float = 241.0,
    cloud_area_threshold_km2: float = 60000.0,
    precipitation_threshold_dbz: float = 25.0,
    precipitation_major_axis_threshold_km: float = 100.0,
    convective_threshold_dbz: float = 45.0,
    cloud_duration_hours: int = 6,
    structural_duration_hours: int = 4,
    overlap_threshold: float = 0.5,
    cell_area_km2: float = 9.0,
    pyflextrkr_python: Path = DEFAULT_PYFLEXTRKR_PYTHON,
    force: bool = False,
    source_model: str = "HRRR",
    ir_required: bool = True,
) -> PyFLEXTRKRResult:
    source_model = str(source_model).upper()
    if ir_required:
        fhrs = sorted(set(ir_by_fhr) & set(reflectivity_by_fhr))
    else:
        fhrs = sorted(reflectivity_by_fhr)
        ir_by_fhr = {
            fhr: _structural_only_tb(reflectivity_by_fhr[fhr])
            for fhr in fhrs
        }
        cloud_area_threshold_km2 = 0.0
        cloud_duration_hours = 1
    if ir_required and len(fhrs) != len(ir_by_fhr):
        raise RuntimeError(
            "PyFLEXTRKR requires collocated IR and REFC for every forecast hour: "
            f"IR={len(ir_by_fhr)} common={len(fhrs)}"
        )
    if not fhrs:
        raise RuntimeError(f"No collocated {source_model} fields are available for PyFLEXTRKR")
    shape = np.asarray(ir_by_fhr[fhrs[0]]).shape
    if any(np.asarray(ir_by_fhr[fhr]).shape != shape for fhr in fhrs):
        raise ValueError("IR grid shape changes within PyFLEXTRKR input")
    if any(np.asarray(reflectivity_by_fhr[fhr]).shape != shape for fhr in fhrs):
        raise ValueError("REFC/IR grid shapes differ within PyFLEXTRKR input")
    if np.asarray(lat).shape != shape or np.asarray(lon).shape != shape:
        raise ValueError("PyFLEXTRKR coordinate and field shapes differ")
    if not pyflextrkr_python.is_file():
        raise FileNotFoundError(
            f"Actual PyFLEXTRKR environment is unavailable: {pyflextrkr_python}"
        )

    pyflex_root = Path(case_dir) / "pyflextrkr"
    input_dir = pyflex_root / "input"
    output_dir = pyflex_root / "output"
    config_path = pyflex_root / "config.json"
    manifest_path = pyflex_root / "input_manifest.json"
    result_path = pyflex_root / "result.json"
    signature = _case_signature(
        run_date, cycle, fhrs, shape,
        bt_threshold_k=bt_threshold_k,
        cloud_area_threshold_km2=cloud_area_threshold_km2,
        precipitation_threshold_dbz=precipitation_threshold_dbz,
        precipitation_major_axis_threshold_km=precipitation_major_axis_threshold_km,
        convective_threshold_dbz=convective_threshold_dbz,
        cloud_duration_hours=cloud_duration_hours,
        structural_duration_hours=structural_duration_hours,
        overlap_threshold=overlap_threshold,
        extent=extent,
        cell_area_km2=cell_area_km2,
        source_model=source_model,
        ir_required=ir_required,
    )
    previous = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    previous_signature = previous.get("signature", {})
    signature_changed = previous_signature != signature
    legacy_hrrr_signature = (
        source_model == "HRRR"
        and ir_required
        and previous_signature.get("adapter_schema_version") == 1
        and all(
            previous_signature.get(key) == value
            for key, value in signature.items()
            if key not in {"adapter_schema_version", "source_model", "ir_required"}
        )
    )
    if force or (signature_changed and not legacy_hrrr_signature):
        input_keys = {
            "run_date", "cycle", "forecast_hours", "grid_shape", "source_model",
            "ir_required",
            "precipitation_threshold_dbz", "reflectivity_representation",
        }
        input_is_compatible = bool(previous_signature) and all(
            previous_signature.get(key) == signature.get(key) for key in input_keys
        )
        if force or not input_is_compatible:
            shutil.rmtree(input_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        result_path.unlink(missing_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    init = datetime.strptime(run_date + cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    input_files = []
    for fhr in fhrs:
        valid = datetime.fromtimestamp(init.timestamp() + fhr * 3600, tz=timezone.utc)
        path = input_dir / f"{source_model.lower()}_pyflex_{valid:%Y-%m-%d_%H:%M:%S}.nc"
        if force or not path.is_file():
            _write_hourly_netcdf(
                path,
                valid,
                np.asarray(ir_by_fhr[fhr], dtype=float),
                np.asarray(reflectivity_by_fhr[fhr], dtype=float),
                np.asarray(lat, dtype=float),
                np.asarray(lon, dtype=float),
                precipitation_threshold_dbz,
                source_model,
            )
        input_files.append({"fhr": fhr, "path": str(path), "bytes": path.stat().st_size})
    write_json(
        manifest_path,
        {
            "signature": signature,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "input_files": input_files,
        },
    )
    config = _config(
        input_dir,
        output_dir,
        run_date,
        cycle,
        fhrs[-1],
        extent,
        bt_threshold_k=bt_threshold_k,
        cloud_area_threshold_km2=cloud_area_threshold_km2,
        precipitation_threshold_dbz=precipitation_threshold_dbz,
        precipitation_major_axis_threshold_km=precipitation_major_axis_threshold_km,
        convective_threshold_dbz=convective_threshold_dbz,
        cloud_duration_hours=cloud_duration_hours,
        structural_duration_hours=structural_duration_hours,
        overlap_threshold=overlap_threshold,
        cell_area_km2=cell_area_km2,
        source_model=source_model,
    )
    write_json(config_path, config)
    if result_path.is_file():
        cached_result = json.loads(result_path.read_text())
        if (
            cached_result.get("error")
            or cached_result.get("upstream_commit") != PINNED_PYFLEXTRKR_COMMIT
        ):
            result_path.unlink()
    if force or not result_path.is_file():
        command = [
            str(pyflextrkr_python),
            str(Path(__file__).resolve()),
            "--execute-config",
            str(config_path),
            "--result-json",
            str(result_path),
        ]
        environment = os.environ.copy()
        environment.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-cache")
        subprocess.run(command, check=True, env=environment)
    payload = json.loads(result_path.read_text())
    return PyFLEXTRKRResult(
        detected=bool(payload["detected"]),
        ir_duration_met=bool(payload["ir_duration_met"]),
        structural_duration_met=bool(payload["structural_duration_met"]),
        max_ir_duration_hours=int(payload.get("max_ir_duration_hours", 0)),
        max_joint_duration_hours=int(payload.get("max_joint_duration_hours", 0)),
        selected_fhr=payload.get("selected_fhr"),
        package_version=str(payload["package_version"]),
        upstream_commit=str(payload["upstream_commit"]),
        robust_stats_path=payload.get("robust_stats_path"),
        result_path=str(result_path),
        config_path=str(config_path),
        input_manifest_path=str(manifest_path),
        official_steps_completed=list(payload.get("official_steps_completed", [])),
    )


def execute_official_pipeline(config_path: Path, result_path: Path) -> int:
    warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"pyflextrkr\..*")
    import xarray as xr
    from pyflextrkr.ft_utilities import load_config, setup_logging
    from pyflextrkr.idfeature_driver import idfeature_driver
    from pyflextrkr.tracksingle_driver import tracksingle_driver
    from pyflextrkr.gettracks import gettracknumbers
    from pyflextrkr.trackstats_driver import trackstats_driver
    from pyflextrkr.identifymcs import identifymcs_tb
    from pyflextrkr.matchtbpf_driver import match_tbpf_tracks
    from pyflextrkr.robustmcs_radar import define_robust_mcs_radar

    setup_logging()
    config = load_config(str(config_path))
    steps = []
    distribution = importlib.metadata.distribution("pyflextrkr")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    installed_commit = direct_url.get("vcs_info", {}).get("commit_id")
    if installed_commit != PINNED_PYFLEXTRKR_COMMIT:
        raise RuntimeError(
            "PyFLEXTRKR source provenance does not match the pinned upstream "
            f"commit: installed={installed_commit!r} expected={PINNED_PYFLEXTRKR_COMMIT}"
        )
    payload = {
        "detected": False,
        "ir_duration_met": False,
        "structural_duration_met": False,
        "max_ir_duration_hours": 0,
        "max_joint_duration_hours": 0,
        "selected_fhr": None,
        "package_version": importlib.metadata.version("pyflextrkr"),
        "upstream_commit": installed_commit,
        "package_module": __import__("pyflextrkr").__file__,
        "official_steps_completed": steps,
        "robust_stats_path": None,
    }
    try:
        tracking_dir = Path(config["tracking_outpath"])
        raw_input_count = len(list(Path(config["clouddata_path"]).glob("*.nc")))
        cloud_files = list(tracking_dir.glob(f"{config['cloudid_filebase']}*.nc"))
        if len(cloud_files) < raw_input_count:
            idfeature_driver(config)
        steps.append("idfeature_driver")
        cloud_files = list(tracking_dir.glob(f"{config['cloudid_filebase']}*.nc"))
        if not cloud_files:
            # The official identifier found no cloud objects. This is a valid
            # negative classification, not an adapter or publishing error.
            write_json(result_path, payload)
            return 0
        track_files = list(tracking_dir.glob("track_*.nc"))
        if len(track_files) < max(0, len(cloud_files) - 1):
            tracksingle_driver(config)
        steps.append("tracksingle_driver")
        if not list(tracking_dir.glob("track_*.nc")):
            # Fewer than two usable feature times cannot form a track.
            write_json(result_path, payload)
            return 0
        gettracknumbers(config)
        steps.append("gettracknumbers")
        trackstats_driver(config)
        steps.append("trackstats_driver")
        try:
            mcsstats_path = identifymcs_tb(config)
        except SystemExit:
            write_json(result_path, payload)
            return 0
        steps.append("identifymcs_tb")
        with xr.open_dataset(mcsstats_path, decode_times=False, mask_and_scale=False) as ds:
            mcs_status = np.asarray(ds["mcs_status"].values)
            payload["ir_duration_met"] = bool(np.any(mcs_status == 1))
            payload["max_ir_duration_hours"] = int(
                np.max(np.sum(mcs_status == 1, axis=1), initial=0)
            )
        match_tbpf_tracks(config)
        steps.append("match_tbpf_tracks")
        try:
            robust_path = define_robust_mcs_radar(config)
        except SystemExit:
            write_json(result_path, payload)
            return 0
        steps.append("define_robust_mcs_radar")
        with xr.open_dataset(robust_path, decode_times=False, mask_and_scale=False) as ds:
            status = np.asarray(ds["pf_mcsstatus"].values)
            qualifying = status == 1
            payload["structural_duration_met"] = bool(np.any(qualifying))
            payload["detected"] = payload["structural_duration_met"]
            payload["max_joint_duration_hours"] = int(
                np.max(np.sum(qualifying, axis=1), initial=0)
            )
            if np.any(qualifying) and "base_time" in ds:
                track, time_index = np.argwhere(qualifying)[0]
                base_time = float(np.asarray(ds["base_time"].values)[track, time_index])
                start = datetime.strptime(
                    config["startdate"], "%Y%m%d.%H%M"
                ).replace(tzinfo=timezone.utc)
                payload["selected_fhr"] = int(round((base_time - start.timestamp()) / 3600))
        payload["robust_stats_path"] = str(robust_path)
        write_json(result_path, payload)
        return 0
    except Exception as exc:
        payload["error"] = repr(exc)
        write_json(result_path, payload)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-config", type=Path)
    parser.add_argument("--result-json", type=Path)
    args = parser.parse_args()
    if not args.execute_config or not args.result_json:
        parser.error("--execute-config and --result-json are required for direct execution")
    return execute_official_pipeline(args.execute_config, args.result_json)


if __name__ == "__main__":
    raise SystemExit(main())
