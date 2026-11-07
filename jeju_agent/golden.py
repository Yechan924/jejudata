"""골든 회귀셋 — 예시 질문별 '선택 도구 + 핵심 숫자'를 지문(fingerprint)으로 고정해 코드 변경 뒤에도 같은 결과가 나오는지 확인한다.

- 지문은 오프라인(규칙) 모드로 만든다: LLM이 끼지 않아 같은 데이터·같은 코드면 바이트 단위로 같아야 한다.
- 데이터는 저장소 동봉 축약본(data/demo)으로 고정한다. 사용자의 data/raw 가 있어도 골든 비교에는 쓰지 않는다.
- 저장 위치: tests/golden/demo_golden.json  (갱신: python scripts/reproduce.py --update-golden)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import ROOT

GOLDEN_PATH = ROOT / "tests" / "golden" / "demo_golden.json"
QUESTIONS_PATH = ROOT / "examples" / "questions.txt"
LABEL_COLS = ["ap_place", "bus_station_name", "bus_route_name", "장소", "정류소", "노선", "대상", "지표", "dow", "date", "hour"]
EXTRA_QUESTIONS = ["눈에 띄는 게 있어?", "__discover__"]   # __discover__ = 첫 화면 자율 탐색(JejuAgent.discover)


def load_questions(path: Path = QUESTIONS_PATH) -> list[str]:
    qs = [q.strip() for q in path.read_text(encoding="utf-8").splitlines() if q.strip() and not q.startswith("#")]
    return qs + EXTRA_QUESTIONS


def fingerprint(res) -> dict:
    """AgentResult → 비교 가능한 작은 dict. 표 전체가 아니라 (도구 순서, 결과별 행 수, 첫 행 라벨·숫자, 첫 사실)만 담는다."""
    out = {"tools": [s.tool for s in res.steps], "ok": all(s.ok for s in res.steps), "results": []}
    for r in res.results:
        t = r.table
        first = {}
        if len(t):
            row = t.iloc[0]
            for c in t.columns:
                v = row[c]
                if c in LABEL_COLS and isinstance(v, str):
                    first[c] = v
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and pd.notna(v) and c not in ("lat", "lon", "latitude", "longitude"):
                    first[c] = round(float(v), 2)
                if len(first) >= 5:
                    break
        out["results"].append({"tool": r.name, "rows": int(len(t)), "first": first, "fact": (r.facts[0] if r.facts else "")[:160]})
    return out


def diff(expected: dict, actual: dict) -> list[str]:
    """두 지문의 차이를 사람이 읽는 문장 목록으로. 비어 있으면 동일."""
    msgs = []
    if expected.get("tools") != actual.get("tools"):
        msgs.append(f"도구 순서 {expected.get('tools')} → {actual.get('tools')}")
    if expected.get("ok") != actual.get("ok"):
        msgs.append(f"도구 성공 여부 {expected.get('ok')} → {actual.get('ok')}")
    er, ar = expected.get("results", []), actual.get("results", [])
    if len(er) != len(ar):
        msgs.append(f"결과 수 {len(er)} → {len(ar)}")
    for i, (e, a) in enumerate(zip(er, ar)):
        if e["rows"] != a["rows"]:
            msgs.append(f"[{i}] {e['tool']} 행 수 {e['rows']} → {a['rows']}")
        for k, v in e["first"].items():
            av = a["first"].get(k)
            same = (abs(float(v) - float(av)) < 1e-6) if isinstance(v, (int, float)) and isinstance(av, (int, float)) else v == av
            if not same:
                msgs.append(f"[{i}] {e['tool']} 첫 행 {k}: {v} → {av}")
        if e["fact"] != a["fact"]:
            msgs.append(f"[{i}] {e['tool']} 첫 사실: '{e['fact'][:60]}…' → '{a['fact'][:60]}…'")
    return msgs


def run_all(agent, questions: list[str] | None = None) -> dict[str, dict]:
    """모든 질문을 오프라인으로 돌려 {질문: 지문}."""
    out = {}
    for q in questions or load_questions():
        res = agent.discover(top_n=3) if q == "__discover__" else agent.run_offline(q)
        out[q] = fingerprint(res)
    return out


def save(fps: dict, path: Path = GOLDEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fps, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: Path = GOLDEN_PATH) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
