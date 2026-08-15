import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operational_pp_reconstruction as pp


def _center_point():
    return np.asarray([[40.0, -100.0]])


def test_fixed_recipe_uses_weighted_components_and_expected_geometry():
    sources = {
        "ST4gFFG": _center_point(),
        "ST4gARI": np.empty((0, 2)),
        "USGS": np.empty((0, 2)),
        "LSRFLASH": np.empty((0, 2)),
        "LSRREG": np.empty((0, 2)),
    }
    field, metadata = pp.build_working_grid_field(sources)
    row = int(round((40.0 - pp.WORK_LAT0_DEG) / pp.WORK_SPACING_DEG))
    column = int(round((-100.0 - pp.WORK_LON0_DEG) / pp.WORK_SPACING_DEG))
    base = np.zeros((pp.WORK_NY, pp.WORK_NX), dtype=bool)
    base[row, column] = True
    structure = np.ones(
        (2 * pp.PROXY_RADIUS_CELLS + 1, 2 * pp.PROXY_RADIUS_CELLS + 1),
        dtype=bool,
    )
    expected = pp._smooth(pp.ndi.binary_dilation(base, structure=structure)) * 0.2
    np.testing.assert_allclose(field, expected, rtol=0, atol=1e-7)
    assert metadata["recipe_id"] == pp.PP_RECIPE_ID
    assert metadata["cycle"] == "12Z-to-12Z only"


def test_reconstruction_samples_nearest_working_grid_and_records_completeness():
    sources = {source: _center_point() for source in pp.PP_REQUIRED_SOURCES}
    values, metadata = pp.reconstruct_pp(
        np.asarray([40.0]),
        np.asarray([-100.0]),
        sources,
        source_available={source: True for source in pp.PP_REQUIRED_SOURCES},
    )
    assert values.shape == (1,)
    assert 0.0 < values[0] <= 1.0
    assert metadata["required_sources_complete"] is True
    assert metadata["mapped_target_count"] == 1
    assert metadata["optional_sources_used"] == []
