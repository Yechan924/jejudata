"""Agent 자율성 강화 테스트: LLM 후속 질문·자기 점검(LLM_EXTRAS), compare_periods 도구와 규칙 플래너 라우팅."""
from datetime import date

import pytest

from jeju_agent.agent import JejuAgent
from jeju_agent.config import SYNTHETIC_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.llm_mock import ScriptedLLM
from jeju_agent.planner_rules import plan
from jeju_agent.tools import call_tool


@pytest.fixture(scope="session")
def db():
    if not (SYNTHETIC_DIR / "wifi_ap_usage.csv").exists():
        from jeju_agent.data.synthetic import generate
        generate(date(2025, 3, 1), date(2025, 4, 30), scale=0.2)
    d = JejuDB(); d.load_all(); return d


ANSWER = "**한 줄 결론** — 주말에 가장 붐비는 곳은 {ap_place}(혼잡지수 {avg_congestion_index})입니다.\n**근거**\n- 일평균 {avg_daily_users}명\n**해석과 제안**\n- 분산\n**한계**\n- 합성"


def test_llm_followups_and_review(db, monkeypatch):
    monkeypatch.setenv("LLM_EXTRAS", "followups,review")
    llm = ScriptedLLM([
        {"tool_calls": [("congestion_rank", {"weekend_only": True, "top_n": 5})]},
        {"text": ANSWER},
        {"text": '{"issues": ["혼잡지수는 표본(와이파이) 기준이라 실제 방문객 수가 아닙니다"]}'},   # review
        {"text": '["그 장소는 언제 가장 붐비나?", "그 장소 가는 버스 정류소는 얼마나 붐비나?", "평일과 주말 차이가 큰 곳은?"]'},   # followups
    ])
    res = JejuAgent(db, llm).run("주말에 가장 붐비는 관광지는?")
    assert res.llm_calls == 4
    assert any("LLM 자기 점검" in x for x in res.limitations) and "LLM 자기 점검" in res.answer
    assert res.followups[:3] == ["그 장소는 언제 가장 붐비나?", "그 장소 가는 버스 정류소는 얼마나 붐비나?", "평일과 주말 차이가 큰 곳은?"]
    kinds = [m["kind"] for m in res.llm_transcript]
    assert "review" in kinds and "followups" in kinds
    assert any("자기 점검" in p for p in res.plan) and any("후속 질문" in p for p in res.plan)
    # 부가 호출에는 도구 스키마를 주지 않는다
    assert llm.calls[2]["tools"] is None and llm.calls[3]["tools"] is None


def test_llm_extras_fall_back_to_rules(db, monkeypatch):
    monkeypatch.setenv("LLM_EXTRAS", "followups,review")
    llm = ScriptedLLM([
        {"tool_calls": [("congestion_rank", {"weekend_only": True, "top_n": 5})]},
        {"text": ANSWER},
        {"raise": "boom"},                 # review 실패
        {"text": "JSON 아님"},               # followups 파싱 실패
    ])
    res = JejuAgent(db, llm).run("주말에 가장 붐비는 관광지는?")
    assert "한 줄 결론" in res.answer
    assert any("자기 점검 실패" in n for n in res.notes)
    assert res.followups and not any("후속 질문: LLM" in p for p in res.plan)   # 규칙 제안으로 대체


def test_extras_off_by_default_in_tests(db):
    llm = ScriptedLLM([{"tool_calls": [("congestion_rank", {"top_n": 3})]}, {"text": ANSWER}])
    res = JejuAgent(db, llm).run("가장 붐비는 관광지는?")
    assert res.llm_calls == 2


def test_compare_periods_tool(db):
    r = call_tool(db, "compare_periods", {"target": "wifi", "split": "halves", "top_n": 5})
    assert {"장소", "전반_일평균", "후반_일평균", "변화율"} <= set(r.table.columns) and len(r.table) <= 10
    assert r.facts and "전반" in r.facts[0]
    r2 = call_tool(db, "compare_periods", {"target": "bus_station", "split": "weekday_weekend", "top_n": 3})
    assert "평일_일평균" in r2.table.columns and r2.table["변화율"].is_monotonic_decreasing
    r3 = call_tool(db, "compare_periods", {"target": "bus_route", "split": "custom", "a_from": "2025-03-01", "a_to": "2025-03-15", "b_from": "2025-04-01", "b_to": "2025-04-15"})
    assert "A기간_일평균" in r3.table.columns
    with pytest.raises(ValueError):
        call_tool(db, "compare_periods", {"split": "custom", "a_from": "2025-03-01"})


def test_rule_planner_routes_comparisons(db):
    places = sorted(db.table("wifi_ap_usage")["ap_place"].dropna().unique().tolist())
    stations = sorted(db.table("bus_station_usage")["bus_station_name"].dropna().unique().tolist())
    routes = sorted(db.table("bus_route_usage")["bus_route_name"].dropna().unique().tolist())
    p = plan("3월 전반과 후반에 어디가 늘고 줄었어?", places, stations, routes, "2025-03-01", "2025-04-30")
    assert p[0][0] == "compare_periods" and p[0][1]["target"] == "wifi" and p[0][1]["split"] == "halves"
    p = plan("평일과 주말 차이가 큰 정류소는?", places, stations, routes, "2025-03-01", "2025-04-30")
    assert p[0][0] == "compare_periods" and p[0][1]["target"] == "bus_station" and p[0][1]["split"] == "weekday_weekend"
    p = plan("2025-03-01~2025-03-15 와 2025-04-01~2025-04-15 노선 이용 비교", places, stations, routes, "2025-03-01", "2025-04-30")
    assert p[0][0] == "compare_periods" and p[0][1]["split"] == "custom" and p[0][1]["b_from"] == "2025-04-01"
    p = plan("효율이 낮은 버스 노선은?", places, stations, routes, "2025-03-01", "2025-04-30")
    assert p[0][0] == "bus_route_efficiency"
