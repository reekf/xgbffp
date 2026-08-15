#!/usr/bin/env python3
"""Operational XGBFFP Day-2 forecast and delayed-verification workflow.

Date contract
-------------
``issue_date`` is the RAP initialization/website issuance day D.  The four
Day-2 helpers are called with event date D+1, which makes them retrieve the RAP
initialized on D and build the 24--48 h predictors.  The resulting forecast is
valid D+1 12Z through D+2 12Z.  Verification is refused until that ending time
has passed, then it attaches the same event-day UFVS/Practically Perfect truth
used by the Day-1 workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd

import realtime_mcs_trigger_plot as core


RADII = (40, 60, 75, 100)
RUN_TAG = "v33day2valid"


def date8(value: str) -> str:
    return core.date8(value)


def day2_dates(issue_date: str) -> tuple[str, str, datetime, datetime]:
    issue = datetime.strptime(date8(issue_date), "%Y%m%d").replace(tzinfo=timezone.utc)
    valid = issue + timedelta(days=1)
    valid_start = valid + timedelta(hours=12)
    valid_end = valid_start + timedelta(days=1)
    return issue.strftime("%Y%m%d"), valid.strftime("%Y%m%d"), valid_start, valid_end


def verification_is_eligible(issue_date: str, now: datetime | None = None) -> bool:
    *_, valid_end = day2_dates(issue_date)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current >= valid_end


def runtime_paths(args) -> core.RuntimePaths:
    project = Path(args.project_dir).expanduser().resolve()
    cache = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else project / "v33day2_realtime_radiusstats_forecasts"
    namespace = SimpleNamespace(
        project_dir=str(project),
        script_dir=str(Path(__file__).resolve().parent),
        cache_dir=str(cache),
        outdir=str(Path(args.outdir).expanduser().resolve()) if args.outdir else None,
        original_root=args.original_root,
        local_root=args.local_root,
    )
    rp = core.make_runtime_paths(namespace)
    # Day-2 WPC and PP caches must never collide with Day 1.
    rp.wpc_cache_dir = project / "realtime_wpc_ero_cache_v33day2"
    rp.pp_cache_dir = project / "realtime_pp_from_ufvs_cache_v33day2"
    # Day 1 and Day 2 verify the same event-day 12Z-to-12Z UFVS truth. Share
    # the immutable raw inputs so both products cannot fetch different source
    # snapshots while retaining separate derived PP caches.
    rp.ufvs_cache_dir = project / "v33_realtime_radiusstats_forecasts" / "ufvs_raw"
    for path in (rp.wpc_cache_dir, rp.pp_cache_dir, rp.ufvs_cache_dir):
        path.mkdir(parents=True, exist_ok=True)
    return rp


def generated_helper(script_dir: Path, radius: int) -> Path:
    path = script_dir / "generated_v33_day2_radius_sensitivity_slimmaster_rowsample" / (
        f"hazard_ml_training_v33day2valid_r{radius}km_singletarget_radiusstats_MEMSAFE.py"
    )
    if not path.is_file():
        raise FileNotFoundError(f"Missing Day-2 generated feature helper: {path}")
    return path


def day2_artifacts(project_dir: Path, radius: int, rp: core.RuntimePaths) -> dict:
    manifest_path = project_dir / "prob_flood_models" / (
        f"active_artifacts_v33day2valid_r{radius}km_singletarget_radiusstats_mse_apcp13p7cv_domain.json"
    )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing Day-2 model manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    paths = {}
    for key, aliases in {
        "model": ("current_model_alias", "model_path"),
        "scaler": ("current_scaler_alias", "scaler_path"),
        "features": ("current_feature_names_alias", "feature_names_path"),
    }.items():
        raw = next((manifest.get(name) for name in aliases if manifest.get(name)), None)
        resolved = core.path_replace_root(raw, rp.original_root, rp.local_root)
        if not resolved or not Path(resolved).is_file():
            raise FileNotFoundError(f"Day-2 r{radius} {key} artifact is unavailable: {resolved}")
        paths[key] = Path(resolved)
    return {"manifest": manifest, "manifest_path": manifest_path, **paths}


def prediction_path(rp: core.RuntimePaths, issue: str, valid: str, radius: int) -> Path:
    return rp.prediction_cache_dir / f"realtime_day2_predictions_{RUN_TAG}_r{radius}km_issue{issue}_valid{valid}.parquet"


def forecast_path(rp: core.RuntimePaths, issue: str, valid: str) -> Path:
    return rp.verified_cache_dir / f"realtime_day2_forecast_{RUN_TAG}_multiradius_issue{issue}_valid{valid}.parquet"


def verified_path(rp: core.RuntimePaths, issue: str, valid: str) -> Path:
    return rp.verified_cache_dir / f"realtime_day2_ufvs_verified_{RUN_TAG}_multiradius_issue{issue}_valid{valid}.parquet"


def predict_member(
    issue: str,
    valid: str,
    radius: int,
    rp: core.RuntimePaths,
    *,
    force_features: bool = False,
    force_predict: bool = False,
    allow_nan_fill_zero: bool = True,
) -> pd.DataFrame:
    output = prediction_path(rp, issue, valid, radius)
    if output.is_file() and output.stat().st_size > 1024 and not force_predict:
        core.log(f"Using existing Day-2 prediction cache: {output}")
        return pd.read_parquet(output)

    helper = generated_helper(rp.script_dir, radius)
    features = core.build_realtime_features(
        valid,
        radius,
        rp,
        force_features=force_features,
        training_script=str(helper),
        cycle_label="rap09z_day2",
    )
    artifacts = day2_artifacts(rp.project_dir, radius, rp)
    feature_names = core.load_feature_names(artifacts["features"])
    matrix = core.strict_realtime_model_matrix(
        features,
        feature_names,
        context=f"realtime_day2_issue{issue}_valid{valid}_r{radius}km",
        diagnostic_dir=rp.cache_dir / "diagnostics",
        allow_nan_fill_zero=allow_nan_fill_zero,
    )
    scaler = joblib.load(artifacts["scaler"])
    model = joblib.load(artifacts["model"])
    scaled = scaler.transform(matrix).astype(np.float32, copy=False)
    probability = np.clip(core.model_positive_class_probability(model, scaled), 0.0, 1.0).astype(np.float32)
    keep = [column for column in ("Date", "Year", "Lat", "Lon") if column in features]
    out = features[keep].copy()
    out["Date"] = valid
    out["Year"] = valid[:4]
    out["Issue_Date"] = issue
    out["RAP_Init_Date"] = issue
    out["Forecast_Day"] = 2
    out["ML_Target_Radius_km"] = radius
    out["ML_Model_Label"] = f"r{radius}km"
    out["ML_Forecast_Prob"] = probability
    out["ML_Experiment_Tag"] = artifacts["manifest"].get("run_tag", f"{RUN_TAG}_r{radius}km")
    out["Prediction_Created_UTC"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(output, index=False)
    core.log(f"Saved Day-2 r{radius} prediction: {output} rows={len(out):,}")
    return out


def combine_members(issue: str, valid: str, members: dict[int, pd.DataFrame]) -> pd.DataFrame:
    base = None
    for radius in RADII:
        pred = members[radius].copy()
        pred["Date"] = valid
        one = pred[["Date", "Year", "Lat", "Lon"]].copy()
        one[f"ML_r{radius}_Prob"] = pd.to_numeric(pred["ML_Forecast_Prob"], errors="coerce").astype(np.float32)
        base = one if base is None else core.merge_grid_by_date_latlon(base, one, [f"ML_r{radius}_Prob"])
    if base is None:
        raise RuntimeError("No Day-2 members were produced")
    base["Issue_Date"] = issue
    base["RAP_Init_Date"] = issue
    base["Forecast_Day"] = 2
    base = core.add_ensemble_mean(base, RADII)
    return base


def build_forecast(args, rp: core.RuntimePaths) -> tuple[pd.DataFrame, Path]:
    issue, valid, _, _ = day2_dates(args.issue_date)
    members = {
        radius: predict_member(
            issue,
            valid,
            radius,
            rp,
            force_features=args.force_features,
            force_predict=args.force_predict,
            allow_nan_fill_zero=args.allow_feature_nan_fill_zero,
        )
        for radius in RADII
    }
    frame = combine_members(issue, valid, members)
    frame = core.add_wpc_ero_to_realtime_from_iem(
        frame,
        date=valid,
        rp=rp,
        force_wpc=args.force_wpc,
        outlook_day=2,
        issuance_date=issue,
    )
    output = forecast_path(rp, issue, valid)
    frame.to_parquet(output, index=False)
    return frame, output


def load_forecast(issue: str, valid: str, rp: core.RuntimePaths) -> pd.DataFrame:
    completed = forecast_path(rp, issue, valid)
    if completed.is_file():
        return pd.read_parquet(completed)
    members = {}
    for radius in RADII:
        path = prediction_path(rp, issue, valid, radius)
        if not path.is_file():
            raise FileNotFoundError(f"Missing issued Day-2 r{radius} prediction: {path}")
        members[radius] = pd.read_parquet(path)
    return combine_members(issue, valid, members)


def verify_forecast(args, rp: core.RuntimePaths) -> tuple[pd.DataFrame, Path]:
    issue, valid, valid_start, valid_end = day2_dates(args.issue_date)
    now = datetime.now(timezone.utc)
    if not args.allow_early_verification and now < valid_end:
        raise RuntimeError(
            f"Day-2 issue {issue} is valid {valid_start:%Y-%m-%d %HZ} to {valid_end:%Y-%m-%d %HZ}; "
            f"verification is not eligible until {valid_end.isoformat()} (now={now.isoformat()})."
        )
    frame = load_forecast(issue, valid, rp)
    verified = core.add_ufvs_and_realtime_pp(
        frame,
        date=valid,
        rp=rp,
        force_ufvs=args.force_ufvs,
    )
    verified = core.add_wpc_ero_to_realtime_from_iem(
        verified,
        date=valid,
        rp=rp,
        force_wpc=args.force_wpc,
        outlook_day=2,
        issuance_date=issue,
    )
    output = verified_path(rp, issue, valid)
    verified.to_parquet(output, index=False)
    return verified, output


def plot_forecast(frame: pd.DataFrame, args, rp: core.RuntimePaths, verified: bool = False) -> Path:
    issue, valid, valid_start, valid_end = day2_dates(args.issue_date)
    if verified:
        name = f"realtime_day2_verification_issue{issue}_valid{valid}_radii_pp.png"
        title = f"Day-2 ML forecast, WPC Day 2, and Practically Perfect | Valid {valid_start:%Y-%m-%d %HZ} to {valid_end:%Y-%m-%d %HZ}"
    else:
        name = f"realtime_day2_public_issue{issue}_valid{valid}_radii_wpc.png"
        title = f"Day-2 ML flood probabilities and WPC Day 2 | Issued {issue} | Valid {valid_start:%Y-%m-%d %HZ} to {valid_end:%Y-%m-%d %HZ}"
    return core.plot_realtime_ero_panels(
        frame,
        date=valid,
        rp=rp,
        radii=list(RADII),
        include_wpc=True,
        include_pp=verified,
        output_filename=name,
        figure_title=title,
        alpha=1.0,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-date", required=True, help="RAP initialization/forecast issuance date YYYYMMDD")
    parser.add_argument("--project-dir", default="/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--outdir", default=None)
    parser.add_argument("--original-root", default="/home/tyreekfrazier/ISU_Research")
    parser.add_argument("--local-root", default="/home/tyreekfrazier/ISU_Research_LOCAL_RUN")
    parser.add_argument("--verification-only", action="store_true")
    parser.add_argument("--run-hrrr-detector", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force-trigger", action="store_true")
    parser.add_argument("--force-features", action="store_true")
    parser.add_argument("--force-predict", action="store_true")
    parser.add_argument("--force-wpc", action="store_true")
    parser.add_argument("--force-ufvs", action="store_true")
    parser.add_argument("--allow-feature-nan-fill-zero", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-early-verification", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--status-json", default=None)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def detector_args(args, rp: core.RuntimePaths):
    # Reuse the operational PyFLEXTRKR-inspired HRRR structural detector, but
    # scan hours 24--48 because this product is valid on the following day.
    parsed = core.parse_args([
        "--date", date8(args.issue_date),
        "--project-dir", str(rp.project_dir),
        "--cache-dir", str(rp.cache_dir),
        "--outdir", str(rp.outdir),
        "--fhr-start", "24",
        "--fhr-end", "48",
    ])
    parsed.force_trigger = bool(args.force_trigger)
    parsed.run_hrrr_detector = bool(args.run_hrrr_detector)
    parsed.verbose = bool(args.verbose)
    return parsed


def main(argv=None) -> int:
    args = parse_args(argv)
    core.SCRIPT_VERBOSE = bool(args.verbose)
    issue, valid, valid_start, valid_end = day2_dates(args.issue_date)
    rp = runtime_paths(args)
    status_path = Path(args.status_json) if args.status_json else rp.outdir / f"status_day2_issue{issue}_valid{valid}.json"
    status = {
        "forecast_day": 2,
        "issue_date": issue,
        "date": valid,
        "valid_start_utc": valid_start.isoformat().replace("+00:00", "Z"),
        "valid_end_utc": valid_end.isoformat().replace("+00:00", "Z"),
        "valid_period_label": f"{valid_start:%Y-%m-%d} 12Z to {valid_end:%Y-%m-%d} 12Z",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }
    try:
        if args.verification_only:
            frame, data_path = verify_forecast(args, rp)
            plot = plot_forecast(frame, args, rp, verified=True)
            status.update({"verification_eligible": True, "data_path": str(data_path), "plot_path": str(plot)})
        else:
            if args.run_hrrr_detector and os.environ.get("REALTIME_DAY2_STAGE_CHILD") != "1":
                detect_args = detector_args(args, rp)
                result, *_ = core.get_mcs_detection(detect_args, rp)
                status["mcs_detection"] = asdict(result)
                if not (result.triggered or args.force_trigger):
                    status.update({"triggered": False, "message": "No qualifying HRRR MCS structure in forecast hours 24--48."})
                    status["finished_utc"] = datetime.now(timezone.utc).isoformat()
                    core.write_status(status_path, status)
                    return 0
                child = [token for token in sys.argv[1:] if token not in {"--run-hrrr-detector", "--no-run-hrrr-detector"}]
                child.extend(["--no-run-hrrr-detector", "--force-trigger"])
                env = os.environ.copy()
                env["REALTIME_DAY2_STAGE_CHILD"] = "1"
                return subprocess.run([sys.executable, str(Path(__file__).resolve()), *child], env=env).returncode
            frame, data_path = build_forecast(args, rp)
            plot = plot_forecast(frame, args, rp, verified=False)
            status.update({"triggered": True, "data_path": str(data_path), "plot_path": str(plot)})
        status["finished_utc"] = datetime.now(timezone.utc).isoformat()
        core.write_status(status_path, status)
        return 0
    except Exception as exc:
        status.update({
            "error": repr(exc),
            "traceback": traceback.format_exc(limit=15),
            "finished_utc": datetime.now(timezone.utc).isoformat(),
        })
        core.write_status(status_path, status)
        core.log(f"Day-2 workflow failed: {exc!r}")
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
