"""실데이터 형식 변형(헤더 변형·기본값·엑셀·세미콜론 구분자·날짜 포맷)을 loader가 흡수하는지 확인한다."""
import pandas as pd
import pytest

from jeju_agent.data.loader import JejuDB


def test_alias_mapping_and_defaults_bus():
    # 공공데이터포털 '일별 시간대별 버스 승차 인원' 류: 승하차유형·이용자구분 없음, 헤더가 명세서와 다름
    df = pd.DataFrame({"기준일자": ["2019-03-01", "2019/3/2"], "시간": ["7", "8"], "정류장명": ["제주시청", "한라병원"],
                       "정류장번호": ["1", "2"], "승차인원수": ["12", "5"]})
    info = {}
    out = JejuDB.standardize(df, "bus_station_usage", info)
    assert out["base_date"].tolist() == ["20190301", "20190302"]
    assert out["bus_station_name"].tolist() == ["제주시청", "한라병원"]
    assert out["user_count"].tolist() == [12, 5]
    assert out["get_on_off_type"].unique().tolist() == ["승차"]
    assert info["defaulted"]["get_on_off_type"] == "승차"
    assert "bus_station_name" in info["alias_mapped"] and "user_count" in info["alias_mapped"]


def test_exact_mapping_wins_over_alias():
    df = pd.DataFrame({"날짜": ["20250101"], "시간대": ["9"], "이용자 수": ["3"], "정류소명": ["A"], "승하차유형": ["하차"]})
    info = {}
    out = JejuDB.standardize(df, "bus_station_usage", info)
    assert out["get_on_off_type"].iloc[0] == "하차"
    assert "get_on_off_type" not in info["defaulted"] and not info["alias_mapped"]


def test_semicolon_and_excel_files(tmp_path):
    p = tmp_path / "버스 정류소별 이용 현황.csv"
    p.write_text("날짜;시간대;정류소명;승하차유형;이용자 수\n20250101;7;제주시청;승차;11\n", encoding="cp949")
    df = JejuDB.read_csv(p)
    assert list(df.columns) == ["날짜", "시간대", "정류소명", "승하차유형", "이용자 수"]
    x = tmp_path / "wifi_ap_usage.xlsx"
    pd.DataFrame({"날짜": ["20250101"], "AP장소": ["성산일출봉"], "사용자 아이디": ["u1"]}).to_excel(x, index=False)
    out = JejuDB.standardize(JejuDB.read_csv(x), "wifi_ap_usage")
    assert out["ap_place"].iloc[0] == "성산일출봉" and out["user_id"].iloc[0] == "u1"


def test_assign_files_recognizes_variants(tmp_path):
    for n in ["4-05 와이파이 AP별 이용 현황_2025.csv", "wifi_ap_usage.xlsx", "버스 정류소별 이용 현황.csv", "unknown_file.csv"]:
        (tmp_path / n).write_text("a\n1\n", encoding="utf-8")
    a = JejuDB.assign_files(tmp_path)
    assert len(a["wifi_ap_usage"]) == 2 and len(a["bus_station_usage"]) == 1
    assert not any("unknown" in f.name for v in a.values() for f in v)


def test_info_table_dedup(tmp_path, monkeypatch):
    import jeju_agent.data.loader as L
    raw = tmp_path / "raw"; raw.mkdir()
    (raw / "bus_station.csv").write_text(
        "날짜,정류소 아이디,정류소명,위도,경도\n20250101,S1,구역,33.1,126.1\n20250102,S1,신역,33.2,126.2\n20250101,S2,둘,33.3,126.3\n", encoding="utf-8")
    monkeypatch.setattr(L, "RAW_DIR", raw)
    db = L.JejuDB(); p = db.load_dataset("bus_station")
    assert p.source == "raw" and p.rows == 2
    assert db.table("bus_station").set_index("bus_station_id").loc["S1", "bus_station_name"] == "신역"


@pytest.mark.parametrize("v,exp", [("2025-03-01", "20250301"), ("20250301 00:00:00", "20250301"), ("2025.3.1", "20250301"), ("이상값", "이상값")])
def test_base_date_normalization(v, exp):
    out = JejuDB.standardize(pd.DataFrame({"날짜": [v]}), "weather_daily")
    assert out["base_date"].iloc[0] == exp
