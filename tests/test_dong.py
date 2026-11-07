"""읍면동 유동인구 결합 도구(dong_population_compare) — 합성 와이파이에서 만든 가짜 유동인구 CSV로 연결·상관·게이트를 확인한다."""
from datetime import date

import pandas as pd
import pytest

import jeju_agent.data.loader as L
from jeju_agent.config import SYNTHETIC_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.gate import apply_gate
from jeju_agent.tools import call_tool
from jeju_agent.tools.dong import admin_dong, compare_dong, wifi_by_dong


def test_admin_dong_mapping():
    assert admin_dong("제주특별자치도 제주시 화북이동") == "화북동"
    assert admin_dong("제주특별자치도 제주시 아라일동") == "아라동"
    assert admin_dong("제주특별자치도 제주시 구좌읍 상도리") == "구좌읍"
    assert admin_dong("서귀포시 강정동") == "대천동"
    assert admin_dong("제주시 이도2동") == "이도2동"
    assert admin_dong("제주특별자치도 서귀포시 서귀동") is None
    assert admin_dong("제주특별자치도") is None


@pytest.fixture(scope="module")
def db_with_pop(tmp_path_factory):
    if not (SYNTHETIC_DIR / "wifi_ap_usage.csv").exists():
        from jeju_agent.data.synthetic import generate
        generate(date(2025, 3, 1), date(2025, 4, 30), scale=0.2)
    base = JejuDB(); base.load_all()
    w, linked = wifi_by_dong(base)
    assert linked > 0.5 and not w.empty
    # 가짜 유동인구: 와이파이 × 40 + 잡음, 성별 2행으로 나눠 '합산' 경로까지 확인. 헤더는 허브 응답을 흉내낸 영문 camelCase
    rows = []
    for r in w.itertuples():
        for g, share in (("남", 0.55), ("여", 0.45)):
            rows.append({"baseDate": r.date.replace("-", ""), "sigunguName": "제주시", "emdName": r.dong, "gender": g, "ageGroup": "전체",
                         "floatingPopulation": round(r.wifi * 40 * share + (hash(r.dong + r.date) % 50), 1)})
    raw = tmp_path_factory.mktemp("raw_pop")
    (raw / "dong_population").mkdir()
    pd.DataFrame(rows).to_csv(raw / "dong_population" / "읍면동 단위 내국인 유동인구_허브API_202503.csv", index=False, encoding="utf-8-sig")
    orig = L.RAW_DIR
    L.RAW_DIR = raw
    try:
        db = JejuDB(); db.load_all()
        yield db
    finally:
        L.RAW_DIR = orig


def test_extra_dataset_loaded_and_mapped(db_with_pop):
    db = db_with_pop
    assert db.has("dong_population")
    p = db.provenance["dong_population"]
    assert p.source == "raw" and p.rows > 0
    assert {"dong_name", "population", "base_date"} <= set(db.table("dong_population").columns)
    assert db.table("dong_population")["population"].notna().all()


def test_compare_and_tool(db_with_pop):
    r = compare_dong(db_with_pop)
    assert "error" not in r, r
    assert r["n_dong"] >= 3 and r["rho"] > 0.8          # 유동인구 = 와이파이×40 이므로 순위 상관이 높아야 한다
    res = call_tool(db_with_pop, "dong_population_compare", {"top_n": 5})
    assert len(res.table) <= 5 and "읍면동" in res.table.columns and "와이파이/유동인구(‰)" in res.table.columns
    g = apply_gate(res)
    assert "manage_no" not in g.table.columns
    assert any("순위 상관" in f for f in res.facts)


def test_tool_without_data_raises(tmp_path):
    orig = L.RAW_DIR
    L.RAW_DIR = tmp_path            # 유동인구 파일이 없는 환경
    try:
        db = JejuDB(); db.load_all()
    finally:
        L.RAW_DIR = orig
    assert not db.has("dong_population")
    with pytest.raises(ValueError):
        call_tool(db, "dong_population_compare", {})


def test_rule_planner_routes_population():
    from jeju_agent.planner_rules import plan
    p = plan("읍면동별 유동인구 알려줘", [], [], [], "2025-03-01", "2025-04-30", {"dong_population": True})
    assert p[0][0] == "dong_population_compare"
    p2 = plan("읍면동별 유동인구 알려줘", [], [], [], "2025-03-01", "2025-04-30", {"dong_population": False})
    assert p2[0][0] != "dong_population_compare"
