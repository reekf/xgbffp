from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import generate_dashboard_data as dashboard
import generate_interactive_map_data as map_data
import realtime_day2_workflow as day2


ROOT = Path(__file__).resolve().parents[1]


def test_day2_issue_valid_and_eligibility_contract():
    issue, valid, start, end = day2.day2_dates("20260728")
    assert issue == "20260728"
    assert valid == "20260729"
    assert start == datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 30, 12, tzinfo=timezone.utc)
    assert not day2.verification_is_eligible(
        issue, datetime(2026, 7, 30, 11, 59, tzinfo=timezone.utc)
    )
    assert day2.verification_is_eligible(
        issue, datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    )


def test_day2_map_metadata_and_members_do_not_require_v2(monkeypatch):
    frame = pd.DataFrame(
        {
            "Date": ["20260729"] * 3,
            "Lat": [35.0, 35.2, 35.4],
            "Lon": [-95.0, -94.8, -94.6],
            "ML_r40_Prob": [0.1, 0.2, 0.3],
            "ML_r60_Prob": [0.1, 0.2, 0.3],
            "ML_r75_Prob": [0.1, 0.2, 0.3],
            "ML_r100_Prob": [0.1, 0.2, 0.3],
            "WPC_ERO_Risk": [0.05, 0.15, 0.4],
        }
    )
    monkeypatch.setattr(map_data, "contour_segments", lambda *args: {})
    monkeypatch.setattr(map_data, "load_observations", lambda *args: {})
    payload = map_data.build_payload(
        frame,
        "20260729",
        "realtime",
        forecast_day=2,
        issue_date="20260728",
    )
    assert payload["forecast_day"] == 2
    assert payload["issue_date"] == "20260728"
    assert "ml_r60v2" not in payload["layers"]
    assert {"ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc"} <= set(payload["layers"])


def test_running_windows_are_30_day_and_seasonal_only():
    records = [{"date": "20260729"}]
    assert set(dashboard.select_windows(records)) == {"monthly", "seasonal"}


def test_day2_verification_scheduler_uses_completeness_catchup():
    catchup = (ROOT / "publish_missing_day2_verification_outputs.sh").read_text()
    cron = (ROOT / "realtime_ml.crontab").read_text()
    forecast_publisher = (ROOT / "publish_day2_ml_output.sh").read_text()
    verification_publisher = (ROOT / "publish_day2_verification_output.sh").read_text()

    assert 'status["valid_end_utc"]' in catchup
    assert 'status.get("verification_available") is True' in catchup
    assert 'required_layers = {"ml_r40", "ml_r60", "ml_r75", "ml_r100", "ml_mean", "wpc", "pp"}' in catchup
    assert 'required_truths = {"practically_perfect", "ufvs_40km"}' in catchup
    assert "./publish_missing_day2_verification_outputs.sh" in cron
    assert 'DAY2_VERIFY_CATCHUP:-1' in forecast_publisher
    assert "missing_layers" in verification_publisher
    assert "missing_truths" in verification_publisher
