"""LLM 모드 실키 검증 — 예시 질문을 LLM 모드와 오프라인 모드로 모두 돌려 비교표를 만든다.

  python scripts/llm_smoke.py                      # .env 의 키로 examples/questions.txt 전체
  python scripts/llm_smoke.py --limit 5            # 앞 5문항만 (비용 절약)
  python scripts/llm_smoke.py --questions my.txt   # 다른 질문 목록
  python scripts/llm_smoke.py --mock               # 키 없이 경로만 점검 (결과는 저장하지 않음)

산출물
  outputs/samples/batch_llm.json  — LLM 모드 결과(전송 내용·검증 결과 포함)
  docs/LLM_EVAL.md                — 문항별 비교표: 선택 도구, LLM 호출 수, 숫자 검증, 4단 형식 준수, 소요 시간, 토큰
검토 포인트: '형식' 열이 모두 ✓ 인지, '미확인 숫자'가 0에 가까운지, 오프라인과 도구 선택이 크게 다르면 왜 다른지(LLM이 더 나은 경우가 많다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jeju_agent.agent import JejuAgent  # noqa: E402
from jeju_agent.data.loader import JejuDB  # noqa: E402
from jeju_agent.llm import LLM  # noqa: E402

SECTIONS = ["한 줄 결론", "근거", "해석과 제안", "한계"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "examples" / "questions.txt"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()

    qs = [q.strip() for q in open(a.questions, encoding="utf-8") if q.strip() and not q.startswith("#")]
    if a.limit:
        qs = qs[: a.limit]
    db = JejuDB(); db.load_all()

    if a.mock:
        from jeju_agent.llm_mock import ScriptedLLM
        def make_llm():
            return ScriptedLLM([{"text": "계획: 혼잡 순위를 보겠습니다.", "tool_calls": [("congestion_rank", {"top_n": 5})]},
                                {"text": "**한 줄 결론** — {ap_place}이(가) 1위입니다.\n**근거**\n- 혼잡지수 {avg_congestion_index}\n**해석과 제안**\n- 분산\n**한계**\n- 합성"}])
    else:
        llm0 = LLM()
        if not llm0.available:
            print("LLM 키가 없습니다. .env 에 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 를 넣거나 --mock 으로 경로만 점검하세요.")
            return 1
        print(f"LLM: {llm0.provider} / {llm0.model}")
        def make_llm():
            return llm0

    offline = JejuAgent(db, LLM("offline"))
    rows, outputs = [], []
    for i, q in enumerate(qs, 1):
        print(f"[{i}/{len(qs)}] {q}")
        res = JejuAgent(db, make_llm()).run(q)
        off = offline.run_offline(q)
        fmt = all(s in res.answer for s in SECTIONS)
        rows.append({
            "q": q, "mode": res.mode, "tools": " → ".join(s.tool for s in res.steps),
            "off_tools": " → ".join(s.tool for s in off.steps), "llm_calls": res.llm_calls,
            "checked": res.verification.get("checked", 0), "unsupported": len(res.verification.get("unsupported", [])),
            "fmt": fmt, "replaced": any("템플릿" in n for n in res.notes), "sec": res.elapsed,
            "tok": (res.tokens or {}).get("input", 0) + (res.tokens or {}).get("output", 0),
            "err": "; ".join(s.error for s in res.steps if not s.ok)[:80],
            "review": sum(1 for x in res.limitations if x.startswith("LLM 자기 점검")) if any("자기 점검" in p for p in res.plan) else "-",
            "fu": "LLM" if any("후속 질문: LLM" in p for p in res.plan) else "규칙",
        })
        outputs.append(JejuAgent.to_dict(res))
        print(f"    {res.mode} · 도구 {rows[-1]['tools'] or '-'} · 검증 {rows[-1]['checked']}/{rows[-1]['unsupported']} · 형식 {'✓' if fmt else '✗'} · 자기점검 {rows[-1]['review']} · 후속 {rows[-1]['fu']} · {res.elapsed}s")

    if a.mock:
        print("\n(mock) 경로 점검 완료 — 파일은 저장하지 않습니다.")
        return 0

    out = ROOT / "outputs" / "samples" / "batch_llm.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(outputs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    md = [f"# LLM 모드 검증 결과", "",
          f"- 실행: {datetime.now():%Y-%m-%d %H:%M} · 모델 `{rows[0]['mode']}` · 문항 {len(rows)}개 · 데이터 {'합성 포함' if db.uses_synthetic() else '실데이터'}",
          f"- 형식 준수 {sum(r['fmt'] for r in rows)}/{len(rows)} · 템플릿 대체 {sum(r['replaced'] for r in rows)}건 · "
          f"미확인 숫자 합계 {sum(r['unsupported'] for r in rows)} / 검사 {sum(r['checked'] for r in rows)} · "
          f"평균 {sum(r['sec'] for r in rows) / len(rows):.1f}s · 토큰 합계 {sum(r['tok'] for r in rows):,} · LLM_EXTRAS={os.getenv('LLM_EXTRAS', 'followups')}", "",
          "| # | 질문 | LLM 선택 도구 | 오프라인 도구 | LLM 호출 | 검증(확인/미확인) | 형식 | 대체 | 자기점검 지적 | 후속질문 | 초 | 오류 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        md.append(f"| {i} | {r['q']} | {r['tools'] or '-'} | {r['off_tools'] or '-'} | {r['llm_calls']} | {r['checked']}/{r['unsupported']} | "
                  f"{'✓' if r['fmt'] else '✗'} | {'○' if r['replaced'] else ''} | {r['review']} | {r['fu']} | {r['sec']} | {r['err']} |")
    md += ["", "## 읽는 법", "- **검증**: 답변 속 숫자 중 근거표에서 확인된 수 / 확인되지 않은 수. 미확인이 20%를 넘으면 해설이 템플릿으로 **대체**된다.",
           "- **형식**: 한 줄 결론·근거·해석과 제안·한계 4단이 모두 있는지.",
           "- **자기점검 지적**: LLM_EXTRAS 에 review 가 있을 때 LLM이 자기 답변을 근거와 대조해 지적한 건수('-'는 미실행). **후속질문**: LLM 제안이 채택됐는지(실패 시 규칙).",
           "- LLM과 오프라인의 도구 선택이 다르면 LLM 쪽 선택이 질문 의도에 더 맞는지 `outputs/samples/batch_llm.json` 의 `plan`·`llm_transcript` 로 확인한다.",
           "", "## 샘플 답변", ""]
    for o in outputs[:3]:
        md += [f"### {o['question']}", "", "> " + o["answer"].replace("\n", "\n> "), ""]
    (ROOT / "docs" / "LLM_EVAL.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n저장: {out}\n      {ROOT / 'docs' / 'LLM_EVAL.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
