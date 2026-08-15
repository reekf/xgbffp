#!/usr/bin/env python3
"""Reconstruct the 12Z WPC-style Practically Perfect field from UFVS inputs.

This is an empirically fitted reconstruction, not an official WPC archive field.
The fixed recipe was selected against retained 2024-2025 continuous operational
PP grids.  Keeping it in one module prevents the realtime plotter, map builder,
and verification statistics from silently drifting to different definitions.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from scipy import ndimage as ndi


PP_RECIPE_ID = "wpcfit12z_g009_pr4sq_or5disk_s9p75_t3p5_w060060100_div3_v1"
PP_RECIPE_LABEL = "Reconstructed Practically Perfect (12Z WPC-fit recipe)"
PP_REQUIRED_SOURCES = ("ST4gFFG", "ST4gARI", "USGS", "LSRFLASH")
PP_OPTIONAL_SOURCES = ("LSRREG",)

# WPC continuous-product working grid used by the multi-case fit.
WORK_LAT0_DEG = 25.0
WORK_LON0_DEG = -129.8
WORK_SPACING_DEG = 0.09
WORK_NY = 276
WORK_NX = 721

# Winning fixed geometry from the six-case continuous-field sweep.
PROXY_RADIUS_CELLS = 4
OBSERVATION_RADIUS_CELLS = 5
GAUSSIAN_SIGMA_CELLS = 9.75
GAUSSIAN_TRUNCATE_SIGMA = 3.5
FFG_WEIGHT = 0.60
ARI_WEIGHT = 0.60
OBSERVATION_WEIGHT = 1.00
COMBINATION_DIVISOR = 3.0


def _points_array(points: object) -> np.ndarray:
    """Normalize one source's points to unique ``(latitude, longitude)`` rows."""
    if points is None:
        return np.empty((0, 2), dtype=float)
    if hasattr(points, "columns") and "Lat" in points.columns and "Lon" in points.columns:
        values = points[["Lat", "Lon"]].to_numpy(dtype=float)
    else:
        values = np.asarray(points, dtype=float)
    if values.size == 0:
        return np.empty((0, 2), dtype=float)
    values = np.atleast_2d(values)
    if values.shape[1] < 2:
        raise ValueError("UFVS points must provide latitude and longitude columns")
    values = values[:, :2]
    valid = (
        np.isfinite(values).all(axis=1)
        & (values[:, 0] >= 15.0)
        & (values[:, 0] <= 60.0)
        & (values[:, 1] >= -135.0)
        & (values[:, 1] <= -55.0)
    )
    return np.unique(values[valid], axis=0)


def _rasterize_nearest(points: np.ndarray) -> np.ndarray:
    field = np.zeros((WORK_NY, WORK_NX), dtype=bool)
    if points.size == 0:
        return field
    rows = np.rint((points[:, 0] - WORK_LAT0_DEG) / WORK_SPACING_DEG).astype(int)
    columns = np.rint((points[:, 1] - WORK_LON0_DEG) / WORK_SPACING_DEG).astype(int)
    valid = (
        (rows >= 0)
        & (rows < WORK_NY)
        & (columns >= 0)
        & (columns < WORK_NX)
    )
    field[rows[valid], columns[valid]] = True
    return field


def _disk(radius_cells: int) -> np.ndarray:
    rows, columns = np.mgrid[-radius_cells : radius_cells + 1, -radius_cells : radius_cells + 1]
    return rows * rows + columns * columns <= radius_cells * radius_cells


def _smooth(mask: np.ndarray) -> np.ndarray:
    return ndi.gaussian_filter(
        mask.astype(np.float32),
        sigma=GAUSSIAN_SIGMA_CELLS,
        mode="constant",
        cval=0.0,
        truncate=GAUSSIAN_TRUNCATE_SIGMA,
    ).astype(np.float32)


def build_working_grid_field(source_points: Mapping[str, object]) -> tuple[np.ndarray, dict]:
    """Create the fitted continuous PP field on the fixed 0.09-degree grid."""
    normalized = {
        source: _points_array(source_points.get(source))
        for source in (*PP_REQUIRED_SOURCES, *PP_OPTIONAL_SOURCES)
    }
    ffg = _rasterize_nearest(normalized["ST4gFFG"])
    ari = _rasterize_nearest(normalized["ST4gARI"])
    observation_rows = [
        normalized[source]
        for source in ("USGS", "LSRFLASH", "LSRREG")
        if normalized[source].size
    ]
    observations = _rasterize_nearest(
        np.concatenate(observation_rows, axis=0)
        if observation_rows
        else np.empty((0, 2), dtype=float)
    )

    proxy_structure = np.ones(
        (2 * PROXY_RADIUS_CELLS + 1, 2 * PROXY_RADIUS_CELLS + 1), dtype=bool
    )
    observation_structure = _disk(OBSERVATION_RADIUS_CELLS)
    ffg_component = _smooth(ndi.binary_dilation(ffg, structure=proxy_structure))
    ari_component = _smooth(ndi.binary_dilation(ari, structure=proxy_structure))
    observation_component = _smooth(
        ndi.binary_dilation(observations, structure=observation_structure)
    )
    field = (
        FFG_WEIGHT * ffg_component
        + ARI_WEIGHT * ari_component
        + OBSERVATION_WEIGHT * observation_component
    ) / COMBINATION_DIVISOR
    field = np.clip(field, 0.0, 1.0).astype(np.float32)
    metadata = {
        "recipe_id": PP_RECIPE_ID,
        "recipe_label": PP_RECIPE_LABEL,
        "cycle": "12Z-to-12Z only",
        "working_grid": {
            "lat0_deg": WORK_LAT0_DEG,
            "lon0_deg": WORK_LON0_DEG,
            "spacing_deg": WORK_SPACING_DEG,
            "shape": [WORK_NY, WORK_NX],
            "placement": "nearest cell",
        },
        "proxy_footprint": {"geometry": "square", "radius_cells": PROXY_RADIUS_CELLS},
        "observation_footprint": {
            "geometry": "circle",
            "radius_cells": OBSERVATION_RADIUS_CELLS,
        },
        "gaussian": {
            "sigma_cells": GAUSSIAN_SIGMA_CELLS,
            "truncate_sigma": GAUSSIAN_TRUNCATE_SIGMA,
        },
        "weights": {
            "ST4gFFG": FFG_WEIGHT,
            "ST4gARI": ARI_WEIGHT,
            "observations": OBSERVATION_WEIGHT,
            "divisor": COMBINATION_DIVISOR,
        },
        "source_point_counts": {
            source: int(len(points)) for source, points in normalized.items()
        },
    }
    return field, metadata


def sample_working_grid_nearest(
    field: np.ndarray, target_latitude: object, target_longitude: object
) -> tuple[np.ndarray, int]:
    """Nearest-neighbor sample the continuous working-grid field."""
    latitude = np.asarray(target_latitude, dtype=float)
    longitude = np.asarray(target_longitude, dtype=float)
    if latitude.shape != longitude.shape:
        raise ValueError("Target latitude and longitude shapes differ")
    rows = np.rint((latitude - WORK_LAT0_DEG) / WORK_SPACING_DEG).astype(int)
    columns = np.rint((longitude - WORK_LON0_DEG) / WORK_SPACING_DEG).astype(int)
    valid = (
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & (rows >= 0)
        & (rows < WORK_NY)
        & (columns >= 0)
        & (columns < WORK_NX)
    )
    sampled = np.zeros(latitude.shape, dtype=np.float32)
    sampled[valid] = np.asarray(field, dtype=np.float32)[rows[valid], columns[valid]]
    return sampled, int(np.count_nonzero(valid))


def reconstruct_pp(
    target_latitude: object,
    target_longitude: object,
    source_points: Mapping[str, object],
    source_available: Mapping[str, bool] | None = None,
) -> tuple[np.ndarray, dict]:
    """Build and sample one 12Z realtime PP reconstruction."""
    field, metadata = build_working_grid_field(source_points)
    sampled, mapped_count = sample_working_grid_nearest(
        field, target_latitude, target_longitude
    )
    availability = {
        source: bool((source_available or {}).get(source, source in source_points))
        for source in (*PP_REQUIRED_SOURCES, *PP_OPTIONAL_SOURCES)
    }
    metadata["source_available"] = availability
    metadata["required_sources_complete"] = all(
        availability[source] for source in PP_REQUIRED_SOURCES
    )
    metadata["optional_sources_used"] = [
        source for source in PP_OPTIONAL_SOURCES if availability[source]
    ]
    metadata["mapped_target_count"] = mapped_count
    metadata["target_count"] = int(np.asarray(target_latitude).size)
    metadata["maximum_probability"] = float(np.nanmax(sampled)) if sampled.size else 0.0
    return sampled, metadata
