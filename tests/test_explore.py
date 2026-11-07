"""자율 탐색(explore_anomalies)·첫 화면 discover() 테스트 — 합성 데이터만 사용."""
from datetime import date

import pytest

from jeju_agent.agent import JejuAgent
from jeju_agent.config import SYNTHETIC_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.llm import LLM
from jeju_agent.llm_mock import ScriptedLLM
from jeju_agent.planner_rules import plan
from jeju_agent.tools import call_tool, tool_specs


@pytest.fixture(scope="module")
def db():
    if not (SYNTHETIC_DIR / "wifi_ap_usage.csv").exists():
        from jeju_agent.data.synthetic import generate
        generate(date(2025, 3, 1), date(2025, 4, 30), scale=0.2)
    d = JejuDB(); d.load_all(); return d


def test_tool_registered():
    assert "explore_anomalies" in {t["name"] for t in tool_specs()}


def test_finds_injected_spike_and_drop(db):
    """정류소 하나에 특정 날 승차를 6배로, 다른 정류소는 10%로 바꾸면 그 둘이 급증·급감으로 잡혀야 한다."""
    b = db.frames["bus_station_usage"]
    on = b[b["get_on_off_type"] == "승차"]
    daily = on.groupby(["bus_station_name", "date"])["user_count"].sum().reset_index()
    med = daily.groupby("bus_station_name")["user_count"].median().sort_values(ascending=False)
    big = med.index[0]
    other = med.index[1]
    dates = sorted(daily["date"].unique())
    d_spike, d_drop = dates[len(dates) // 2], dates[len(dates) // 2 + 3]
    orig = b.copy()
    try:
        m1 = (b["bus_station_name"] == big) & (b["date"] == d_spike)
        m2 = (b["bus_station_name"] == other) & (b["date"] == d_drop)
        b.loc[m1, "user_count"] = b.loc[m1, "user_count"] * 6
        b.loc[m2, "user_count"] = (b.loc[m2, "user_count"] * 0.1).round().clip(lower=1)
        r = call_tool(db, "explore_anomalies", {"target": "bus_station", "top_n": 10})
        t = r.table
        assert set(["rank", "구분", "대상", "유형", "날짜", "관측값", "평소", "배율", "설명"]).issubset(t.columns)
        assert (t["구분"] == "정류소").all()
        sp = t[(t["대상"] == big) & (t["유형"] == "급증")]
        assert len(sp) == 1 and str(sp.iloc[0]["날짜"]) == d_spike and sp.iloc[0]["배율"] >= 1.8
        dr = t[(t["대상"] == other) & (t["유형"] == "급감")]
        assert len(dr) == 1 and str(dr.iloc[0]["날짜"]) == d_drop and dr.iloc[0]["배율"] <= 0.55
        assert r.extra["questions"][0].endswith("?") or "노선" in r.extra["questions"][0]
        assert any("탐색 범위" in f for f in r.facts)
        assert r.map_points is not None and len(r.map_points) >= 1
    finally:
        db.frames["bus_station_usage"] = orig


def test_all_targets_and_diversity(db):
    r = call_tool(db, "explore_anomalies", {"target": "all", "top_n": 6})
    assert 1 <= len(r.table) <= 6
    assert r.table["rank"].tolist() == list(range(1, len(r.table) + 1))
    # 유형이 2종 이상 걸렸다면 상위 선택도 2종 이상이어야 한다(다양성)
    if r.table["유형"].nunique() >= 2:
        assert r.table.head(3)["유형"].nunique() >= 2
    assert set(r.datasets) <= {"wifi_ap_usage", "bus_station_usage", "bus_route_usage"}


def test_top_n_bounds(db):
    assert len(call_tool(db, "explore_anomalies", {"top_n": 0}).table) == 3   # 0·None → 기본 3
    assert len(call_tool(db, "explore_anomalies", {"top_n": 1}).table) == 1
    assert len(call_tool(db, "explore_anomalies", {"top_n": 99}).table) <= 20


def test_planner_routes_open_questions(db):
    places, stations, routes = ["성산일출봉"], ["제주버스터미널"], ["251(a)"]
    for q, tgt in (("눈에 띄는 게 있어?", None), ("요즘 특이한 점 찾아줘", None), ("노선 중에 이상한 데 없어?", "bus_route"),
                   ("정류소 알아서 둘러봐", "bus_station"), ("관광지에서 눈에 띄는 변화는?", "wifi"), ("뭐부터 봐야 해?", None)):
        p = plan(q, places, stations, routes, "2025-03-01", "2025-04-30")
        assert p[0][0] == "explore_anomalies", q
        assert p[0][1].get("target") == tgt, q


def test_discover_offline(db):
    res = JejuAgent(db, LLM("offline")).discover(top_n=3)
    assert [s.tool for s in res.steps] == ["congestion_rank", "explore_anomalies"]
    assert all(s.ok for s in res.steps)
    assert "한 줄 결론" in res.answer and "오늘의 발견" in res.results[1].title
    assert len(res.results[1].table) <= 3
    assert res.followups and res.plan and res.mode == "offline" and res.llm_calls == 0


def test_discover_with_llm_and_number_check(db):
    """LLM 해설 경로: 근거에 없는 숫자를 쓰면 템플릿으로 대체된다."""
    llm = ScriptedLLM([{"text": "**한 줄 결론** — 어제 123,456명이 몰렸습니다.\n**근거**\n- 987,654명\n**해석과 제안**\n- x\n**한계**\n- y"}])
    res = JejuAgent(db, llm).discover(top_n=3, use_llm=True)
    assert res.llm_calls >= 1
    assert "123,456" not in res.answer and "템플릿 해설로 대체" in " ".join(res.notes)


def test_run_offline_explore_answer(db):
    res = JejuAgent(db, LLM("offline")).run_offline("눈에 띄는 게 있어?")
    assert res.results and res.results[0].name == "explore_anomalies"
    assert "자율 탐색" in " ".join(res.plan)
