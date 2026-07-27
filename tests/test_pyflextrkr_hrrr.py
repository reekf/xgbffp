#!/usr/bin/env python3
"""Integration checks for the actual PyFLEXTRKR HRRR adapter."""

from pathlib import Path
import sys
import tempfile

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyflextrkr_hrrr import (  # noqa: E402
    DEFAULT_PYFLEXTRKR_PYTHON,
    _config,
    prepare_and_run_pyflextrkr,
)


def synthetic_frames(frame_count=6, structural_frame_count=4):
    ir_by_fhr = {}
    reflectivity_by_fhr = {}
    for fhr in range(frame_count):
        ir = np.full((60, 80), 280.0, dtype=np.float32)
        refc = np.zeros_like(ir)
        x0 = 3 + fhr
        ir[5:35, x0 : x0 + 30] = 220.0
        if fhr < structural_frame_count:
            refc[18:21, x0 + 2 : x0 + 22] = 30.0
            refc[19, x0 + 10] = 50.0
        ir_by_fhr[fhr] = ir
        reflectivity_by_fhr[fhr] = refc
    lat = np.broadcast_to(np.linspace(30, 36, 60)[:, None], (60, 80)).copy()
    lon = np.broadcast_to(np.linspace(-102, -94, 80)[None, :], (60, 80)).copy()
    return ir_by_fhr, reflectivity_by_fhr, lat, lon


def test_config_uses_requested_modified_criteria():
    cfg = _config(
        Path("/tmp/input"), Path("/tmp/output"), "20260727", "12", 24,
        (-110, -80, 25, 50), bt_threshold_k=241,
        cloud_area_threshold_km2=60000, precipitation_threshold_dbz=25,
        precipitation_major_axis_threshold_km=100,
        convective_threshold_dbz=45, cloud_duration_hours=6,
        structural_duration_hours=4,
        overlap_threshold=0.5, cell_area_km2=100,
    )
    assert cfg["feature_type"] == "tb_pf_radar3d"
    assert cfg["mcs_tb_area_thresh"] == 60000
    assert cfg["mcs_tb_duration_thresh"] == 6
    assert cfg["mcs_pf_majoraxis_thresh"] == 100
    assert cfg["abs_ConvThres_aml"] == 45
    assert cfg["mcs_pf_durationthresh"] == 3
    assert cfg["othresh"] == 0.5


def test_official_pyflextrkr_pipeline_detects_synthetic_case():
    assert DEFAULT_PYFLEXTRKR_PYTHON.is_file()
    ir, refc, lat, lon = synthetic_frames()
    with tempfile.TemporaryDirectory(prefix="xgbffp-pyflex-test-", dir="/tmp") as tmp:
        result = prepare_and_run_pyflextrkr(
            ir, refc, lat, lon,
            run_date="20260727", cycle="12", case_dir=Path(tmp),
            extent=(-102, -94, 30, 36), cell_area_km2=100,
        )
    assert result.package_version == "2026.7.0"
    assert result.upstream_commit == "6a3a6435ee6b3a64ec411b9f2af38226d6f32850"
    assert result.official_steps_completed == [
        "idfeature_driver", "tracksingle_driver", "gettracknumbers",
        "trackstats_driver", "identifymcs_tb", "match_tbpf_tracks",
        "define_robust_mcs_radar",
    ]
    assert result.ir_duration_met
    assert result.structural_duration_met
    assert result.detected


def test_five_cloud_hours_do_not_meet_six_hour_requirement():
    ir, refc, lat, lon = synthetic_frames(frame_count=5, structural_frame_count=4)
    with tempfile.TemporaryDirectory(prefix="xgbffp-pyflex-short-", dir="/tmp") as tmp:
        result = prepare_and_run_pyflextrkr(
            ir, refc, lat, lon,
            run_date="20260727", cycle="12", case_dir=Path(tmp),
            extent=(-102, -94, 30, 36), cell_area_km2=100,
        )
    assert not result.ir_duration_met
    assert not result.detected


if __name__ == "__main__":
    test_config_uses_requested_modified_criteria()
    test_official_pyflextrkr_pipeline_detects_synthetic_case()
    test_five_cloud_hours_do_not_meet_six_hour_requirement()
    print("Actual PyFLEXTRKR HRRR adapter tests passed")
