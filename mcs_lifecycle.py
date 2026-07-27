"""PyFLEXTRKR-style lifecycle screening for HRRR MCS forecast gates.

This is a small operational adapter, not a vendored copy of PyFLEXTRKR.  It
uses the modified Feng et al. (2019) U.S. database contract needed by XGBFFP:
identify connected cold cloud shields, link overlapping objects through time,
measure an embedded precipitation feature, and require deep convection for a
configurable duration.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi


@dataclass
class LifecycleResult:
    detected: bool
    ir_duration_met: bool
    structural_duration_met: bool
    max_ir_duration_hours: int
    max_joint_duration_hours: int
    selected_fhr: int | None
    selected_mask: np.ndarray | None
    best_track: dict | None
    hourly_records: list[dict]


def _precipitation_feature_metrics(
    cloud_mask: np.ndarray,
    reflectivity: np.ndarray | None,
    *,
    precipitation_threshold_dbz: float,
    precipitation_major_axis_threshold_km: float,
    convective_threshold_dbz: float,
    cell_area_km2: float,
) -> dict:
    if reflectivity is None:
        return {
            "max_major_axis_km": 0.0,
            "max_reflectivity_dbz": None,
            "feature_count": 0,
            "qualifying_feature_count": 0,
        }
    feature_mask = (
        cloud_mask
        & np.isfinite(reflectivity)
        & (reflectivity >= float(precipitation_threshold_dbz))
    )
    labels, feature_count = ndi.label(feature_mask, structure=np.ones((3, 3), dtype=int))
    cell_width_km = float(np.sqrt(cell_area_km2))
    max_major_axis_km = 0.0
    max_reflectivity_dbz = None
    feature_metrics = []
    for label in range(1, int(feature_count) + 1):
        mask = labels == label
        coordinates = np.argwhere(mask).astype(float)
        if len(coordinates) <= 1:
            major_axis_km = 0.0
        else:
            covariance = np.cov(coordinates, rowvar=False, bias=True)
            eigenvalues = np.linalg.eigvalsh(covariance)
            major_axis_km = float(4.0 * np.sqrt(max(0.0, float(eigenvalues[-1]))) * cell_width_km)
        feature_max_dbz = float(np.nanmax(reflectivity[mask]))
        feature_metrics.append((major_axis_km, feature_max_dbz))
        max_major_axis_km = max(max_major_axis_km, major_axis_km)
        max_reflectivity_dbz = (
            feature_max_dbz
            if max_reflectivity_dbz is None
            else max(max_reflectivity_dbz, feature_max_dbz)
        )
    return {
        "max_major_axis_km": max_major_axis_km,
        "max_reflectivity_dbz": max_reflectivity_dbz,
        "feature_count": int(feature_count),
        "qualifying_feature_count": sum(
            major_axis_km > float(precipitation_major_axis_threshold_km)
            and maximum_dbz > float(convective_threshold_dbz)
            for major_axis_km, maximum_dbz in feature_metrics
        ),
    }


def track_mcs_lifecycle(
    ir_by_fhr: dict[int, np.ndarray],
    reflectivity_by_fhr: dict[int, np.ndarray],
    *,
    bt_threshold_k: float = 241.0,
    cloud_area_threshold_km2: float = 40000.0,
    precipitation_threshold_dbz: float = 25.0,
    precipitation_major_axis_threshold_km: float = 100.0,
    convective_threshold_dbz: float = 45.0,
    duration_hours: int = 4,
    overlap_threshold: float = 0.5,
    cell_area_km2: float = 9.0,
) -> LifecycleResult:
    """Track qualifying HRRR cloud objects and return the MCS lifecycle gate.

    Consecutive objects are linked when their overlap divided by the smaller
    object's area meets ``overlap_threshold``. A valid lifecycle requires a
    cold shield larger than 40,000 km2 containing a >=25 dBZ precipitation
    feature with major axis longer than 100 km and reflectivity above 45 dBZ.
    Every condition must hold for ``duration_hours`` consecutive frames.
    """
    previous: list[dict] = []
    all_states: list[dict] = []
    hourly_records: list[dict] = []
    shape = None
    for fhr in sorted(ir_by_fhr):
        bt = np.asarray(ir_by_fhr[fhr], dtype=float)
        if bt.ndim != 2:
            raise ValueError(f"IR f{fhr:02d} must be 2-D, got {bt.shape}")
        if shape is None:
            shape = bt.shape
        elif bt.shape != shape:
            raise ValueError(f"IR grids change shape at f{fhr:02d}: {shape} -> {bt.shape}")
        cold_mask = np.isfinite(bt) & (bt < float(bt_threshold_k))
        labels, component_count = ndi.label(cold_mask, structure=np.ones((3, 3), dtype=int))
        counts = np.bincount(labels.ravel()) if component_count else np.array([0])
        reflectivity = reflectivity_by_fhr.get(fhr)
        if reflectivity is not None:
            reflectivity = np.asarray(reflectivity, dtype=float)
            if reflectivity.shape != bt.shape:
                raise ValueError(
                    f"Reflectivity/IR shape mismatch at f{fhr:02d}: "
                    f"{reflectivity.shape} != {bt.shape}"
                )
        current: list[dict] = []
        for label in range(1, int(component_count) + 1):
            pixel_count = int(counts[label])
            area_km2 = float(pixel_count * cell_area_km2)
            if area_km2 <= float(cloud_area_threshold_km2):
                continue
            mask = labels == label
            pf = _precipitation_feature_metrics(
                mask,
                reflectivity,
                precipitation_threshold_dbz=float(precipitation_threshold_dbz),
                precipitation_major_axis_threshold_km=float(
                    precipitation_major_axis_threshold_km
                ),
                convective_threshold_dbz=float(convective_threshold_dbz),
                cell_area_km2=float(cell_area_km2),
            )
            structure_ok = bool(
                pf["max_major_axis_km"] > float(precipitation_major_axis_threshold_km)
                and pf["max_reflectivity_dbz"] is not None
                and pf["max_reflectivity_dbz"] > float(convective_threshold_dbz)
                and pf["qualifying_feature_count"] > 0
            )
            best_previous = None
            best_previous_score = None
            for candidate in previous:
                if int(candidate["fhr"]) != int(fhr) - 1:
                    continue
                intersection = int(np.count_nonzero(mask & candidate["mask"]))
                overlap = intersection / max(1, min(pixel_count, int(candidate["pixel_count"])))
                if overlap < float(overlap_threshold):
                    continue
                score = (int(candidate["ir_duration_hours"]), overlap)
                if best_previous_score is None or score > best_previous_score:
                    best_previous = candidate
                    best_previous_score = score
            if best_previous is None:
                ir_duration = 1
                joint_duration = 1 if structure_ok else 0
                track_hours = [int(fhr)]
            else:
                ir_duration = int(best_previous["ir_duration_hours"]) + 1
                joint_duration = (
                    int(best_previous["joint_duration_hours"]) + 1 if structure_ok else 0
                )
                track_hours = [*best_previous["track_hours"], int(fhr)]
            state = {
                "fhr": int(fhr),
                "label": int(label),
                "mask": mask,
                "pixel_count": pixel_count,
                "area_km2": area_km2,
                "precipitation_feature_major_axis_km": float(pf["max_major_axis_km"]),
                "max_reflectivity_dbz": pf["max_reflectivity_dbz"],
                "precipitation_feature_count": int(pf["feature_count"]),
                "qualifying_feature_count": int(pf["qualifying_feature_count"]),
                "structure_ok": structure_ok,
                "ir_duration_hours": ir_duration,
                "joint_duration_hours": joint_duration,
                "track_hours": track_hours,
            }
            current.append(state)
            all_states.append(state)
        previous = current
        hourly_records.append(
            {
                "fhr": int(fhr),
                "cold_component_count": int(component_count),
                "qualifying_cloud_count": len(current),
                "max_cloud_area_km2": max((state["area_km2"] for state in current), default=0.0),
                "max_precipitation_feature_major_axis_km": max(
                    (state["precipitation_feature_major_axis_km"] for state in current), default=0.0
                ),
                "max_reflectivity_dbz": max(
                    (
                        float(state["max_reflectivity_dbz"])
                        for state in current
                        if state["max_reflectivity_dbz"] is not None
                    ),
                    default=None,
                ),
                "reflectivity_available": reflectivity is not None,
            }
        )

    ir_candidates = [
        state
        for state in all_states
        if state["ir_duration_hours"] >= int(duration_hours)
    ]
    joint_candidates = [
        state
        for state in all_states
        if state["joint_duration_hours"] >= int(duration_hours)
    ]
    selection_pool = joint_candidates or ir_candidates or all_states
    selected = max(
        selection_pool,
        key=lambda state: (
            int(state["joint_duration_hours"]),
            int(state["ir_duration_hours"]),
            float(state["area_km2"]),
        ),
        default=None,
    )
    best_track = None
    if selected is not None:
        best_track = {
            key: value
            for key, value in selected.items()
            if key != "mask"
        }
    return LifecycleResult(
        detected=bool(joint_candidates),
        ir_duration_met=bool(ir_candidates),
        structural_duration_met=bool(joint_candidates),
        max_ir_duration_hours=max((int(state["ir_duration_hours"]) for state in all_states), default=0),
        max_joint_duration_hours=max(
            (int(state["joint_duration_hours"]) for state in all_states), default=0
        ),
        selected_fhr=int(selected["fhr"]) if selected is not None else None,
        selected_mask=np.asarray(selected["mask"], dtype=bool) if selected is not None else None,
        best_track=best_track,
        hourly_records=hourly_records,
    )
