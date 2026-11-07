"""규칙 기반 오프라인 플래너.

LLM이 없거나(센터 PC에 외부망이 없을 때) 실패했을 때 질문을 도구 호출 계획으로 바꾼다.
LLM 모드에서도 이 결과를 '힌트'로 쓰지는 않고, 순수 폴백으로만 쓴다.
"""
from __future__ import annotations

import re
from datetime import date

from .tools.base import fuzzy_match

MONTH = re.compile(r"(?:(20\d{2})\s*년\s*)?(1[0-2]|[1-9])\s*월")
ROUTE = re.compile(r"\b(\d{3}(?:-\d{1,2})?)\s*(?:번)?\s*(?:노선|버스)?")


def parse_period(q: str, data_min: str, data_max: str) -> tuple[str | None, str | None]:
    m = MONTH.search(q)
    if m:
        y = int(m.group(1)) if m.group(1) else int(data_max[:4])
        mo = int(m.group(2))
        nxt = date(y + (mo == 12), mo % 12 + 1, 1)
        end = date.fromordinal(nxt.toordinal() - 1)
        return f"{y}-{mo:02d}-01", end.isoformat()
    if "최근" in q and "달" in q:
        y, mo = int(data_max[:4]), int(data_max[5:7])
        return f"{y}-{mo:02d}-01", data_max
    return None, None


def plan(question: str, places: list[str], stations: list[str], routes: list[str], data_min: str, data_max: str,
         caps: dict | None = None) -> list[tuple[str, dict]]:
    """caps: 적재 데이터의 능력 {"nationality": bool, "hour": bool, "weather": bool} — 없는 능력은 가장 가까운 도구로 대체."""
    caps = caps or {}
    q = question.replace(" ", "")
    df, dt = parse_period(question, data_min, data_max)
    period = {k: v for k, v in (("date_from", df), ("date_to", dt)) if v}
    weekend = any(k in q for k in ("주말", "토요일", "일요일"))
    foreign = any(k in q for k in ("외국인", "중국인", "해외")) and caps.get("nationality", True)
    senior = any(k in q for k in ("경로", "어르신", "노인", "고령", "교통약자", "노년"))
    rain = any(k in q for k in ("비오", "비가", "우천", "강우", "날씨", "비올", "장마"))
    bus_q = any(k in q for k in ("버스", "노선", "정류소", "정류장", "승차", "환승", "배차", "준공영"))
    place = fuzzy_match_any(q, places)
    station = fuzzy_match_any(q, stations)
    route = next((r for r in ROUTE.findall(question)
                  if any(n.split("(")[0] == r or n.split("(")[0].split("-")[0] == r for n in routes)), None)
    if any(k in q for k in ("어떤데이터", "무슨데이터", "데이터목록", "데이터있")):
        return [("describe_data", {})]
    if any(k in q for k in ("유동인구", "읍면동별", "동네별", "어느동네", "어느읍면동")) and caps.get("dong_population", False):
        args = {**period}
        if any(k in q for k in ("비율", "대비", "표본")):
            args["sort_by"] = "wifi_per_mille"
        elif weekend:
            args["sort_by"] = "pop_weekend_ratio"
        return [("dong_population_compare", args)]
    if any(k in q for k in ("브리핑", "상황요약", "월간", "지난달어땠", "한달정리")):
        if "해변" in q or "해수욕장" in q:
            pass
        m = MONTH.search(question)
        return [("monthly_brief", {"month": f"{int(m.group(1)) if m.group(1) else int(data_max[:4])}-{int(m.group(2)):02d}"} if m else {})]
    if any(k in q for k in ("발견", "특이", "눈에띄", "이상한", "이상치", "알아서", "둘러봐", "탐색", "뭐부터", "뭘물어", "무엇을물어", "요약해줘", "한눈에")):
        args = {"top_n": 5, **period}
        if any(k in q for k in ("노선",)):
            args["target"] = "bus_route"
        elif bus_q:
            args["target"] = "bus_station"
        elif any(k in q for k in ("관광지", "장소", "와이파이")):
            args["target"] = "wifi"
        return [("explore_anomalies", args)]
    compare = any(k in q for k in ("비교", "달라졌", "달라지", "늘었", "줄었", "늘어난", "줄어든", "변화", "전후", "차이", "전반", "후반", "증가", "감소"))
    if compare and not any(k in q for k in ("효율", "비효율")):
        target = "bus_route" if any(k in q for k in ("노선",)) else ("bus_station" if bus_q else "wifi")
        if any(k in q for k in ("평일", "주말")) and any(k in q for k in ("평일", "주말", "요일")):
            split = "weekday_weekend"
        else:
            split = "halves"
        args = {"target": target, "split": split, **period}
        ranges = re.findall(r"(\d{4}-\d{2}-\d{2})\s*[~부터]\s*(\d{4}-\d{2}-\d{2})", question)
        if len(ranges) >= 2:
            args.update(split="custom", a_from=ranges[0][0], a_to=ranges[0][1], b_from=ranges[1][0], b_to=ranges[1][1])
            args.pop("date_from", None); args.pop("date_to", None)
        ref = route or station or place
        if ref:
            args["place"] = ref
        return [("compare_periods", args)]
    if route:
        return [("bus_route_efficiency", {"route": route, **period})]
    if any(k in q for k in ("예상", "예측", "다음주말", "이번주말", "다음토요일", "연휴", "붐빌까", "붐빌것")):
        args = {"top_n": 10}
        if place:
            args["place"] = place
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", question)
        if dm:
            args["target_date"] = dm.group(1)
        return [("congestion_forecast", args)]
    if rain:
        if bus_q:
            return [("weather_effect", {"target": "bus_station", **({"place": station} if station else {}), **period})]
        return [("weather_effect", {"target": "wifi", **({"place": place} if place else {}), **period})]
    if any(k in q for k in ("효율", "비효율", "텅", "빈차", "적자", "준공영", "저이용")) and bus_q:
        return [("bus_route_efficiency", {"ascending": True, "top_n": 10, **period})]
    if senior:
        steps = [("bus_station_rank", {"sort_by": "senior_share", "top_n": 10, **period})]
        if station:
            steps.append(("hourly_profile", {"station": station, "user_category": "경로", **period}))
        return steps
    if station and bus_q and any(k in q for k in ("몇시", "시간대", "언제", "출근", "퇴근")):
        if caps.get("hour", True):
            return [("hourly_profile", {"station": station, **period})]
        return [("bus_station_rank", {"sort_by": "boardings", "top_n": 10, "station_name": station, **period})]
    if place and any(k in q for k in ("정류소", "정류장", "버스", "가는", "접근", "대중교통")):
        return [("place_access", {"place": place, **period})]
    if place:
        return [("place_profile", {"place": place, **period}), ("place_access", {"place": place, **period})]
    if station and any(k in q for k in ("몇시", "시간대", "언제", "출근", "퇴근")):
        if caps.get("hour", True):
            return [("hourly_profile", {"station": station, **period})]
        return [("bus_station_rank", {"sort_by": "boardings", "top_n": 10, "station_name": station, **period})]
    if bus_q:
        asc = any(k in q for k in ("적은", "한산", "적게", "낮은"))
        return [("bus_station_rank", {"sort_by": "weekend_ratio" if weekend else "boardings", "ascending": asc, "top_n": 10, **period})]
    # 기본: 혼잡 순위 → 1위 장소 접근성
    many = any(k in q for k in ("많이", "많은", "인기"))
    sort_by = "users" if many else ("foreign_share" if foreign and "비중" in q else "congestion_index")
    cat = "관광지" if any(k in q for k in ("관광지", "관광객", "명소", "놀러")) else ("해변" if any(k in q for k in ("해변", "해수욕장", "바다")) else ("전통시장" if "시장" in q else None))
    args = {"weekend_only": weekend, "foreign_only": foreign and "비중" not in q, "sort_by": sort_by, "top_n": 10, **period}
    if cat:
        args["category"] = cat
    return [("congestion_rank", args), ("place_access", {"place": "__TOP1__", **period})]

def fuzzy_match_any(q: str, names: list[str]) -> str | None:
    """질문 안에 이름(또는 3글자 이상 접두어)이 포함된 후보를 찾는다. 긴 이름 우선."""
    best = None
    for n in sorted(names, key=len, reverse=True):
        key = n.split("(")[0].replace(" ", "")
        if len(key) >= 2 and key in q:
            return n
        if len(key) >= 4 and key[:3] in q and best is None:
            best = n
    return best


SUGGESTION_BY_TOOL = {
    "congestion_rank": "상위 장소는 주말 분산 안내(실시간 혼잡 알림·대체 관광지 추천)와 임시 주차·셔틀 검토 대상입니다.",
    "place_profile": "붐비는 요일·시간을 피한 방문 권장 문구를 관광 안내에 반영하고, 피크일 전후로 인력·시설 운영을 조정할 수 있습니다.",
    "place_access": "접근 정류소의 피크 시간대에 증차·셔틀을 배치하면 관광지 쏠림과 대중교통 혼잡을 함께 줄일 수 있습니다.",
    "bus_station_rank": "상위 정류소는 승강장 확충·배차 조정 우선순위이며, 교통약자 비중이 높은 곳은 저상버스·안내 보강 대상입니다.",
    "bus_route_efficiency": "저이용 시간대는 배차 간격 조정(탄력 운행) 검토, 효율 하위 노선은 수요응답형(DRT) 전환 후보입니다.",
    "hourly_profile": "피크 시간대 전후 배차를 촘촘히 하고, 한산한 시간대는 간격을 늘려 운행 비용을 아낄 수 있습니다.",
    "weather_effect": "비 오는 날 감소폭이 큰 야외 관광지는 실내 대체 코스 안내, 버스는 우천 시 수요 변화에 맞춘 배차가 가능합니다.",
    "congestion_forecast": "예측 상위 장소는 주말 전 분산 안내·주차 관리 계획을 세울 근거가 됩니다(예측은 과거 패턴 기반).",
    "monthly_brief": "월간 브리핑은 관광·교통 담당 부서의 정례 보고 초안으로 활용할 수 있습니다.",
    "explore_anomalies": "급증한 곳은 원인(행사·단체 방문) 확인과 분산 안내, 급감·최근 한산은 수집 공백인지 실제 감소인지 현장 확인 뒤 운영·홍보 점검 대상입니다. 발견 항목을 눌러 세부 분석으로 이어가세요.",
    "compare_periods": "크게 늘어난 곳은 원인(행사·개통·계절) 확인과 선제 분산 안내, 크게 줄어든 곳은 운영·홍보 점검 대상입니다.",
}


def template_answer(question: str, results: list, synthetic: bool, limitations: list[str] | None = None) -> str:
    """LLM 없이 도구 결과의 facts로 답변을 조립한다."""
    if not results:
        return ("질문에 맞는 분석 도구를 찾지 못했습니다. 예: '주말에 가장 붐비는 관광지는?', '251번 노선 시간대별 이용', "
                "'비 오면 어디가 한산해지나?' (복합 질문은 LLM 모드를 권장합니다)")
    lines = ["**한 줄 결론** — " + (results[0].facts[0] if results[0].facts else results[0].title) + ".", "", "**근거**"]
    for r in results:
        for f in r.facts:
            lines.append(f"- {f}")
        lines.append(f"- (사용 데이터: {', '.join(r.datasets)}" + (f", 기간 {r.params.get('date_from')}~{r.params.get('date_to')}" if r.params.get("date_from") else ", 전체 기간") + ")")
    lines += ["", "**해석과 제안**"]
    seen_s = set()
    for r in results:
        s = SUGGESTION_BY_TOOL.get(r.name)
        if s and s not in seen_s:
            lines.append(f"- {s}")
            seen_s.add(s)
    if not seen_s:
        lines.append("- 위 수치는 도구가 계산한 집계이며, 상위 항목부터 현장 확인·정책 검토 대상으로 삼을 수 있습니다.")
    lines += ["", "**한계**"]
    if limitations:
        lines += [f"- {x}" for x in limitations[:8]]
    else:
        seen = set()
        for r in results:
            for c in r.caveats:
                if c not in seen:
                    lines.append(f"- {c}")
                    seen.add(c)
        if synthetic:
            lines.append("- ⚠️ 현재 적재된 데이터 일부/전체가 개발용 합성 데이터입니다. 실제 의사결정에 사용하지 마세요.")
    lines.append("- (오프라인 모드: LLM 없이 규칙 기반으로 작성된 답변입니다.)")
    return "\n".join(lines)
