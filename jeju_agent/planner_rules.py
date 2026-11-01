"""규칙 기반 오프라인 플래너.

LLM이 없거나(센터 PC에 외부망이 없을 때) 실패했을 때 질문을 도구 호출 계획으로 바꾼다.
LLM 모드에서도 이 결과를 '힌트'로 쓰지는 않고, 순수 폴백으로만 쓴다.
"""
from __future__ import annotations

import re
from datetime import date

from .tools.base import fuzzy_match

MONTH = re.compile(r"(?:(20\d{2})\s*년\s*)?(1[0-2]|[1-9])\s*월")
ROUTE = re.compile(r"\b(\d{3}(?:-\d)?)\s*(?:번)?\s*(?:노선|버스)?")


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


def plan(question: str, places: list[str], stations: list[str], routes: list[str], data_min: str, data_max: str) -> list[tuple[str, dict]]:
    q = question.replace(" ", "")
    df, dt = parse_period(question, data_min, data_max)
    period = {k: v for k, v in (("date_from", df), ("date_to", dt)) if v}
    weekend = any(k in q for k in ("주말", "토요일", "일요일"))
    foreign = any(k in q for k in ("외국인", "중국인", "해외"))
    senior = any(k in q for k in ("경로", "어르신", "노인", "고령", "교통약자", "노년"))
    rain = any(k in q for k in ("비오", "비가", "우천", "강우", "날씨", "비올", "장마"))
    bus_q = any(k in q for k in ("버스", "노선", "정류소", "정류장", "승차", "환승", "배차", "준공영"))
    place = fuzzy_match_any(q, places)
    station = fuzzy_match_any(q, stations)
    route = next((r for r in ROUTE.findall(question) if any(r == n.split("(")[0] for n in routes)), None)
    if any(k in q for k in ("어떤데이터", "무슨데이터", "데이터목록", "데이터있")):
        return [("describe_data", {})]
    if rain:
        if bus_q:
            return [("weather_effect", {"target": "bus_station", **({"place": station} if station else {}), **period})]
        return [("weather_effect", {"target": "wifi", **({"place": place} if place else {}), **period})]
    if route:
        return [("bus_route_efficiency", {"route": route, **period})]
    if any(k in q for k in ("효율", "비효율", "텅", "빈차", "적자", "준공영", "저이용")) and bus_q:
        return [("bus_route_efficiency", {"ascending": True, "top_n": 10, **period})]
    if senior:
        steps = [("bus_station_rank", {"sort_by": "senior_share", "top_n": 10, **period})]
        if station:
            steps.append(("hourly_profile", {"station": station, "user_category": "경로", **period}))
        return steps
    if station and bus_q and any(k in q for k in ("몇시", "시간대", "언제", "출근", "퇴근")):
        return [("hourly_profile", {"station": station, **period})]
    if place and any(k in q for k in ("정류소", "정류장", "버스", "가는", "접근", "대중교통")):
        return [("place_access", {"place": place, **period})]
    if place:
        return [("place_profile", {"place": place, **period}), ("place_access", {"place": place, **period})]
    if station and any(k in q for k in ("몇시", "시간대", "언제", "출근", "퇴근")):
        return [("hourly_profile", {"station": station, **period})]
    if bus_q:
        asc = any(k in q for k in ("적은", "한산", "적게", "낮은"))
        return [("bus_station_rank", {"sort_by": "weekend_ratio" if weekend else "boardings", "ascending": asc, "top_n": 10, **period})]
    # 기본: 혼잡 순위 → 1위 장소 접근성
    many = any(k in q for k in ("많이", "많은", "인기"))
    sort_by = "users" if many else ("foreign_share" if foreign and "비중" in q else "congestion_index")
    return [("congestion_rank", {"weekend_only": weekend, "foreign_only": foreign and "비중" not in q, "sort_by": sort_by, "top_n": 10, **period}),
            ("place_access", {"place": "__TOP1__", **period})]

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


def template_answer(question: str, results: list, synthetic: bool) -> str:
    """LLM 없이 도구 결과의 facts로 답변을 조립한다."""
    if not results:
        return "질문에 맞는 분석 도구를 찾지 못했습니다. 예: '주말에 가장 붐비는 관광지는?', '251번 노선 시간대별 이용', '비 오면 어디가 한산해지나?'"
    lines = ["**한 줄 결론** — " + (results[0].facts[0] if results[0].facts else results[0].title) + ".", "", "**근거**"]
    for r in results:
        for f in r.facts:
            lines.append(f"- {f}")
        lines.append(f"- (사용 데이터: {', '.join(r.datasets)}" + (f", 기간 {r.params.get('date_from')}~{r.params.get('date_to')}" if r.params.get("date_from") else ", 전체 기간") + ")")
    lines += ["", "**해석과 제안**", "- 위 수치는 도구가 계산한 집계이며, 상위 항목부터 현장 확인·정책 검토 대상으로 삼을 수 있습니다.", "",
              "**한계**"]
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
