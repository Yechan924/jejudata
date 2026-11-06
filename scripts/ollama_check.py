"""센터 PC(오프라인, API 키 없음) 로컬 LLM 점검 — Ollama 서버·모델·도구 호출·컨텍스트를 한 번에 확인한다.

  python scripts/ollama_check.py                # 서버 확인 → 모델 확인(없으면 pull 안내) → 도구 호출 1회 → Agent 질문 1개
  python scripts/ollama_check.py --pull         # 모델이 없으면 `ollama pull` 자동 실행
  python scripts/ollama_check.py --model qwen2.5:3b

권장 모델 (센터 PC: GTX 1060 6GB / RAM 32GB 기준)
  qwen2.5:7b   (4bit 4.7GB) — 한국어·도구 호출 양호. 컨텍스트 12k 로 두면 KV 캐시 일부가 RAM 으로 넘어가 느리지만(질문당 30~90초) 동작.
  qwen2.5:3b   (2.0GB)      — 빠름(10~30초). 복합 질문 정확도는 낮지만 도구 선택은 대체로 맞음.
  llama3.1:8b  (4.9GB)      — 도구 호출 지원, 한국어는 qwen 보다 약함.
설치: https://ollama.com/download (Windows 설치 후 자동 실행) → `ollama pull qwen2.5:7b` → .env 에 LLM_PROVIDER=ollama
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jeju_agent.config import OLLAMA_BASE_URL, OLLAMA_NUM_CTX  # noqa: E402

RECOMMENDED = ["qwen2.5:7b", "qwen2.5:3b", "llama3.1:8b"]


def ok(msg):
    print(f"  ✅ {msg}")


def bad(msg):
    print(f"  ❌ {msg}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="")
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--question", default="주말에 가장 붐비는 관광지는 어디야?")
    a = ap.parse_args()

    print(f"[1] Ollama 서버 {OLLAMA_BASE_URL}")
    try:
        tags = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3).json()
    except Exception as e:  # noqa: BLE001
        bad(f"연결 실패: {type(e).__name__}. Ollama 가 설치·실행 중인지 확인 (https://ollama.com/download, 트레이 아이콘)")
        return 1
    models = [m["name"] for m in tags.get("models", [])]
    ok(f"응답함. 설치된 모델 {len(models)}개: {', '.join(models) or '(없음)'}")

    from jeju_agent.llm import DEFAULT_MODELS
    model = a.model or next((m for m in RECOMMENDED if any(x == m or x.startswith(m) for x in models)), DEFAULT_MODELS["ollama"])
    print(f"[2] 모델 {model}")
    if not any(x == model or x.startswith(model) for x in models):
        if a.pull and shutil.which("ollama"):
            print(f"  … ollama pull {model} (수 GB, 수 분)")
            subprocess.run(["ollama", "pull", model], check=False)
        else:
            bad(f"없음 → 터미널에서  ollama pull {model}  실행 후 다시 (또는 --pull)")
            return 1
    ok("설치됨")

    print(f"[3] 도구 호출 1회 (num_ctx={OLLAMA_NUM_CTX})")
    from jeju_agent.llm import LLM
    llm = LLM("ollama", model)
    tool = [{"name": "congestion_rank", "description": "장소별 혼잡 순위", "parameters": {"type": "object", "properties": {"weekend_only": {"type": "boolean"}, "top_n": {"type": "integer"}}}}]
    t0 = time.time()
    try:
        rep = llm.chat("도구를 사용해 답하세요.", [{"role": "user", "content": "주말 혼잡 순위 상위 5곳을 도구로 조회해."}], tool, max_tokens=200)
    except Exception as e:  # noqa: BLE001
        bad(f"실패: {e}")
        return 1
    if rep.tool_calls:
        ok(f"도구 호출 됨: {rep.tool_calls[0].name} {rep.tool_calls[0].args} ({time.time() - t0:.0f}s)")
    else:
        bad(f"도구를 부르지 않고 텍스트로 답함 → 이 모델은 tools 미지원이거나 약함. 권장: {', '.join(RECOMMENDED)}  (응답: {rep.text[:80]!r})")
        return 1

    print(f"[4] Agent 질문: {a.question}")
    from jeju_agent.agent import JejuAgent
    t0 = time.time()
    res = JejuAgent(llm=llm).run(a.question)
    sec = time.time() - t0
    if res.mode.startswith("llm:"):
        ok(f"{res.mode} · 도구 {len(res.steps)}회 · LLM {res.llm_calls}회 · {sec:.0f}s · 검증 {res.verification}")
        print("  ─ 답변 첫 줄: " + res.answer.splitlines()[0][:120])
        if res.notes:
            print("  ─ 참고: " + " / ".join(res.notes))
        if sec > 120:
            print("  ⚠️ 느립니다. .env 에 LLM_MODEL=qwen2.5:3b 또는 OLLAMA_NUM_CTX=8192 를 시도하세요.")
    else:
        bad(f"LLM 경로가 실패해 규칙 모드로 폴백됨: {res.mode} / {[s.error for s in res.steps if s.error]}")
        return 1
    print("\n완료. .env 에  LLM_PROVIDER=ollama  와  LLM_MODEL=" + model + "  를 적고 run.bat / streamlit run app.py 를 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
