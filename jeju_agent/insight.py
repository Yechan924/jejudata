"""답변 뒤에 붙는 '자동 점검'과 '다음 질문 제안'.

- auto_limitations(): 데이터 출처(실데이터/합성/기본값 채움), 요청 기간과 실제 적재 기간의 차이, 반출 게이트 마스킹,
  도구별 주의사항, 숫자 검증 결과를 **코드가** 모아 한계로 적는다. LLM이 한계를 빠뜨려도 결과물에는 항상 남는다.
- suggest_followups(): 방금 쓴 도구와 결과(1위 장소·정류소·노선)에 맞춰 다음 질문 2~3개를 만든다.
  IT 비전문가가 "다음에 무엇을 물어야 하는지"를 몰라도 탐색을 이어갈 수 있게 한다.
"""
from __future__ import annotations

from . import schemas
from .data.loader import INFO_TABLES
from .tools.base import ToolResult

TOOL_KO = {
    "congestion_rank": "장소별 혼잡 순위", "place_profile": "장소 혼잡 프로필", "list_places": "장소 목록",
    "bus_station_rank": "정류소 승차 순위", "bus_route_efficiency": "노선 효율", "hourly_profile": "정류소 시간대 프로필",
    "place_access": "관광지 주변 정류소 접근성", "weather_effect": "강우 영향", "congestion_forecast": "주말 혼잡 예측", "compare_periods": "기간·요일 비교",
    "monthly_brief": "월간 브리핑", "run_sql": "맞춤 집계(SQL)", "describe_data": "데이터 설명",
}


def josa(word: str, a: str = "은", b: str = "는") -> str:
    """받침 유무에 따라 조사 선택: josa('성산일출봉') → '성산일출봉은', josa('만장굴','이','가') → '만장굴이'."""
    if not word:
        return word
    ch = word.strip()[-1]
    if "가" <= ch <= "힣":
        return word + (a if (ord(ch) - 0xAC00) % 28 else b)
    return word + f"{a}({b})"


def describe_step(name: str, args: dict) -> str:
    """도구 호출 1건을 사람이 읽는 한 줄로."""
    ko = TOOL_KO.get(name, name)
    bits = []
    if args.get("place"):
        bits.append("앞 단계 1위 장소" if args["place"] == "__TOP1__" else f"장소 '{args['place']}'")
    if args.get("station"):
        bits.append(f"정류소 '{args['station']}'")
    if args.get("route"):
        bits.append(f"노선 {args['route']}")
    if args.get("weekend_only"):
        bits.append("주말만")
    if args.get("foreign_only"):
        bits.append("외국인만")
    if args.get("sort_by"):
        bits.append(f"정렬 {args['sort_by']}")
    if args.get("date_from") or args.get("date_to"):
        bits.append(f"기간 {args.get('date_from', '')}~{args.get('date_to', '')}")
    if args.get("month"):
        bits.append(f"{args['month']}월")
    if args.get("target") == "bus_station":
        bits.append("버스 기준")
    return ko + (f" ({', '.join(bits)})" if bits else "")


def auto_limitations(db, results: list[ToolResult], verification: dict | None = None, requested_period: tuple | None = None) -> list[str]:
    out: list[str] = []
    used = sorted({d for r in results for d in r.datasets if d in db.provenance})
    # 1) 출처
    syn = [schemas.dataset(k)["code"] for k in used if db.provenance[k].source == "synthetic"]
    if syn:
        out.append(f"⚠️ 사용 데이터 {', '.join(syn)}은(는) 개발용 합성 데이터입니다. 실제 의사결정에 쓰지 마세요.")
    for k in used:
        p = db.provenance[k]
        if p.defaulted:
            out.append(f"{schemas.dataset(k)['code']}에 원본에 없던 컬럼을 기본값으로 채웠습니다({', '.join(f'{a}={b}' for a, b in p.defaulted.items())}). 그 컬럼으로 나눈 비중은 의미가 없습니다.")
    # 1-1) 이름 매핑 실패(좌표·ID 없음) — 결과에 그 장소가 들어 있으면 짚어 준다
    for k, col in (("wifi_ap_usage", "ap_place"), ("bus_station_usage", "bus_station_name")):
        if k in used and db.provenance[k].unmatched:
            names = {str(x) for r in results if col in r.table.columns for x in r.table[col].head(20)}
            hit = [n for n in db.provenance[k].unmatched if n in names]
            if hit:
                out.append(f"{', '.join(hit[:3])}은(는) 4-01/3-03과 이름이 맞지 않아 좌표가 없습니다(지도에 표시되지 않음, 순위 수치는 유효).")
    # 2) 기간
    ps = [db.provenance[k] for k in used if db.provenance[k].date_min and k not in INFO_TABLES]
    if ps:
        dmin, dmax = min(p.date_min for p in ps), max(p.date_max for p in ps)
        if requested_period and any(requested_period):
            rf, rt = requested_period
            if (rf and rf < dmin) or (rt and rt > dmax):
                out.append(f"요청 기간 중 적재 범위({dmin}~{dmax}) 밖은 집계에 포함되지 않았습니다.")
        else:
            out.append(f"기간을 지정하지 않아 적재된 전체 기간({dmin}~{dmax})으로 계산했습니다.")
    # 2-1) 날짜 공백 (허브 API 에 비어 있는 날이 있음)
    for k in used:
        if k in INFO_TABLES or k not in db.frames or "date" not in db.frames[k].columns:
            continue
        d = db.frames[k]["date"].dropna()
        if d.empty:
            continue
        import pandas as _pd
        full = _pd.date_range(d.min(), d.max(), freq="D").strftime("%Y-%m-%d")
        gaps = sorted(set(full) - set(d.unique()))
        if 0 < len(gaps) <= 10:
            out.append(f"{schemas.dataset(k)['code']}에 데이터가 없는 날짜: {', '.join(x[5:] for x in gaps)} (제공처 공백, 집계에서 제외)")
        elif len(gaps) > 10:
            out.append(f"{schemas.dataset(k)['code']}에 데이터가 없는 날짜가 {len(gaps)}일 있습니다(제공처 공백, 집계에서 제외)")
    # 3) 게이트
    masked = [g for r in results for g in r.gate_log if "마스킹" in g]
    if masked:
        out.append("소집단 보호를 위해 10 미만 인원 셀은 '<10'으로 가려졌습니다(반출 정책).")
    trunc = [g for r in results for g in r.gate_log if "절단" in g]
    if trunc:
        out.append("표가 길어 상위 500행만 표시했습니다.")
    # 4) 표본 크기
    for r in results:
        days = None
        if "days" in r.table.columns:
            try:
                days = int(r.table["days"].min())
            except (TypeError, ValueError):
                days = None
        if days is not None and days < 14:
            out.append(f"'{r.title}'의 일부 항목은 {days}일치만 있어 변동이 큽니다.")
            break
    # 5) 도구 주의사항 (중복 제거)
    seen = set()
    for r in results:
        for c in r.caveats:
            if c not in seen:
                out.append(c)
                seen.add(c)
    # 6) 숫자 검증
    if verification and verification.get("unsupported"):
        out.append(f"해설 속 숫자 {', '.join(verification['unsupported'][:3])}은(는) 근거표에서 확인되지 않았습니다.")
    return out


def suggest_followups(question: str, results: list[ToolResult], db) -> list[str]:
    names = [r.name for r in results]
    sug: list[str] = []

    def top(r: ToolResult, col: str) -> str | None:
        if col in r.table.columns and len(r.table):
            v = r.table.iloc[0][col]
            return str(v) if v is not None and str(v) != "nan" else None
        return None

    for r in results:
        if r.name == "congestion_rank":
            p = top(r, "ap_place")
            if p:
                sug += [f"{josa(p)} 언제 가장 붐비나?", f"{p} 가는 버스 정류소는 얼마나 붐비나?", f"비 오면 {josa(p)} 한산해지나?"]
            if not r.params.get("weekend_only"):
                sug.append("주말만 보면 순위가 달라지나?")
            if not r.params.get("foreign_only"):
                sug.append("외국인 관광객이 많이 가는 곳은?")
        elif r.name == "place_profile":
            p = r.params.get("place")
            if p:
                sug += [f"{p} 가는 버스 정류소는 얼마나 붐비나?", f"비 오면 {josa(p)} 한산해지나?", f"이번 주말 {josa(p)} 붐빌까?"]
        elif r.name == "place_access":
            s = top(r, "bus_station_name")
            if s:
                sug += [f"{s} 정류소는 몇 시에 가장 붐비나?", f"어르신이 많이 타는 정류소는?"]
        elif r.name == "bus_station_rank":
            s = top(r, "bus_station_name")
            if s:
                sug += [f"{s} 정류소는 몇 시에 가장 붐비나?", "비 오는 날 버스 이용은 어떻게 바뀌어?"]
            if r.params.get("sort_by") != "senior_share":
                sug.append("어르신이 많이 타는 정류소는?")
        elif r.name == "bus_route_efficiency":
            rt = top(r, "bus_route_number") or top(r, "route")
            if rt and not r.params.get("route"):
                sug.append(f"{rt}번 노선 시간대별 이용 현황 알려줘")
            sug.append("이용 많은 정류소는 어디야?")
        elif r.name == "hourly_profile":
            sug += ["어르신이 많이 타는 정류소는?", "효율이 낮은 버스 노선은?"]
        elif r.name == "compare_periods":
            kind = next((c for c in ("장소", "정류소", "노선") if c in r.table.columns), None)
            p = top(r, kind) if kind else None
            if p and kind == "장소":
                sug += [f"{josa(p)} 언제 가장 붐비나?", f"{p} 가는 버스 정류소는 얼마나 붐비나?"]
            elif p and kind == "정류소":
                sug.append(f"{p} 정류소는 몇 시에 가장 붐비나?")
            if r.params.get("split") != "weekday_weekend":
                sug.append("평일과 주말 차이가 큰 곳은?")
            if r.params.get("target") != "bus_station":
                sug.append("정류소 승차는 전반과 후반이 어떻게 달라졌어?")
        elif r.name == "weather_effect":
            sug += ["주말에 가장 붐비는 관광지는?", "비 오는 날 버스 이용은 어떻게 바뀌어?" if r.params.get("target") != "bus_station" else "비 오면 어디가 한산해지나?"]
        elif r.name == "congestion_forecast":
            p = top(r, "ap_place")
            if p:
                sug += [f"{josa(p)} 언제 가장 붐비나?", f"{p} 가는 버스 정류소는 얼마나 붐비나?"]
        elif r.name == "monthly_brief":
            sug += ["주말에 가장 붐비는 관광지는?", "효율이 낮은 버스 노선은?", "비 오면 어디가 한산해지나?"]
        elif r.name == "describe_data":
            sug += ["주말에 가장 붐비는 관광지는?", "효율이 낮은 버스 노선은?", "5월 월간 브리핑 보여줘"]
    if not results:
        sug = ["주말에 가장 붐비는 관광지는?", "효율이 낮은 버스 노선은?", "어떤 데이터가 있어?"]
    # 이미 한 질문·중복 제거, 3개까지
    q = question.replace(" ", "")
    out, seen = [], set()
    for s in sug:
        k = s.replace(" ", "")
        if k in seen or k == q or (k[:6] and k[:6] in q and len(k) < 14):
            continue
        seen.add(k)
        out.append(s)
        if len(out) == 3:
            break
    return out
