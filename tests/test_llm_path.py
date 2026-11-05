"""LLM 모드 경로 테스트 (API 키 불필요): 도구 호출 루프, 숫자 검증, 한도 초과, 장애 폴백, 전송 내용 기록, 메시지 변환."""
from datetime import date

import pytest

from jeju_agent.agent import JejuAgent
from jeju_agent.config import SYNTHETIC_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.llm import LLM
from jeju_agent.llm_mock import ScriptedLLM


@pytest.fixture(scope="session")
def db():
    if not (SYNTHETIC_DIR / "wifi_ap_usage.csv").exists():
        from jeju_agent.data.synthetic import generate
        generate(date(2025, 3, 1), date(2025, 4, 30), scale=0.2)
    d = JejuDB(); d.load_all(); return d


def test_tool_loop_then_answer(db):
    llm = ScriptedLLM([
        {"text": "계획: 주말 혼잡 순위를 보겠습니다.", "tool_calls": [("congestion_rank", {"weekend_only": True, "top_n": 5})]},
        {"text": "**한 줄 결론** — 주말에 가장 붐비는 곳은 {ap_place}(혼잡지수 {avg_congestion_index})입니다.\n**근거**\n- 일평균 {avg_daily_users}명\n**해석과 제안**\n- 분산 유도\n**한계**\n- 합성 데이터"},
    ])
    res = JejuAgent(db, llm).run("주말에 가장 붐비는 관광지는?")
    assert res.mode == "llm:mock/scripted" and res.llm_calls == 2
    assert [s.tool for s in res.steps] == ["congestion_rank"] and res.steps[0].ok
    assert "한 줄 결론" in res.answer and "자료를 활용한 것" in res.answer
    assert res.verification["checked"] >= 1 and not res.verification["unsupported"]
    assert res.plan and "주말 혼잡 순위" in res.plan[0]
    kinds = [m["kind"] for m in res.llm_transcript]
    assert kinds[:2] == ["system", "user"] and "tool_result:congestion_rank" in kinds and kinds[-1] == "answer"
    # 시스템 프롬프트에 스키마·적재 상태가 들어갔는지
    sys_prompt = llm.calls[0]["system"]
    assert "wifi_ap_usage" in sys_prompt and "synthetic" in sys_prompt
    assert any(t["name"] == "congestion_rank" for t in llm.calls[0]["tools"])


def test_hallucinated_numbers_replaced_by_template(db):
    llm = ScriptedLLM([
        {"tool_calls": [("bus_station_rank", {"top_n": 3})]},
        {"text": "**한 줄 결론** — 1위 정류소의 일평균 승차는 987,654명이고 2위는 123,456명, 3위는 55,555명입니다."},
    ])
    res = JejuAgent(db, llm).run("이용 많은 정류소는?")
    assert res.notes and "템플릿 해설로 대체" in res.notes[0]
    assert "987,654" not in res.answer and "근거" in res.answer


def test_tool_error_is_fed_back_and_retried(db):
    llm = ScriptedLLM([
        {"tool_calls": [("place_profile", {"place": "존재하지않는곳"})]},
        {"tool_calls": [("place_profile", {"place": "성산"})]},
        {"text": "**한 줄 결론** — 성산일출봉 프로필입니다."},
    ])
    res = JejuAgent(db, llm).run("성산일출봉은 언제 붐비나?")
    assert [s.ok for s in res.steps] == [False, True]
    err_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert "도구 오류" in err_msg and "list_places" in err_msg


def test_tool_call_limit_forces_final_answer(db, monkeypatch):
    import jeju_agent.agent as A
    monkeypatch.setattr(A, "LLM_MAX_TOOL_CALLS", 2)
    llm = ScriptedLLM([{"tool_calls": [("describe_data", {})]}] * 3 + [{"text": "**한 줄 결론** — 마무리"}])
    res = JejuAgent(db, llm).run("어떤 데이터가 있어?")
    assert len(res.steps) <= 3 and "마무리" in res.answer
    assert "한도" in llm.calls[-1]["messages"][-1]["content"]


def test_llm_failure_falls_back_offline(db):
    llm = ScriptedLLM([{"raise": "HTTP 529 overloaded"}])
    res = JejuAgent(db, llm).run("주말에 가장 붐비는 관광지는?")
    assert res.mode.startswith("offline(fallback") and res.results
    assert res.steps[0].tool == "llm" and not res.steps[0].ok


def test_history_is_passed_for_followup(db):
    llm = ScriptedLLM([
        {"tool_calls": [("place_access", {"place": "성산일출봉"})]},
        {"text": "**한 줄 결론** — 성산일출봉 주변 정류소입니다."},
    ])
    history = [{"role": "user", "content": "성산일출봉은 언제 붐비나?"}, {"role": "assistant", "content": "주말 오후입니다."}]
    JejuAgent(db, llm).run("거기 가는 버스는?", history=history)
    msgs = llm.calls[0]["messages"]
    assert msgs[0]["content"] == "성산일출봉은 언제 붐비나?" and msgs[-1]["content"] == "거기 가는 버스는?"


def test_anthropic_message_conversion():
    """OpenAI 형식 내부 메시지 → Anthropic Messages 형식 변환 (tool_result 가 user 블록으로 묶이는지)."""
    l = LLM("anthropic", "x")
    messages = [{"role": "user", "content": "q"},
                {"role": "assistant", "content": "plan", "tool_calls": [{"id": "t1", "name": "a", "args": {}}, {"id": "t2", "name": "b", "args": {"k": 1}}]},
                {"role": "tool", "tool_call_id": "t1", "content": "r1"},
                {"role": "tool", "tool_call_id": "t2", "content": "r2"}]
    conv = l._to_anthropic(messages)
    assert conv[1]["role"] == "assistant" and [b["type"] for b in conv[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert conv[2]["role"] == "user" and len(conv[2]["content"]) == 2 and conv[2]["content"][1]["tool_use_id"] == "t2"
    assert len(conv) == 3


def test_offline_resolves_pronoun_from_history(db):
    from jeju_agent.llm import LLM
    ag = JejuAgent(db, LLM("offline"))
    history = [{"role": "user", "content": "성산일출봉은 언제 붐비나?"}, {"role": "assistant", "content": "성산일출봉은 주말이 붐빕니다."}]
    res = ag.run("거기 가는 버스 정류소는?", history=history)
    assert res.steps and res.steps[0].tool == "place_access" and "성산일출봉" in res.steps[0].args["place"]
    assert any("지시어" in p for p in res.plan) and res.followups
