"""실데이터 적재 점검.

  python scripts/check_data.py            # data/raw 를 읽어 데이터셋별 인식·컬럼·기간을 표로 출력
  python scripts/check_data.py --strict   # 필수 데이터(4-05, 3-04)가 실데이터가 아니면 종료코드 1

제주데이터허브에서 받은 파일을 data/raw/ 에 넣은 뒤 이 스크립트로 "에이전트가 제대로 읽었는지"를 먼저 확인하세요.
확인 항목: 파일 인식 여부, 헤더 매핑(정확/별칭/기본값), 비어 있는 필수 컬럼, 기간, 행 수, 매핑되지 않은 원본 헤더.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jeju_agent import schemas  # noqa: E402
from jeju_agent.config import RAW_DIR  # noqa: E402
from jeju_agent.data.loader import ANY_OF_COLUMNS, OPTIONAL_COLUMNS, REQUIRED_COLUMNS, JejuDB  # noqa: E402
from jeju_agent.tools.base import df_to_md  # noqa: E402

REQUIRED = ["wifi_ap_usage", "bus_station_usage"]   # 대회 필수 데이터 1종 이상 — 이 서비스는 2종을 필수로 쓴다


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    print(f"원본 폴더: {RAW_DIR}  (존재: {RAW_DIR.exists()})")
    files = sorted(RAW_DIR.glob("**/*")) if RAW_DIR.exists() else []
    files = [f for f in files if f.is_file()]
    print(f"파일 {len(files)}개: " + ", ".join(f.name for f in files[:20]) + (" …" if len(files) > 20 else ""))
    assigned = JejuDB.assign_files(RAW_DIR)
    unassigned = [f for f in files if not any(f in v for v in assigned.values()) and f.suffix.lower() in (".csv", ".xlsx", ".xls", ".txt", ".tsv")]
    if unassigned:
        print("\n⚠️ 어떤 데이터셋인지 알 수 없는 파일 (파일명을 데이터명·코드·영문키로 시작하게 바꾸세요):")
        for f in unassigned:
            print("   -", f.name, " → 예: '", schemas.dataset("wifi_ap_usage")["name"], ".csv' 또는 'wifi_ap_usage.csv'")

    db = JejuDB()
    db.load_all()
    s = db.summary()
    print("\n" + df_to_md(s[["code", "name", "source", "rows", "period", "note"]]))

    ok = True
    print("\n[필수 데이터 점검]")
    for k in REQUIRED:
        p = db.provenance.get(k)
        d = schemas.dataset(k)
        if not p or p.source != "raw":
            print(f"  ✗ {d['code']} {d['name']}: 실데이터 없음 (현재 {p.source if p else 'missing'})")
            ok = False
            continue
        t = db.table(k)
        has = lambda c: c in t.columns and t[c].notna().any()  # noqa: E731
        missing = [c for c in REQUIRED_COLUMNS[k] if not has(c)]
        for group in ANY_OF_COLUMNS.get(k, []):
            if not any(has(c) for c in group):
                missing.append(" 또는 ".join(group))
        if missing:
            print(f"  ✗ {d['code']} {d['name']}: 값이 비어 있는 필수 컬럼 {missing}")
            ok = False
        else:
            print(f"  ✓ {d['code']} {d['name']}: {p.rows:,}행, {p.date_min}~{p.date_max}")
        if p.unmapped:
            print(f"     · 매핑되지 않은 원본 헤더: {p.unmapped[:10]}")
        if p.defaulted:
            print(f"     · 기본값으로 채운 컬럼: {p.defaulted}  (원본에 없던 값 — 해석 시 주의)")
        if p.unmatched:
            print(f"     · 이름으로 이어 붙이지 못한 장소·정류소(이용량 순): {p.unmatched[:10]}")
        off = [f"{c}({why})" for c, why in OPTIONAL_COLUMNS.get(k, {}).items() if not has(c) or c in p.defaulted]
        if off:
            print(f"     · 없어서 비활성화되는 기능: {', '.join(off)}")

    print("\n[보조 데이터]")
    for k in schemas.CORE_DATASETS:
        if k in REQUIRED:
            continue
        p = db.provenance.get(k)
        print(f"  {'✓' if p and p.source == 'raw' else '·'} {schemas.dataset(k)['code']} {schemas.dataset(k)['name']}: {p.source if p else 'missing'}")

    print("\n결론:", "필수 2종 실데이터 적재 완료 — 샘플·기획서를 실데이터로 갱신하세요." if ok else "필수 데이터가 아직 실데이터가 아닙니다. docs/DATA_ACQUISITION.md 참고.")
    return 0 if (ok or not a.strict) else 1


if __name__ == "__main__":
    sys.exit(main())
