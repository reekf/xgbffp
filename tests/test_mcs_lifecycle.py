#!/usr/bin/env python3
"""Unit tests for the modified Feng/PyFLEXTRKR-style HRRR lifecycle gate."""

from pathlib import Path
import sys

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcs_lifecycle import track_mcs_lifecycle


def synthetic_frames(frame_count=4, cloud_size=21, pf_width=15, max_dbz=50.0):
    ir_by_fhr = {}
    reflectivity_by_fhr = {}
    for fhr in range(frame_count):
        ir = np.full((40, 50), 280.0)
        reflectivity = np.zeros_like(ir)
        x0 = 3 + fhr
        ir[5 : 5 + cloud_size, x0 : x0 + cloud_size] = 220.0
        reflectivity[14:16, x0 + 2 : x0 + 2 + pf_width] = 30.0
        reflectivity[14, x0 + 8] = max_dbz
        ir_by_fhr[fhr] = ir
        reflectivity_by_fhr[fhr] = reflectivity
    return ir_by_fhr, reflectivity_by_fhr


def detect(ir_by_fhr, reflectivity_by_fhr):
    return track_mcs_lifecycle(
        ir_by_fhr,
        reflectivity_by_fhr,
        cell_area_km2=100.0,
    )


def test_full_modified_feng_contract_detects():
    result = detect(*synthetic_frames())
    assert result.detected
    assert result.ir_duration_met
    assert result.structural_duration_met
    assert result.max_joint_duration_hours == 4


def test_three_hourly_frames_do_not_satisfy_more_than_three_hours():
    result = detect(*synthetic_frames(frame_count=3))
    assert not result.detected
    assert result.max_joint_duration_hours == 3


def test_cloud_area_must_be_strictly_greater_than_40000_km2():
    result = detect(*synthetic_frames(cloud_size=20))
    assert not result.detected
    assert result.max_ir_duration_hours == 0


def test_precipitation_feature_major_axis_must_exceed_100_km():
    result = detect(*synthetic_frames(pf_width=7))
    assert not result.detected
    assert result.ir_duration_met
    assert not result.structural_duration_met


def test_convective_reflectivity_must_be_strictly_greater_than_45_dbz():
    result = detect(*synthetic_frames(max_dbz=45.0))
    assert not result.detected
    assert result.ir_duration_met
    assert not result.structural_duration_met
