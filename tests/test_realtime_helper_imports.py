#!/usr/bin/env python3
"""Regression checks for dynamically loaded realtime feature helpers."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from realtime_mcs_trigger_plot import (
    MCSDetectionResult,
    combine_hrrr_rap_gate,
    load_training_module_for_realtime,
    parse_args,
    rap_aws_url,
)


def test_dynamic_helper_can_import_sibling_module(tmp_path):
    sibling = tmp_path / "mode_case_catalog.py"
    sibling.write_text("CATALOG_SENTINEL = 'loaded-from-sibling'\n")
    helper = tmp_path / "generated_helper.py"
    helper.write_text("from mode_case_catalog import CATALOG_SENTINEL\n")

    parent = str(tmp_path.resolve())
    assert parent not in sys.path
    sys.modules.pop("mode_case_catalog", None)

    try:
        module = load_training_module_for_realtime(
            radius_km=60,
            script_dir=tmp_path,
            explicit_script=str(helper),
            original_root=Path("/original-root"),
            local_root=Path("/local-root"),
        )

        assert module.CATALOG_SENTINEL == "loaded-from-sibling"
        assert parent not in sys.path
    finally:
        sys.modules.pop("mode_case_catalog", None)


def test_dual_model_gate_requires_both_models():
    base = MCSDetectionResult(True, 241.0, 60000.0, 1, 1, 70000.0, None)
    combined = combine_hrrr_rap_gate(
        base,
        {
            "mcs_detected": False,
            "ir_available": True,
            "ir_required": True,
            "ir_duration_met": True,
            "structural_duration_met": False,
            "rap_cycle": "09",
        },
        None, None, None, None,
    )
    assert combined.hrrr_triggered is True
    assert combined.rap_triggered is False
    assert combined.triggered is False


def test_rap_defaults_align_with_hrrr_valid_window():
    args = parse_args(["--date", "20260727"])
    assert (args.hrrr_cycle, args.fhr_start, args.fhr_end) == ("12", 0, 24)
    assert (args.rap_cycle, args.rap_fhr_start, args.rap_fhr_end) == ("09", 3, 27)
    assert rap_aws_url("20260727", "09", 3).endswith(
        "/rap.20260727/rap.t09z.awp130pgrbf03.grib2"
    )


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        test_dynamic_helper_can_import_sibling_module(Path(directory))
    test_dual_model_gate_requires_both_models()
    test_rap_defaults_align_with_hrrr_valid_window()
    print("Realtime helper sibling-import regression check passed.")
