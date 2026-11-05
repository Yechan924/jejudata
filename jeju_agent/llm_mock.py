"""시나리오 기반 가짜 LLM — 실제 API 키 없이 LLM 모드의 전체 경로(도구 호출 루프·검증·폴백)를 테스트한다.

사용:
    from jeju_agent.llm_mock import ScriptedLLM
    llm = ScriptedLLM([
        {"tool_calls": [("congestion_rank", {"weekend_only": True})]},   # 1번째 응답: 도구 호출
        {"text": "**한 줄 결론** — ..."},                                 # 2번째 응답: 최종 답변
    ])
    JejuAgent(db, llm).run("주말에 가장 붐비는 관광지는?")

text 에 "{top_place}" 같은 자리표시자를 쓰면 직전 도구 결과 첫 행의 컬럼 값으로 치환된다(근거 기반 답변 흉내).
"""
from __future__ import annotations

from .llm import LLM, LLMReply, ToolCall


class ScriptedLLM(LLM):
    provider = "mock"

    def __init__(self, script: list[dict], model: str = "scripted"):
        self.provider, self.model = "mock", model
        self.script = list(script)
        self.calls: list[dict] = []   # 실제로 받은 (system, messages, tools) 기록 — 프롬프트 검증용

    @property
    def available(self) -> bool:
        return True

    def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1500) -> LLMReply:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self.script:
            return LLMReply(text="(스크립트 소진) 지금까지 결과로 답합니다.")
        step = self.script.pop(0)
        if "raise" in step:
            raise RuntimeError(step["raise"])
        rep = LLMReply(usage={"input_tokens": 100, "output_tokens": 50})
        text = step.get("text", "")
        last_tool = next((m for m in reversed(messages) if m["role"] == "tool"), None)
        if "{" in text and last_tool:
            text = _fill(text, last_tool["content"])
        rep.text = text
        for i, (name, args) in enumerate(step.get("tool_calls", [])):
            rep.tool_calls.append(ToolCall(f"call_{len(self.calls)}_{i}", name, args))
        return rep


def _fill(text: str, tool_md: str) -> str:
    """도구 결과 마크다운 표의 첫 데이터 행에서 {컬럼명} 치환."""
    lines = [l for l in tool_md.splitlines() if l.startswith("|")]
    if len(lines) < 3:
        return text
    cols = [c.strip() for c in lines[0].strip("|").split("|")]
    vals = [c.strip() for c in lines[2].strip("|").split("|")]
    for c, v in zip(cols, vals):
        text = text.replace("{" + c + "}", v)
    return text
