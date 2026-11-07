"""원클릭 재현 — 심사위원·운영진이 한 번의 명령으로 "문서에 쓴 결과가 코드에서 그대로 나오는가"를 확인한다.

  python scripts/reproduce.py                 # 동봉 축약본(data/demo)으로: 데이터 점검 → 18문항 오프라인 실행 → 골든 지문 비교 → 샘플 산출물
  python scripts/reproduce.py --data auto     # data/raw(전체 실데이터)가 있으면 그걸로 실행 (골든 비교는 demo 전용이라 생략)
  python scripts/reproduce.py --update-golden # 코드·데이터를 의도적으로 바꾼 뒤 골든 지문을 다시 저장
  python scripts/reproduce.py --pytest        # 끝에 pytest 전체도 실행
  python scripts/reproduce.py --llm           # LLM 키가 있으면 scripts/llm_smoke.py 까지 실행(비용 발생)

산출물
  outputs/samples/batch_offline.json   18문항 오프라인 결과(도구 호출·근거표·한계)
  outputs/samples/sample_report.html   첫 질문의 HTML 리포트(PNG 차트 내장)
  outputs/samples/discover.json        첫 화면 자율 탐색 결과
  outputs/samples/REPRODUCE.md         이번 실행 요약(환경·데이터·골든 비교표)
종료 코드: 0 = 모두 통과, 1 = 골든 불일치 또는 도구 오류 (어디가 달라졌는지 표로 출력)
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import jeju_agent.data.loader as L  # noqa: E402
from jeju_agent import golden  # noqa: E402
from jeju_agent.agent import JejuAgent  # noqa: E402
from jeju_agent.config import DEMO_DIR, OUTPUT_DIR, RAW_DIR  # noqa: E402
from jeju_agent.data.loader import JejuDB  # noqa: E402
from jeju_agent.llm import LLM  # noqa: E402
from jeju_agent.report import build_html  # noqa: E402
from jeju_agent.tools.base import df_to_md  # noqa: E402

SAMPLES = OUTPUT_DIR / "samples"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["demo", "auto"], default="demo", help="demo=동봉 축약본 고정(기본), auto=data/raw 우선")
    ap.add_argument("--update-golden", action="store_true")
    ap.add_argument("--pytest", action="store_true")
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()

    t0 = datetime.now()
    print(f"재현 시작 {t0:%Y-%m-%d %H:%M} · Python {platform.python_version()} · {platform.system()} {platform.release()}")
    if a.data == "demo":
        L.RAW_DIR = ROOT / "data" / "__none__"     # data/raw 를 무시하고 동봉 축약본만 읽는다 (골든 비교의 전제)
        print(f"데이터: 동봉 축약본 {DEMO_DIR} (data/raw 무시)")
    else:
        print(f"데이터: {RAW_DIR} 우선, 없으면 {DEMO_DIR}")
    db = JejuDB(); db.load_all()
    summ = db.summary()
    print(df_to_md(summ[["code", "name", "source", "rows", "period"]]))
    if db.uses_synthetic():
        print("⚠️ 합성 데이터가 섞여 있습니다 — 재현 결과는 문서의 실데이터 수치와 다릅니다.")
    agent = JejuAgent(db, LLM("offline"))

    # 1) 질문 실행
    qs = golden.load_questions()
    fps, outputs, errors = {}, [], []
    for i, q in enumerate(qs, 1):
        res = agent.discover(top_n=3) if q == "__discover__" else agent.run_offline(q)
        fps[q] = golden.fingerprint(res)
        outputs.append(JejuAgent.to_dict(res))
        bad = [s for s in res.steps if not s.ok]
        if bad:
            errors.append((q, bad[0].error))
        print(f"[{i:2d}/{len(qs)}] {'✓' if not bad else '✗'} {q}  →  {' → '.join(s.tool for s in res.steps) or '-'}  ({res.elapsed}s)")
        if q == "__discover__":
            SAMPLES.mkdir(parents=True, exist_ok=True)
            (SAMPLES / "discover.json").write_text(json.dumps(outputs[-1], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        elif i == 1:
            SAMPLES.mkdir(parents=True, exist_ok=True)
            (SAMPLES / "sample_report.html").write_text(build_html(res), encoding="utf-8")
    (SAMPLES / "batch_offline.json").write_text(json.dumps(outputs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # 2) 골든 비교
    rows, mismatched = [], 0
    if a.data != "demo":
        print("\n골든 비교: data=auto 에서는 건너뜁니다 (골든은 동봉 축약본 기준).")
    elif a.update_golden:
        golden.save(fps)
        print(f"\n골든 지문 저장: {golden.GOLDEN_PATH} ({len(fps)}문항)")
    else:
        exp = golden.load()
        if exp is None:
            golden.save(fps)
            print(f"\n골든 지문이 없어 새로 저장했습니다: {golden.GOLDEN_PATH}")
        else:
            print("\n골든 비교 (tests/golden/demo_golden.json)")
            for q, fp in fps.items():
                d = golden.diff(exp[q], fp) if q in exp else ["골든에 없는 문항"]
                mismatched += bool(d)
                rows.append((q, "✓ 동일" if not d else "✗ " + "; ".join(d)[:200]))
                print(f"  {'✓' if not d else '✗'} {q}" + ("" if not d else "\n      " + "\n      ".join(d)))
            print(f"→ {len(fps) - mismatched}/{len(fps)} 동일" + ("" if not mismatched else "  (의도한 변경이면 --update-golden 으로 갱신)"))

    # 3) 요약 문서
    md = [f"# 재현 실행 요약", "", f"- 실행: {t0:%Y-%m-%d %H:%M} · Python {platform.python_version()} · {platform.system()} · 엔진 `{db.engine}` · 모드 offline(규칙)",
          f"- 데이터: {'동봉 축약본(data/demo)' if a.data == 'demo' else 'data/raw 우선'} · 합성 포함 {'예' if db.uses_synthetic() else '아니오'}",
          f"- 문항 {len(qs)}개 · 도구 오류 {len(errors)}건 · 골든 비교 " + (f"{len(fps) - mismatched}/{len(fps)} 동일" if rows else ("갱신" if a.update_golden else "생략")), "",
          "| 코드 | 데이터 | 출처 | 행 | 기간 |", "|---|---|---|---|---|"]
    md += [f"| {r.code} | {r.name} | {r.source} | {r.rows:,} | {r.period} |" for r in summ.itertuples()]
    md += ["", "| 질문 | 도구 | 골든 |", "|---|---|---|"]
    gold = dict(rows)
    md += [f"| {q} | {' → '.join(fp['tools']) or '-'} | {gold.get(q, '-')} |" for q, fp in fps.items()]
    if errors:
        md += ["", "## 도구 오류"] + [f"- {q}: {e}" for q, e in errors]
    (SAMPLES / "REPRODUCE.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n산출물: {SAMPLES / 'batch_offline.json'}, sample_report.html, discover.json, REPRODUCE.md")

    rc = 0
    if errors:
        print(f"\n도구 오류 {len(errors)}건:"); [print(f"  - {q}: {e}") for q, e in errors]; rc = 1
    if mismatched:
        rc = 1
    if a.pytest:
        print("\npytest 실행…")
        rc = max(rc, subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")], cwd=ROOT))
    if a.llm:
        print("\nLLM 실키 검증(scripts/llm_smoke.py)…")
        rc = max(rc, subprocess.call([sys.executable, str(ROOT / "scripts" / "llm_smoke.py")], cwd=ROOT))
    print(f"\n{'통과' if rc == 0 else '실패'} · 소요 {(datetime.now() - t0).seconds}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
