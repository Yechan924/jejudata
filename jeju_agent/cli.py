"""명령행 실행.

  python -m jeju_agent.cli "주말에 가장 붐비는 관광지는?"          # 한 번 질문
  python -m jeju_agent.cli --offline "251번 노선 시간대별 이용"      # LLM 없이
  python -m jeju_agent.cli --batch examples/questions.txt --out outputs/batch.json
  python -m jeju_agent.cli --status                                 # 적재 데이터·LLM 상태
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from .agent import JejuAgent
from .config import OUTPUT_DIR
from .data.loader import JejuDB
from .llm import LLM
from .tools.base import df_to_md


def main(argv=None):
    ap = argparse.ArgumentParser(description="제주 혼잡·이동 진단 Agent CLI")
    ap.add_argument("question", nargs="?", help="질문")
    ap.add_argument("--offline", action="store_true", help="LLM 없이 규칙 기반으로 실행")
    ap.add_argument("--batch", help="질문이 한 줄에 하나씩 들어있는 파일")
    ap.add_argument("--out", help="결과 JSON 저장 경로")
    ap.add_argument("--status", action="store_true", help="데이터·LLM 상태만 출력")
    ap.add_argument("--show-tables", action="store_true", help="도구 결과 표도 출력")
    ap.add_argument("--brief", metavar="YYYY-MM", help="월간 브리핑을 HTML 리포트로 저장")
    ap.add_argument("--png", action="store_true", help="도구 결과 차트를 outputs/charts/ 에 PNG로 저장")
    a = ap.parse_args(argv)

    db = JejuDB()
    db.load_all()
    llm = LLM("offline") if a.offline else LLM()
    agent = JejuAgent(db, llm)

    if a.status:
        print(f"엔진: {db.engine} / LLM: {llm.provider} {llm.model}")
        print(df_to_md(db.summary()))
        return 0

    if a.brief:
        from .report import save_report
        res = agent.run(f"{a.brief} 월간 브리핑")
        print(res.answer)
        print("리포트:", save_report(res, OUTPUT_DIR / f"brief_{a.brief}.html"))
        return 0

    questions = []
    if a.batch:
        questions = [q.strip() for q in open(a.batch, encoding="utf-8") if q.strip() and not q.startswith("#")]
    elif a.question:
        questions = [a.question]
    else:
        ap.print_help()
        return 1

    outputs = []
    for q in questions:
        res = agent.run(q)
        print("=" * 80)
        print(f"Q: {q}\n[{res.mode}, {res.elapsed}s, 도구 {len(res.steps)}회, LLM {res.llm_calls}회]")
        for i, p in enumerate(res.plan, 1):
            print(f"  계획 {i}. {p}")
        print()
        print(res.answer)
        if res.followups:
            print("\n이어서 물어보기: " + " · ".join(res.followups))
        if a.show_tables:
            for r in res.results:
                print(f"\n--- {r.title} ({r.name})\n{df_to_md(r.table, 20)}")
        if a.png:
            from .charts import render_png, save_png
            for r in res.results:
                png = render_png(r)
                if png:
                    print("  PNG:", save_png(png, OUTPUT_DIR / "charts" / f"{r.name}_{datetime.now():%H%M%S}.png"))
        for s in res.steps:
            print(f"  · {s.tool} {json.dumps(s.args, ensure_ascii=False)} → {'OK' if s.ok else 'ERR ' + s.error} ({s.seconds}s, {s.rows}행) {s.gate}")
        outputs.append(JejuAgent.to_dict(res))

    out = a.out or (OUTPUT_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.json")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(outputs, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n결과 저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
