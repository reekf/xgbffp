#!/usr/bin/env python3
"""Backfill interactive Day-2 test-case maps for same-valid-period comparison."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from generate_interactive_map_data import _merge_aligned, _sort_grid, write_frame_map_data


RADII = (40, 60, 75, 100)
PROJECT_DIR = Path("/home/tyreekfrazier/ISU_Research_LOCAL_RUN/fall_2025_ml_proj")
GRID_PATH = PROJECT_DIR / "df_pp_viewer_with_wpc_ero_day2valid.parquet"
PREDICTION_DIR = PROJECT_DIR / "v33day2valid_singletarget_radius_sensitivity_viewer_prediction_cache"
REPO_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = REPO_DIR / "docs" / "day2" / "archive"


def prediction_path(radius: int) -> Path:
    return PREDICTION_DIR / f"v33day2valid_singletarget_radius_sensitivity_predictions_r{radius}km.parquet"


def parquet_dates(path: Path) -> set[str]:
    table = pq.read_table(path, columns=["Date"])
    return {str(value)[:8] for value in table.column("Date").to_pylist()}


def available_dates() -> list[str]:
    paths = [GRID_PATH] + [prediction_path(radius) for radius in RADII]
    return sorted(set.intersection(*(parquet_dates(path) for path in paths)))


def read_date(path: Path, date: str, columns: list[str]) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=columns, filters=[("Date", "==", date)])
    if frame.empty:
        frame = pd.read_parquet(path, columns=columns)
        frame = frame[frame["Date"].astype(str).str[:8] == date].copy()
    return frame


def build_case(date: str) -> tuple[pd.DataFrame, str]:
    base = _sort_grid(read_date(
        GRID_PATH,
        date,
        [
            "Date",
            "RAP_Init_Date",
            "Lat",
            "Lon",
            "WPC_ERO_Risk",
            "PP_Any flood proxy",
            "UFVS_ANY",
        ],
    ))
    if base.empty:
        raise RuntimeError(f"No canonical Day-2 verification grid rows for {date}")
    issue_dates = sorted({str(value)[:8] for value in base["RAP_Init_Date"].dropna()})
    if len(issue_dates) != 1:
        raise RuntimeError(f"Expected one RAP initialization date for {date}, got {issue_dates}")

    for radius in RADII:
        prediction = read_date(
            prediction_path(radius),
            date,
            ["Date", "Lat", "Lon", "ML_Forecast_Prob"],
        ).rename(columns={"ML_Forecast_Prob": f"ML_r{radius}_Prob"})
        if prediction.empty:
            raise RuntimeError(f"No Day-2 r{radius} prediction rows for {date}")
        base = _merge_aligned(base, prediction, [f"ML_r{radius}_Prob"])
    return base, issue_dates[0]


def write_status(date: str, issue_date: str, destination: Path, generated: str) -> None:
    start = datetime.strptime(date + "12", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    payload = {
        "date": date,
        "forecast_day": 2,
        "issue_date": issue_date,
        "map_available": True,
        "map_data": "map.json",
        "map_updated_utc": generated,
        "plot_available": False,
        "published": True,
        "site_updated_utc": generated,
        "source_class": "formal-independent-test-set",
        "valid_end_utc": end.isoformat().replace("+00:00", "Z"),
        "valid_period_label": f"{start:%Y-%m-%d} 12Z to {end:%Y-%m-%d} 12Z",
        "valid_start_utc": start.isoformat().replace("+00:00", "Z"),
        "verification_available": True,
        "verification_embedded_in_map": True,
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def rebuild_archive_index(generated: str) -> None:
    entries = []
    for day_dir in sorted((path for path in ARCHIVE_DIR.iterdir() if path.is_dir()), reverse=True):
        status_path = day_dir / "status.json"
        if not status_path.is_file():
            continue
        status = json.loads(status_path.read_text())
        date = str(status.get("date") or day_dir.name)
        map_exists = (day_dir / "map.json").is_file()
        plot_exists = (day_dir / "latest.png").is_file()
        verification_plot_exists = (day_dir / "verification.png").is_file()
        entries.append({
            "date": date,
            "forecast_day": 2,
            "issue_date": status.get("issue_date"),
            "map_available": bool(map_exists and status.get("map_available", True)),
            "map_href": f"day2/archive/{date}/map.json" if map_exists else None,
            "map_updated_utc": status.get("map_updated_utc", status.get("site_updated_utc", "")),
            "plot_available": bool(plot_exists and status.get("plot_available", False)),
            "plot_href": f"day2/archive/{date}/latest.png" if plot_exists else None,
            "published": bool(status.get("published", False)),
            "site_updated_utc": status.get("site_updated_utc", ""),
            "source_class": status.get("source_class", "realtime"),
            "status_href": f"day2/archive/{date}/status.json",
            "valid_period_label": status.get("valid_period_label", ""),
            "verification_available": bool(
                status.get("verification_available", False)
                or status.get("verification_embedded_in_map", False)
            ),
            "verification_embedded_in_map": bool(status.get("verification_embedded_in_map", False)),
            "verification_plot_href": (
                f"day2/archive/{date}/verification.png" if verification_plot_exists else None
            ),
            "verification_updated_utc": status.get("verification_updated_utc", ""),
        })
    payload = {
        "entries": entries,
        "forecast_day": 2,
        "generated_utc": generated,
    }
    (ARCHIVE_DIR / "index.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", action="append", dest="dates", help="Valid date YYYYMMDD; repeat as needed")
    parser.add_argument("--force", action="store_true", help="Rebuild map files that already exist")
    args = parser.parse_args()

    source_dates = set(available_dates())
    requested = set(args.dates or source_dates)
    unknown = requested.difference(source_dates)
    if unknown:
        raise SystemExit(f"Requested dates are unavailable in every Day-2 source: {sorted(unknown)}")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    for date in sorted(requested):
        day_dir = ARCHIVE_DIR / date
        map_path = day_dir / "map.json"
        status_path = day_dir / "status.json"
        if map_path.is_file() and status_path.is_file() and not args.force:
            print(f"[{date}] already complete; skipping", flush=True)
            continue
        print(f"[{date}] building Day-2 comparison map", flush=True)
        frame, issue_date = build_case(date)
        day_dir.mkdir(parents=True, exist_ok=True)
        write_frame_map_data(
            frame,
            date,
            map_path,
            "historical",
            forecast_day=2,
            issue_date=issue_date,
        )
        write_status(date, issue_date, status_path, generated)

    rebuild_archive_index(generated)
    print(f"Day-2 archive index now contains {len(json.loads((ARCHIVE_DIR / 'index.json').read_text())['entries'])} entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
