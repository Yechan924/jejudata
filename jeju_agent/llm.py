"""LLM 공급자 추상화 (SDK 없이 requests만 사용).

지원: anthropic (Messages API, 도구호출), openai (Chat Completions, 도구호출), ollama (OpenAI 호환 /v1, 도구호출 지원 모델),
      offline (LLM 없음 → 규칙 기반 플래너). provider=auto 면 키/서버 존재 여부로 자동 선택.

메시지는 내부적으로 OpenAI 형식(role/content/tool_calls/tool)으로 다루고 공급자별로 변환한다.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

from .config import LLM_MODEL, LLM_PROVIDER, LLM_RETRIES, LLM_TIMEOUT, OLLAMA_BASE_URL

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini", "ollama": "qwen2.5:7b"}


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict | None = None
    usage: dict = field(default_factory=dict)


def detect_provider() -> str:
    if LLM_PROVIDER != "auto":
        return LLM_PROVIDER
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
        if r.ok:
            return "ollama"
    except Exception:  # noqa: BLE001
        pass
    return "offline"


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = provider or detect_provider()
        self.model = model or LLM_MODEL or DEFAULT_MODELS.get(self.provider, "")

    @property
    def available(self) -> bool:
        return self.provider != "offline"

    # ---------- 공개 API ----------
    def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1500) -> LLMReply:
        if self.provider == "anthropic":
            return self._anthropic(system, messages, tools, max_tokens)
        if self.provider in ("openai", "ollama"):
            return self._openai_compat(system, messages, tools, max_tokens)
        raise RuntimeError("LLM 공급자가 설정되지 않았습니다 (offline).")

    # ---------- 공통: 재시도 ----------
    @staticmethod
    def _post(url: str, body: dict, headers: dict, timeout: int) -> dict:
        """429/5xx·네트워크 오류는 지수 백오프로 최대 LLM_RETRIES 회 재시도. 4xx(인증·요청 오류)는 즉시 실패."""
        last: Exception | None = None
        for attempt in range(LLM_RETRIES + 1):
            try:
                r = requests.post(url, json=body, headers=headers, timeout=timeout)
                if r.status_code in (429, 500, 502, 503, 504, 529) and attempt < LLM_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if not r.ok:
                    detail = ""
                    try:
                        detail = r.json().get("error", {}).get("message", "")[:200]
                    except Exception:  # noqa: BLE001
                        detail = r.text[:200]
                    raise RuntimeError(f"HTTP {r.status_code} {detail}".strip())
                return r.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                if attempt < LLM_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise
        raise last or RuntimeError("LLM 요청 실패")

    # ---------- Anthropic ----------
    @staticmethod
    def _to_anthropic(messages: list[dict]) -> list[dict]:
        conv: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                conv.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
                conv.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(없음)"}]})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                    conv[-1]["content"].append(block)
                else:
                    conv.append({"role": "user", "content": [block]})
        return conv

    def _anthropic(self, system, messages, tools, max_tokens) -> LLMReply:
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": self._to_anthropic(messages), "temperature": 0}
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        data = self._post("https://api.anthropic.com/v1/messages", body, {
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"}, LLM_TIMEOUT)
        rep = LLMReply(raw=data, usage=data.get("usage", {}))
        for b in data.get("content", []):
            if b["type"] == "text":
                rep.text += b["text"]
            elif b["type"] == "tool_use":
                rep.tool_calls.append(ToolCall(b["id"], b["name"], b.get("input") or {}))
        return rep

    # ---------- OpenAI 호환 (OpenAI, Ollama) ----------
    def _openai_compat(self, system, messages, tools, max_tokens) -> LLMReply:
        conv = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant":
                am = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    am["tool_calls"] = [{"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)}} for tc in m["tool_calls"]]
                conv.append(am)
            elif m["role"] == "tool":
                conv.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                conv.append({"role": "user", "content": m["content"]})
        body = {"model": self.model, "messages": conv, "temperature": 0, "max_tokens": max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        if self.provider == "openai":
            url, headers = "https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}
        else:
            url, headers = f"{OLLAMA_BASE_URL}/v1/chat/completions", {}
        data = self._post(url, body, headers, LLM_TIMEOUT * (3 if self.provider == "ollama" else 1))
        msg = data["choices"][0]["message"]
        rep = LLMReply(text=msg.get("content") or "", raw=data, usage=data.get("usage", {}))
        for i, tc in enumerate(msg.get("tool_calls") or []):
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            rep.tool_calls.append(ToolCall(tc.get("id") or f"call_{i}", tc["function"]["name"], args))
        return rep
