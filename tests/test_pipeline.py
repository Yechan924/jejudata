"""엔드투엔드 테스트. 합성 데이터가 없으면 작은 규모로 생성해서 돌린다.  실행: pytest -q"""
from datetime import date

import pytest

from jeju_agent.config import SYNTHETIC_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.gate import apply_gate
from jeju_agent.tools import call_tool, tool_specs
from jeju_agent.tools.base import ToolResult
import pandas as pd


@pytest.fixture(scope="session")
def db():
    if not (SYNTHETIC_DIR / "wifi_ap_usage.csv").exists():
        from jeju_agent.data.synthetic import generate
        generate(date(2025, 3, 1), date(2025, 4, 30), scale=0.2)
    d = JejuDB()
    d.load_all()
    return d


def test_core_datasets_loaded(db):
    for k in ["wifi_ap_usage", "bus_station_usage", "bus_route_usage", "weather_daily"]:
        assert db.has(k) and len(db.table(k)) > 0


def test_column_standardization_korean_headers(db):
    df = pd.DataFrame({"날짜": ["20250101"], "정류소명": ["제주시청"], "이용자 수": ["7"], "승하차유형": ["승차"]})
    out = JejuDB.standardize(df, "bus_station_usage")
    assert {"base_date", "bus_station_name", "user_count", "get_on_off_type"} <= set(out.columns)
    assert out["user_count"].iloc[0] == 7


@pytest.mark.parametrize("name,args", [
    ("congestion_rank", {"top_n": 5}),
    ("congestion_rank", {"weekend_only": True, "sort_by": "users"}),
    ("place_profile", {"place": "성산"}),
    ("bus_station_rank", {"sort_by": "senior_share"}),
    ("bus_route_efficiency", {}),
    ("bus_route_efficiency", {"route": "251"}),
    ("hourly_profile", {"station": "한라병원"}),
    ("place_access", {"place": "함덕"}),
    ("weather_effect", {"target": "wifi"}),
    ("describe_data", {}),
])
def test_tools_run(db, name, args):
    r = call_tool(db, name, args)
    assert isinstance(r, ToolResult) and not r.table.empty and r.facts


def test_gate_removes_identifiers_and_masks(db):
    df = pd.DataFrame({"user_id": ["a", "b"], "ap_place": ["x", "y"], "users": [3, 50]})
    r = ToolResult("t", "t", df)
    g = apply_gate(r)
    assert "user_id" not in g.table.columns
    assert g.table["users"].iloc[0] == "<10" and g.table["users"].iloc[1] == 50


def test_sql_tool_blocks_raw_rows(db):
    with pytest.raises(ValueError):
        call_tool(db, "run_sql", {"sql": "select * from wifi_ap_usage limit 5"})
    with pytest.raises(ValueError):
        call_tool(db, "run_sql", {"sql": "delete from wifi_ap_usage"})
    r = call_tool(db, "run_sql", {"sql": "select ap_place, count(distinct user_id) users from wifi_ap_usage group by ap_place"})
    assert "users" in r.table.columns


def test_offline_agent_answers(db):
    from jeju_agent.agent import JejuAgent
    from jeju_agent.llm import LLM
    a = JejuAgent(db, LLM("offline"))
    for q in ["주말에 가장 붐비는 관광지는?", "251번 노선 시간대별 이용", "비 오면 어디가 한산해지나", "어르신이 많이 타는 정류소는?"]:
        res = a.run(q)
        assert res.results and "한 줄 결론" in res.answer
        assert all(s.ok for s in res.steps)


def test_tool_specs_are_valid_json_schema():
    for t in tool_specs():
        assert t["parameters"]["type"] == "object"
        assert t["name"] and t["description"]


# ---------- 고도화 항목 ----------
def test_gate_uses_official_threshold_10(db):
    df = pd.DataFrame({"ap_place": ["a", "b", "c"], "users": [9, 10, 300]})
    g = apply_gate(ToolResult("t", "t", df))
    assert g.table["users"].tolist() == ["<10", 10, 300]


def test_number_verification_flags_hallucinated_numbers(db):
    from jeju_agent.verify import verify_numbers
    r = ToolResult("t", "t", pd.DataFrame({"ap_place": ["성산일출봉"], "avg_daily_users": [63.4], "tourist_share": [0.93]}),
                   facts=["혼잡 1위 성산일출봉(일평균 63명)"])
    ok = verify_numbers("성산일출봉이 일평균 63명, 관광 비중 93%로 1위입니다.", [r])
    assert ok.ok and not ok.unsupported
    bad = verify_numbers("성산일출봉 일평균 1,234명, 관광 비중 93%, 외국인 4,567명.", [r])
    assert not bad.ok and set(bad.unsupported) == {"1,234", "4,567"}


@pytest.mark.parametrize("name,args", [("congestion_forecast", {"top_n": 5}), ("congestion_forecast", {"place": "성산"}), ("monthly_brief", {})])
def test_new_tools(db, name, args):
    r = call_tool(db, name, args)
    assert not r.table.empty and r.facts and r.method


def test_forecast_within_observed_range(db):
    r = call_tool(db, "congestion_forecast", {"top_n": 30})
    t = r.table
    assert ((t.predicted_users >= t.range_low * 0.9 - 1) & (t.predicted_users <= t.range_high * 1.1 + 1)).all()


def test_offline_answer_has_attribution_and_no_transcript(db):
    from jeju_agent.agent import JejuAgent
    from jeju_agent.config import ATTRIBUTION
    from jeju_agent.llm import LLM
    res = JejuAgent(db, LLM("offline")).run("이번 주말 어디가 붐빌까?")
    assert ATTRIBUTION in res.answer and res.llm_transcript == [] and res.results[0].name == "congestion_forecast"


def test_llm_finish_replaces_hallucinated_answer(db):
    """LLM 답변에 근거표에 없는 숫자가 많으면 템플릿으로 대체되고 메모가 남는다."""
    from jeju_agent.agent import JejuAgent
    from jeju_agent.llm import LLM
    a = JejuAgent(db, LLM("offline"))
    res = a._run_tool("congestion_rank", {"top_n": 3}, [])
    out = a._finish("q", "총 98,765명이 방문했고 54,321명이 외국인이며 12,345명이 주말에 왔습니다.", [res], [], 1, {}, 0.0, [])
    assert "한 줄 결론" in out.answer and out.notes and "대체" in out.notes[0]


def test_png_chart_renders(db):
    from jeju_agent.charts import render_png
    png = render_png(call_tool(db, "bus_route_efficiency", {"route": "251"}))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_sql_tool_rejects_identifier_columns(db):
    with pytest.raises(ValueError):
        call_tool(db, "run_sql", {"sql": "select user_id, count(*) n from wifi_ap_usage group by user_id"})
