#!/usr/bin/env python3
"""Classify archived 2026 forecasts with the HRRR MCS lifecycle gate."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent
DEFAULT_DOCS_DIR = REPO_DIR / "docs"
DEFAULT_PROJECT_DIR = REPO_DIR.parents[1] / "fall_2025_ml_proj"
PYFLEXTRKR_PACKAGE_VERSION = "2026.7.0"
PYFLEXTRKR_UPSTREAM_COMMIT = "6a3a6435ee6b3a64ec411b9f2af38226d6f32850"
OFFICIAL_STEPS = [
    "idfeature_driver",
    "tracksingle_driver",
    "gettracknumbers",
    "trackstats_driver",
    "identifymcs_tb",
    "match_tbpf_tracks",
    "define_robust_mcs_radar",
]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")


def summary_is_current(summary: dict, fhr_end: int) -> bool:
    completed = summary.get("pyflextrkr_official_steps_completed", [])
    steps_are_valid = bool(completed) and completed == OFFICIAL_STEPS[: len(completed)]
    if bool(summary.get("mcs_detected")):
        steps_are_valid = completed == OFFICIAL_STEPS
    return (
        summary.get("mcs_method") == "actual_pyflextrkr"
        and summary.get("pyflextrkr_package_version") == PYFLEXTRKR_PACKAGE_VERSION
        and summary.get("pyflextrkr_upstream_commit") == PYFLEXTRKR_UPSTREAM_COMMIT
        and steps_are_valid
        and float(summary.get("ir_area_threshold_km2", -1)) == 40000.0
        and int(summary.get("ir_duration_threshold_hours", -1)) == 4
        and float(summary.get("precipitation_threshold_dbz", -1)) == 25.0
        and float(summary.get("precipitation_major_axis_threshold_km", -1)) == 100.0
        and float(summary.get("convective_threshold_dbz", -1)) == 45.0
        and len(summary.get("ir_records", [])) == fhr_end + 1
        and len(summary.get("reflectivity_records", [])) == fhr_end + 1
        and bool(summary.get("qpf6_records"))
    )


def classification_from_summary(day: str, summary: dict) -> dict:
    ir_met = bool(summary.get("ir_duration_met"))
    structural_met = bool(summary.get("structural_duration_met"))
    rainfall_met = bool(summary.get("qpf6_threshold_met"))
    eligible = bool(summary.get("mcs_detected") and ir_met and structural_met)
    rainfall_only = bool(rainfall_met and not ir_met)
    if eligible:
        label = "MCS-associated precipitation"
    elif rainfall_only:
        label = "Non-MCS-associated precipitation — rainfall threshold only"
    elif not ir_met:
        label = "Non-MCS-associated precipitation — cold-cloud lifecycle not met"
    else:
        label = "Non-MCS-associated precipitation — precipitation-feature structure not met"
    return {
        "date": day,
        "mcs_eligible": eligible,
        "label": label,
        "rainfall_only": rainfall_only,
        "ir_duration_met": ir_met,
        "structural_duration_met": structural_met,
        "qpf6_threshold_met": rainfall_met,
        "max_ir_duration_hours": int(summary.get("max_ir_duration_hours", 0)),
        "max_joint_duration_hours": int(summary.get("max_joint_duration_hours", 0)),
        "best_ir_component_area_km2": round(
            float((summary.get("best_ir") or {}).get("max_ir_component_area_km2", 0.0)), 1
        ),
    }


def audit_case(day: str, args, cache_root: Path, audit_status_dir: Path) -> tuple[str, dict, bool]:
    case_dir = cache_root / f"{day}_12z"
    summary_path = case_dir / f"hrrr_mcs_trigger_summary_{day}_12z.json"
    summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
    if not args.force and summary_is_current(summary, args.fhr_end):
        return day, summary, True

    status_path = audit_status_dir / f"status_{day}.json"
    command = [
        sys.executable,
        str(REPO_DIR / "realtime_mcs_trigger_plot.py"),
        "--date", day,
        "--project-dir", str(args.project_dir),
        "--fhr-end", str(args.fhr_end),
        "--trigger-audit-only",
        "--include-qpf-debug",
        "--no-save-hrrr-debug-plots",
        "--status-json", str(status_path),
    ]
    if args.force:
        command.append("--force-pyflextrkr")
    completed = subprocess.run(
        command,
        cwd=REPO_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_dir = cache_root / "archive_audit_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"pyflextrkr_{day}.log").write_text(completed.stdout or "")
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout or "").splitlines()[-80:])
        raise RuntimeError(f"PyFLEXTRKR archive audit failed for {day}:\n{tail}")
    summary = json.loads(summary_path.read_text())
    if not summary_is_current(summary, args.fhr_end):
        raise RuntimeError(f"PyFLEXTRKR audit for {day} did not write a current summary")
    return day, summary, False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--dates", nargs="*", help="Optional YYYYMMDD subset")
    parser.add_argument("--fhr-end", type=int, default=24)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    archive_dir = args.docs_dir / "archive"
    available = sorted(
        path.name
        for path in archive_dir.iterdir()
        if path.is_dir() and path.name.startswith("2026") and len(path.name) == 8
    )
    dates = [day for day in available if not args.dates or day in set(args.dates)]
    if not dates:
        raise SystemExit("No matching 2026 archive dates")

    cache_root = (
        args.project_dir
        / "v33_realtime_radiusstats_forecasts"
        / "hrrr_mcs_trigger_inputs"
    )
    audit_status_dir = cache_root / "archive_audit_status"
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    summaries = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(audit_case, day, args, cache_root, audit_status_dir): day
            for day in dates
        }
        for number, future in enumerate(as_completed(futures), start=1):
            day, summary, reused = future.result()
            summaries[day] = summary
            action = "Reused" if reused else "Completed"
            print(f"[{number}/{len(dates)}] {action} actual PyFLEXTRKR audit for {day}", flush=True)

    results = []
    for day in dates:
        summary = summaries[day]
        result = classification_from_summary(day, summary)
        results.append(result)

        status_path = archive_dir / day / "status.json"
        status = json.loads(status_path.read_text()) if status_path.is_file() else {"date": day}
        status.update(
            {
                "mcs_eligible": result["mcs_eligible"],
                "mcs_classification_label": result["label"],
                "mcs_classification": {
                    "method": "Actual PyFLEXTRKR tb_pf_radar3d pipeline using HRRR SBT and REFC",
                    "pyflextrkr_package_version": summary.get("pyflextrkr_package_version"),
                    "pyflextrkr_upstream_commit": summary.get("pyflextrkr_upstream_commit"),
                    "official_steps_completed": summary.get("pyflextrkr_official_steps_completed", []),
                    "cloud_shield": "SBT < 241 K and area > 40000 km2 for > 3 continuous hours",
                    "precipitation_feature": ">=25 dBZ connected feature with major axis >100 km for >3 continuous hours",
                    "convective_feature": "Composite simulated reflectivity >45 dBZ within the precipitation feature for >3 continuous hours",
                    "overlap_fraction": 0.5,
                    "ir_duration_met": result["ir_duration_met"],
                    "structural_duration_met": result["structural_duration_met"],
                    "qpf6_threshold_met": result["qpf6_threshold_met"],
                    "rainfall_only": result["rainfall_only"],
                    "max_ir_duration_hours": result["max_ir_duration_hours"],
                    "max_joint_duration_hours": result["max_joint_duration_hours"],
                },
            }
        )
        write_json(status_path, status)

    manifest = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "Actual PyFLEXTRKR tb_pf_radar3d pipeline using HRRR SBT and REFC",
        "pyflextrkr_package_version": PYFLEXTRKR_PACKAGE_VERSION,
        "pyflextrkr_upstream_commit": PYFLEXTRKR_UPSTREAM_COMMIT,
        "official_steps": OFFICIAL_STEPS,
        "criteria": {
            "cloud_shield_threshold_k": 241,
            "cloud_shield_area_km2": 40000,
            "precipitation_feature_threshold_dbz": 25,
            "precipitation_feature_major_axis_km": 100,
            "convective_feature_threshold_dbz": 45,
            "reflectivity_representation": "HRRR REFC composite repeated on compatibility levels to represent reflectivity exceeding 45 dBZ at any vertical level; it is not a reconstructed vertical profile",
            "duration_hours": 4,
            "duration_definition": ">3 continuous hours represented by four hourly frames",
            "object_overlap_fraction": 0.5,
            "qpf6_rainfall_only_diagnostic_mm": 50.8,
        },
        "case_count": len(results),
        "eligible_count": sum(item["mcs_eligible"] for item in results),
        "excluded_count": sum(not item["mcs_eligible"] for item in results),
        "rainfall_only_count": sum(item["rainfall_only"] for item in results),
        "cases": results,
    }
    write_json(archive_dir / "mcs-classification-2026.json", manifest)

    index_path = archive_dir / "index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text())
        by_date = {item["date"]: item for item in results}
        for entry in index.get("entries", []):
            result = by_date.get(str(entry.get("date")))
            if result:
                entry["mcs_eligible"] = result["mcs_eligible"]
                entry["mcs_classification_label"] = result["label"]
                entry["verification_included"] = result["mcs_eligible"]
        index["mcs_classification_path"] = "archive/mcs-classification-2026.json"
        write_json(index_path, index)

    print(
        f"Audited {len(results)} cases: "
        f"eligible={manifest['eligible_count']} excluded={manifest['excluded_count']} "
        f"rainfall_only={manifest['rainfall_only_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
