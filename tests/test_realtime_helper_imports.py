#!/usr/bin/env python3
"""Regression checks for dynamically loaded realtime feature helpers."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from realtime_mcs_trigger_plot import (
    MCSDetectionResult,
    finalize_hrrr_only_gate,
    load_training_module_for_realtime,
    parse_args,
)
from audit_2026_mcs_archive import classification_from_summary  # noqa: E402


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


def test_hrrr_only_gate_uses_hrrr_result():
    base = MCSDetectionResult(True, 241.0, 60000.0, 1, 1, 70000.0, None)
    finalized = finalize_hrrr_only_gate(base, None, None, None, None)
    assert finalized.hrrr_triggered is True
    assert finalized.triggered is True
    assert list(finalized.model_results) == ["hrrr"]


def test_hrrr_gate_defaults():
    args = parse_args(["--date", "20260727"])
    assert (args.hrrr_cycle, args.fhr_start, args.fhr_end) == ("12", 0, 24)
    assert args.mcs_cloud_duration_hours == 3
    assert args.mcs_structural_duration_hours == 4


def test_archive_classification_ignores_stale_rap_gate_metadata():
    result = classification_from_summary(
        "20260727",
        {
            "mcs_detected": False,
            "hrrr_mcs_detected": True,
            "ir_duration_met": True,
            "structural_duration_met": True,
            "rap_mcs_detection": {"mcs_detected": False},
        },
    )
    assert result["mcs_eligible"] is True
    assert result["hrrr_criterion_met"] is True
    assert not any(key.startswith("rap_") for key in result)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        test_dynamic_helper_can_import_sibling_module(Path(directory))
    test_hrrr_only_gate_uses_hrrr_result()
    test_hrrr_gate_defaults()
    test_archive_classification_ignores_stale_rap_gate_metadata()
    print("Realtime helper sibling-import regression check passed.")
