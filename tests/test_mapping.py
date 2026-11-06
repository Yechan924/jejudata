"""이름 정규화 매핑(와이파이 장소→4-01 좌표, 정류소명→3-03 ID) 테스트."""
from __future__ import annotations

import pandas as pd

from jeju_agent.data.loader import JejuDB, name_key


def test_name_key():
    assert name_key("저지리 수동") == name_key("저지리수동")
    assert name_key("제주대학교[남]") == name_key("제주대학교")
    assert name_key("지하상가 1-2") == name_key("지하상가")
    assert name_key("13코스 #2-2") == name_key("13코스")
    assert name_key("탐라장애인종합복지관(노형로 방향)") != name_key("탐라장애인종합복지관")   # 괄호 안 내용은 유지


def _db_with(raw, wifi_usage, wifi_ap, bus_usage, bus_station):
    pd.DataFrame(wifi_usage).to_csv(raw / "와이파이 AP별 이용 현황_허브API_202509.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(wifi_ap).to_csv(raw / "와이파이 AP 현황_허브API.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(bus_usage).to_csv(raw / "버스 정류소별 이용 현황_허브API_202509.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(bus_station).to_csv(raw / "버스 정류소 정보_허브API.csv", index=False, encoding="utf-8-sig")
    db = JejuDB()
    db.load_all()
    return db


def test_normalized_mapping(tmp_path, monkeypatch):
    import jeju_agent.data.loader as L
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(L, "RAW_DIR", raw)
    days = ["20250901", "20250902"]
    wifi_usage = [{"base_date": d, "ap_place": p, "user_count": 50} for d in days
                  for p in ("돌문화공원", "저지리 수동", "제주대학교[남]", "버스정류소_위미3리(406000239)", "버스정류소_하귀농협_(방면)_[405000001]", "완전미지장소")]
    wifi_ap = [{"base_date": "20250915", "ap_group_name": "돌문화공원", "category": "관광지", "latitude": 33.45, "longitude": 126.66},
               {"base_date": "20250915", "ap_group_name": "저지리수동", "category": "마을", "latitude": 33.33, "longitude": 126.26},
               {"base_date": "20250915", "ap_group_name": "제주대학교", "category": "학교", "latitude": 33.46, "longitude": 126.56}]
    bus_station = [{"bus_station_id": "406000239", "bus_station_name": "위미3리", "latitude": 33.27, "longitude": 126.67},
                   {"bus_station_id": "405000001", "bus_station_name": "하귀농협", "latitude": 33.48, "longitude": 126.40},
                   {"bus_station_id": "405000002", "bus_station_name": "탐라장애인종합복지관(노형로 방향)", "latitude": 33.49, "longitude": 126.47}]
    bus_usage = [{"base_date": d, "bus_station_id": "0001", "bus_station_name": n, "get_on_off_type": "승차", "user_category": "일반", "pay_type": "카드", "user_count": 20}
                 for d in days for n in ("위미3리", "하귀 농협", "탐라장애인종합복지관 (노형로 방향)", "미지정류소")]
    bus_usage.append({"base_date": "20250903", "bus_station_id": "0001", "bus_station_name": None, "get_on_off_type": "승차", "user_category": "일반", "pay_type": "카드", "user_count": 5})
    db = _db_with(raw, wifi_usage, wifi_ap, bus_usage, bus_station)

    w = db.table("wifi_ap_usage").drop_duplicates("ap_place").set_index("ap_place")
    assert w.loc["돌문화공원", "ap_latitude"] == 33.45                       # 정확 일치
    assert w.loc["저지리 수동", "ap_latitude"] == 33.33                      # 공백 무시
    assert w.loc["제주대학교[남]", "ap_latitude"] == 33.46                   # 방향 꼬리표 무시
    assert w.loc["버스정류소_위미3리(406000239)", "ap_latitude"] == 33.27    # 괄호 속 정류소ID → 3-03
    assert w.loc["버스정류소_하귀농협_(방면)_[405000001]", "ap_latitude"] == 33.48
    assert pd.isna(w.loc["완전미지장소", "ap_latitude"])
    assert db.provenance["wifi_ap_usage"].unmatched == ["완전미지장소"]

    b = db.table("bus_station_usage")
    ids = b.drop_duplicates("bus_station_name").set_index("bus_station_name")["bus_station_id"]
    assert ids["위미3리"] == "406000239" and ids["하귀 농협"] == "405000001"
    assert ids["탐라장애인종합복지관 (노형로 방향)"] == "405000002"
    assert ids["미지정류소"].startswith("N_")
    assert b["bus_station_name"].isna().sum() == 0                          # 빈 이름은 같은 원본ID의 이름으로 채움 ('0001' → 마지막 이름)
    assert db.provenance["bus_station_usage"].unmatched == ["미지정류소"]
    assert "정규화" in db.provenance["bus_station_usage"].note
