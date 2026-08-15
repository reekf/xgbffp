#!/usr/bin/env python3
"""Rebuild every saved realtime PP case and all dependent website statistics."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import generate_dashboard_data as dashboard
import generate_interactive_map_data as map_data
import realtime_day2_workflow as day2
import realtime_mcs_trigger_plot as core
from operational_pp_reconstruction import PP_RECIPE_ID


REPO_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECT_DIR = Path(
    "/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj"
)
DAY1_PATTERN = re.compile(r"realtime_ufvs_verified_.*_(20\d{6})\.parquet$")
DAY2_PATTERN = re.compile(
    r"realtime_day2_ufvs_verified_.*_issue(20\d{6})_valid(20\d{6})\.parquet$"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def _update_status(archive_dir: Path, updated: str) -> None:
    path = archive_dir / "status.json"
    status = json.loads(path.read_text()) if path.is_file() else {}
    status.update(
        {
            "verification_available": True,
            "verification_plot": "verification.png",
            "verification_updated_utc": updated,
            "map_available": True,
            "map_data": "map.json",
            "map_updated_utc": updated,
            "pp_reconstruction_recipe": PP_RECIPE_ID,
        }
    )
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _rebuild_archive_index(archive_root: Path, forecast_day: int) -> None:
    """Refresh archive navigation from the files and statuses just rebuilt."""
    entries = []
    for day in sorted((path for path in archive_root.iterdir() if path.is_dir()), reverse=True):
        status_path = day / "status.json"
        if not status_path.is_file():
            continue
        status = json.loads(status_path.read_text())
        plot_exists = (day / "latest.png").is_file()
        map_exists = (day / "map.json").is_file()
        verification_exists = (day / "verification.png").is_file()
        if forecast_day == 1:
            verification_embedded = bool(
                status.get("verification_embedded_in_forecast", False)
            ) or "practically perfect verification" in str(
                status.get("product_description", "")
            ).lower()
            entry = {
                "date": str(status.get("date") or day.name),
                "valid_period_label": status.get("valid_period_label", ""),
                "published": bool(status.get("published", False)),
                "plot_available": bool(plot_exists and status.get("plot_available", False)),
                "site_updated_utc": status.get("site_updated_utc", ""),
                "status_href": f"archive/{day.name}/status.json",
                "plot_href": f"archive/{day.name}/latest.png" if plot_exists else None,
                "map_available": bool(map_exists),
                "map_href": f"archive/{day.name}/map.json" if map_exists else None,
                "map_updated_utc": status.get(
                    "map_updated_utc", status.get("site_updated_utc", "")
                ),
                "verification_available": bool(
                    verification_exists or (verification_embedded and plot_exists)
                ),
                "verification_plot_href": (
                    f"archive/{day.name}/verification.png"
                    if verification_exists
                    else (
                        f"archive/{day.name}/latest.png"
                        if verification_embedded and plot_exists
                        else None
                    )
                ),
                "verification_embedded_in_forecast": bool(
                    verification_embedded and not verification_exists
                ),
                "verification_updated_utc": status.get(
                    "verification_updated_utc", status.get("site_updated_utc", "")
                ),
                "mcs_eligible": status.get("mcs_eligible", True),
                "mcs_classification_label": status.get(
                    "mcs_classification_label", "MCS classification not audited"
                ),
                "verification_included": status.get("mcs_eligible", True),
            }
        else:
            verification_embedded = bool(status.get("verification_embedded_in_map", False))
            entry = {
                "date": str(status.get("date") or day.name),
                "issue_date": status.get("issue_date", ""),
                "forecast_day": 2,
                "valid_period_label": status.get("valid_period_label", ""),
                "published": bool(status.get("published", False)),
                "plot_available": bool(plot_exists and status.get("plot_available", True)),
                "map_available": bool(map_exists and status.get("map_available", True)),
                "verification_available": bool(verification_exists or verification_embedded),
                "verification_embedded_in_map": verification_embedded,
                "source_class": status.get("source_class", "realtime"),
                "site_updated_utc": status.get("site_updated_utc", ""),
                "map_updated_utc": status.get("map_updated_utc", ""),
                "verification_updated_utc": status.get("verification_updated_utc", ""),
                "status_href": f"day2/archive/{day.name}/status.json",
                "plot_href": f"day2/archive/{day.name}/latest.png" if plot_exists else None,
                "map_href": f"day2/archive/{day.name}/map.json" if map_exists else None,
                "verification_plot_href": (
                    f"day2/archive/{day.name}/verification.png"
                    if verification_exists
                    else None
                ),
            }
        entries.append(entry)
    payload = {"generated_utc": _utc_now(), "entries": entries}
    if forecast_day == 2:
        payload["forecast_day"] = 2
    index_path = archive_root / "index.json"
    temporary = index_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, index_path)


def _validate_frame(frame: pd.DataFrame, path: Path) -> dict:
    if "PP_Reconstruction_Recipe" not in frame:
        raise RuntimeError(f"{path}: rebuilt frame lacks PP recipe provenance")
    recipes = frame["PP_Reconstruction_Recipe"].dropna().astype(str).unique().tolist()
    if recipes != [PP_RECIPE_ID]:
        raise RuntimeError(f"{path}: unexpected PP recipes {recipes}")
    values = pd.to_numeric(frame["PP_Any flood proxy"], errors="coerce")
    if values.isna().any() or not values.between(0.0, 1.0).all():
        raise RuntimeError(f"{path}: invalid reconstructed PP probabilities")
    return {
        "rows": int(len(frame)),
        "mean": float(values.mean()),
        "maximum": float(values.max()),
        "required_sources_complete": bool(
            frame["PP_Reconstruction_Required_Sources_Complete"].iloc[0]
        ),
    }


def _day1_paths(project_dir: Path) -> core.RuntimePaths:
    cache = project_dir / "v33_realtime_radiusstats_forecasts"
    return core.make_runtime_paths(
        SimpleNamespace(
            project_dir=str(project_dir),
            script_dir=str(REPO_DIR),
            cache_dir=str(cache),
            outdir=str(cache / "mcs_triggered_figures"),
            original_root="/home/tyreekfrazier/ISU_Research",
            local_root="/home/tyreekfrazier/ISU_Research_LOCAL_RUN",
        )
    )


def _day1_dates(rp: core.RuntimePaths) -> list[str]:
    dates = set()
    for path in rp.verified_cache_dir.glob("realtime_ufvs_verified_*.parquet"):
        match = DAY1_PATTERN.search(path.name)
        if match:
            dates.add(match.group(1))
    return sorted(dates)


def rebuild_day1(project_dir: Path, requested_dates: set[str] | None = None) -> list[dict]:
    rp = _day1_paths(project_dir)
    dates = _day1_dates(rp)
    if requested_dates is not None:
        dates = [date for date in dates if date in requested_dates]
    results = []
    for index, date in enumerate(dates, start=1):
        print(f"Day 1 PP rebuild {index}/{len(dates)}: {date}", flush=True)
        previous_path = rp.verified_cache_dir / (
            f"realtime_ufvs_verified_v33_multiradius_r40_r60_r75_r100_{date}.parquet"
        )
        previous = pd.read_parquet(previous_path) if previous_path.is_file() else None
        old_max = (
            float(pd.to_numeric(previous["PP_Any flood proxy"], errors="coerce").max())
            if previous is not None and "PP_Any flood proxy" in previous
            else None
        )
        rebuilt_path = core.verify_existing_realtime_predictions(
            date=date,
            radii=[40, 60, 75, 100],
            rp=rp,
            force_ufvs=False,
            cycle_label="hrrr12z",
        )
        if rebuilt_path is None:
            raise RuntimeError(f"{date}: no Day-1 prediction members were available")
        rebuilt = pd.read_parquet(rebuilt_path)
        metrics = _validate_frame(rebuilt, rebuilt_path)
        result = {"date": date, "old_maximum": old_max, **metrics}

        archive_dir = REPO_DIR / "docs" / "archive" / date
        if archive_dir.is_dir():
            combined = map_data.load_realtime(date)
            figure = core.plot_realtime_ero_panels(
                combined,
                date=date,
                rp=rp,
                radii=[40, 60, 75, 100],
                include_wpc=True,
                include_ufvs=False,
                include_pp=True,
                show_below_5=False,
                output_filename=f"realtime_ml_verification_{date}_valid12to12_radii_pp.png",
                figure_title=(
                    "ML forecast, WPC ERO, and reconstructed Practically Perfect "
                    f"verification | Valid {core.valid_period_12z(date)[2]}"
                ),
            )
            _atomic_copy(figure, archive_dir / "verification.png")
            temporary_map = archive_dir / "map.json.tmp"
            map_data.write_frame_map_data(combined, date, temporary_map, "realtime")
            os.replace(temporary_map, archive_dir / "map.json")
            _update_status(archive_dir, _utc_now())
            result["website_archive_updated"] = True
        else:
            result["website_archive_updated"] = False
        results.append(result)
    return results


def _day2_args(project_dir: Path, issue: str) -> SimpleNamespace:
    return SimpleNamespace(
        issue_date=issue,
        project_dir=str(project_dir),
        cache_dir=None,
        outdir=None,
        original_root="/home/tyreekfrazier/ISU_Research",
        local_root="/home/tyreekfrazier/ISU_Research_LOCAL_RUN",
        force_ufvs=False,
        force_wpc=False,
        allow_early_verification=False,
        verbose=False,
    )


def rebuild_day2(project_dir: Path) -> list[dict]:
    probe_args = _day2_args(project_dir, "20000101")
    rp = day2.runtime_paths(probe_args)
    cases = []
    for path in rp.verified_cache_dir.glob("realtime_day2_ufvs_verified_*.parquet"):
        match = DAY2_PATTERN.search(path.name)
        if match:
            cases.append((match.group(1), match.group(2)))
    cases = sorted(set(cases))
    results = []
    for index, (issue, valid) in enumerate(cases, start=1):
        print(f"Day 2 PP rebuild {index}/{len(cases)}: issue={issue} valid={valid}", flush=True)
        args = _day2_args(project_dir, issue)
        rp = day2.runtime_paths(args)
        rebuilt, rebuilt_path = day2.verify_forecast(args, rp)
        metrics = _validate_frame(rebuilt, rebuilt_path)
        result = {"issue_date": issue, "date": valid, **metrics}
        archive_dir = REPO_DIR / "docs" / "day2" / "archive" / valid
        if archive_dir.is_dir():
            figure = day2.plot_forecast(rebuilt, args, rp, verified=True)
            _atomic_copy(figure, archive_dir / "verification.png")
            temporary_map = archive_dir / "map.json.tmp"
            map_data.write_frame_map_data(
                rebuilt,
                valid,
                temporary_map,
                "realtime",
                forecast_day=2,
                issue_date=issue,
            )
            os.replace(temporary_map, archive_dir / "map.json")
            _update_status(archive_dir, _utc_now())
            result["website_archive_updated"] = True
        else:
            result["website_archive_updated"] = False
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--dates", nargs="*", help="Optional Day-1 date subset")
    parser.add_argument("--skip-day2", action="store_true")
    args = parser.parse_args()
    requested = set(args.dates) if args.dates else None
    summary = {
        "recipe_id": PP_RECIPE_ID,
        "generated_utc": _utc_now(),
        "day1": rebuild_day1(args.project_dir, requested),
        "day2": [] if args.skip_day2 else rebuild_day2(args.project_dir),
    }
    dashboard.publish_realtime_verification(
        REPO_DIR / "docs", _utc_now(), forecast_day=1, product_keys=dashboard.PRODUCTS
    )
    dashboard.publish_realtime_verification(
        REPO_DIR / "docs", _utc_now(), forecast_day=2, product_keys=dashboard.DAY2_PRODUCTS
    )
    _rebuild_archive_index(REPO_DIR / "docs" / "archive", forecast_day=1)
    _rebuild_archive_index(REPO_DIR / "docs" / "day2" / "archive", forecast_day=2)
    summary_path = args.project_dir / "realtime_pp_from_ufvs_cache_v33" / (
        f"rebuild_manifest_{PP_RECIPE_ID}.json"
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"Rebuild complete; manifest: {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
