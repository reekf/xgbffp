import pandas as pd

import generate_interactive_map_data as map_data


def _grid(date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": [date, date, date],
            "Lat": [40.0, 40.0, 41.0],
            "Lon": [-100.0, -99.0, -100.0],
        }
    )


def test_realtime_verification_preserves_issued_wpc_layer(tmp_path, monkeypatch):
    date = "20260729"
    realtime_dir = tmp_path / "verified"
    realtime_dir.mkdir()
    monkeypatch.setattr(map_data, "REALTIME_DIR", realtime_dir)
    monkeypatch.setattr(map_data, "REALTIME_WPC_DIR", tmp_path / "wpc")

    forecast = _grid(date)
    for radius in map_data.RADII:
        forecast[f"ML_r{radius}_Prob"] = [0.1, 0.2, 0.3]
    forecast["WPC_ERO_Risk"] = [0.05, 0.15, 0.4]
    forecast.to_parquet(
        realtime_dir
        / f"realtime_verified_v33_multiradius_r40_r60_r75_r100_{date}.parquet"
    )

    verification = _grid(date)
    verification["PP_Any flood proxy"] = [0.0, 0.25, 0.8]
    verification["UFVS_ANY"] = [0, 1, 0]
    verification.to_parquet(
        realtime_dir
        / f"realtime_ufvs_verified_v33_multiradius_r40_r60_r75_r100_{date}.parquet"
    )

    combined = map_data.load_realtime(date)

    assert combined["WPC_ERO_Risk"].tolist() == [0.05, 0.15, 0.4]
    assert combined["PP_Any flood proxy"].tolist() == [0.0, 0.25, 0.8]
    assert combined["UFVS_ANY"].tolist() == [0, 1, 0]


def test_realtime_wpc_fallback_accepts_current_cache_name(tmp_path, monkeypatch):
    date = "20260730"
    realtime_dir = tmp_path / "verified"
    wpc_dir = tmp_path / "wpc"
    realtime_dir.mkdir()
    wpc_dir.mkdir()
    monkeypatch.setattr(map_data, "REALTIME_DIR", realtime_dir)
    monkeypatch.setattr(map_data, "REALTIME_WPC_DIR", wpc_dir)

    forecast = _grid(date)
    for radius in map_data.RADII:
        forecast[f"ML_r{radius}_Prob"] = [0.1, 0.2, 0.3]
    forecast.to_parquet(
        realtime_dir
        / f"realtime_verified_v33_multiradius_r40_r60_r75_r100_{date}.parquet"
    )

    wpc = _grid(date)
    wpc["WPC_ERO_Risk"] = [0.05, 0.15, 0.4]
    wpc.to_parquet(
        wpc_dir / f"wpc_ero_day1_issue{date}_valid{date}_12to12_3rows.parquet"
    )

    combined = map_data.load_realtime(date)

    assert combined["WPC_ERO_Risk"].tolist() == [0.05, 0.15, 0.4]
