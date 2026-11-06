"""날씨 실데이터 경로 테스트.

기상자료개방포털 ASOS 일자료 CSV 헤더 형식(cp949)을 흉내 낸 작은 fixture 로
 1) scripts/fetch_weather.py --kma-csv 변환이 5-02 컬럼을 만드는지
 2) 변환 없이 data/raw 에 넣어도 별칭 규칙으로 읽히는지
 3) 실데이터 기간과 겹치지 않는 합성 날씨는 적재되지 않고(비활성), 겹치는 실데이터 날씨는 weather_effect 가 동작하는지
를 확인한다. 숫자는 형식 확인용이며 실제 관측값이 아니다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
KMA_HEADER = ("지점,지점명,일시,평균기온(°C),최저기온(°C),최저기온 시각(hhmi),최고기온(°C),최고기온 시각(hhmi),"
              "강수 계속시간(hr),일강수량(mm),최대 순간 풍속(m/s),최대 순간 풍속 풍향(16방위),최대 순간 풍속 시각(hhmi),최대 풍속(m/s),평균 풍속(m/s)")


def _kma_csv(path: Path, days=("2025-09-01", "2025-09-02", "2025-09-03", "2025-09-04", "2025-09-05", "2025-09-06", "2025-09-07", "2025-09-08")):
    lines = [KMA_HEADER]
    for code, name in ((184, "제주"), (189, "서귀포")):
        for i, d in enumerate(days):
            rain = "" if i % 2 == 0 else f"{5.0 + i:.1f}"     # 무강수일은 빈칸(기상청 표기)
            lines.append(f"{code},{name},{d},24.{i},21.0,0530,28.0,1420,{'' if not rain else '3.0'},{rain},9.{i},270,1500,6.0,3.{i}")
    path.write_text("\n".join(lines) + "\n", encoding="cp949")


def _load_script():
    spec = importlib.util.spec_from_file_location("fetch_weather", ROOT / "scripts" / "fetch_weather.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_kma_csv_conversion(tmp_path):
    src = tmp_path / "OBS_ASOS_DD.csv"
    _kma_csv(src)
    fw = _load_script()
    rows = fw.from_kma_csv(src)
    assert len(rows) == 16
    r0 = rows[0]
    assert r0["base_date"] == "20250901" and r0["observatory_code"] == "184" and r0["pt_month"] == "09"
    assert r0["daily_rainfall"] == "0.0"            # 빈칸 → 0
    assert rows[1]["daily_rainfall"] == "6.0" and rows[1]["lowest_temperature_time"] == "0530"
    assert rows[1]["maximum_wind_speed"] == "9.1" and rows[1]["maximum_wind_speed_direction"] == "270"


def test_kma_csv_dropped_in_raw_is_recognized(tmp_path, monkeypatch):
    import jeju_agent.data.loader as L
    raw = tmp_path / "raw"
    raw.mkdir()
    _kma_csv(raw / "일일 날씨 정보_기상청.csv")
    monkeypatch.setattr(L, "RAW_DIR", raw)
    db = L.JejuDB()
    files = db.find_files("weather_daily", raw)
    assert [f.name for f in files] == ["일일 날씨 정보_기상청.csv"]
    df = db.standardize(db.read_csv(files[0]), "weather_daily")
    assert df["observatory_code"].iloc[0] == "184" and df["base_date"].iloc[0] == "20250901"
    assert df["lowest_temperature_time"].iloc[0] == "0530" and float(df["average_temperature"].iloc[0]) == 24.0
    assert np.isnan(df["daily_rainfall"].iloc[0]) and float(df["daily_rainfall"].iloc[1]) == 6.0


def _real_core(raw: Path, days: list[str]):
    """실데이터처럼 보이는 작은 4-05·3-04 (허브 API 집계본 형식)."""
    wifi = [{"base_date": d.replace("-", ""), "ap_place": p, "user_count": 100 + 50 * (p == "A해변") * (i % 2)}
            for i, d in enumerate(days) for p in ("A해변", "B시장")]
    pd.DataFrame(wifi).to_csv(raw / "와이파이 AP별 이용 현황_허브API_202509.csv", index=False, encoding="utf-8-sig")
    bus = [{"base_date": d.replace("-", ""), "bus_station_id": "1", "bus_station_name": "정류소1", "get_on_off_type": "승차", "user_category": "일반", "pay_type": "카드", "user_count": 30}
           for d in days]
    pd.DataFrame(bus).to_csv(raw / "버스 정류소별 이용 현황_허브API_202509.csv", index=False, encoding="utf-8-sig")


def test_synthetic_weather_not_mixed_with_real_core(tmp_path, monkeypatch):
    import jeju_agent.data.loader as L
    raw = tmp_path / "raw"
    raw.mkdir()
    _real_core(raw, [f"2025-09-{d:02d}" for d in range(1, 9)])
    monkeypatch.setattr(L, "RAW_DIR", raw)
    db = L.JejuDB()
    db.load_all()
    assert db.provenance["wifi_ap_usage"].source == "raw"
    assert db.provenance["weather_daily"].source == "missing" and "겹치지 않아" in db.provenance["weather_daily"].note
    assert not db.has("weather_daily") and not db.has("weather_station")
    assert db.provenance["wifi_daily_users"].source == "missing"   # 4-06 합성도 기간 불일치 → 비활성
    from jeju_agent.tools import call_tool
    with pytest.raises(ValueError, match="날씨 데이터"):
        call_tool(db, "weather_effect", {"target": "wifi"})


def test_weather_effect_with_real_period_weather(tmp_path, monkeypatch):
    import jeju_agent.data.loader as L
    raw = tmp_path / "raw"
    raw.mkdir()
    days = [f"2025-09-{d:02d}" for d in range(1, 9)]
    _real_core(raw, days)
    (raw / "weather_daily").mkdir()
    _kma_csv(raw / "weather_daily" / "일일 날씨 정보_기상청_202509.csv", tuple(days))
    monkeypatch.setattr(L, "RAW_DIR", raw)
    db = L.JejuDB()
    db.load_all()
    assert db.provenance["weather_daily"].source == "raw"
    assert db.provenance["weather_station"].source == "raw" and "내장" in db.provenance["weather_station"].note   # 5-01 없으면 내장 관측소
    assert len(db.table("weather_station")) == 4
    from jeju_agent.tools import call_tool
    r = call_tool(db, "weather_effect", {"target": "wifi", "rain_mm": 1.0})
    t = r.table.set_index("ap_place")
    assert t.loc["A해변", "rain_days"] == 4 and t.loc["A해변", "dry_days"] == 4
    assert t.loc["B시장", "weekday_rain_effect"] == 0.0 or t.loc["B시장", "weekend_rain_effect"] == 0.0
