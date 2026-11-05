"""티머니 버스 승하차 데이터로 이동 수요·노선 효율·교통약자 패턴을 보는 도구.

정의
- 정류소 일평균 승차 = 3-04에서 get_on_off_type='승차' user_count 합 ÷ 일수
- 교통약자 비중 = (경로+장애+유공+어린이) 승차 ÷ 전체 승차
- 노선 효율 지표 = 일평균 승차 ÷ 노선거리(km)  (승객/km/일), 시간대별 '저이용 시간대' = 해당 시간 승차가 최대 시간대의 15% 미만인 운행시간(05~23시)
한계: 카드 태그 기반이라 현금·무료환승·관광객 1일권 등은 집계되지 않는다. 운행횟수(배차) 데이터가 없어 '빈 차'는 승차 수요 기준 추정이다.
"""
from __future__ import annotations

import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, fuzzy_match, haversine_km, period_filter, register, wifi_users_agg

VULNERABLE = ["경로", "어린이", "장애 일반", "장애 동반", "유공 일반", "유공 동반",
              "기타"]   # 허브 공개 API(일일 정류소별)는 요금제를 일반/청소년/어린이/기타 4종으로만 제공 → '기타'(경로·장애·유공 등 감면)를 교통약자로 간주
CAVEAT_BUS = "티머니 카드 태그 기반 집계이며 현금 승차·일부 요금제는 포함되지 않습니다. 배차 횟수 데이터가 없어 효율은 '승차 수요' 기준입니다."
CAVEAT_CAT = "허브 공개 API는 이용자구분을 일반/청소년/어린이/기타로만 제공하므로 '기타'(경로·장애·유공 감면)를 교통약자로 간주했습니다. 경로 단독 비중은 센터 원본(3-04) 적재 시 계산됩니다."


def _boardings(db, date_from, date_to, key="bus_station_usage"):
    df = period_filter(db.table(key), date_from, date_to)
    return df[df["get_on_off_type"] == "승차"]


@register(
    "bus_station_rank",
    "버스 정류소별 승차 수요를 순위화한다. 교통약자(경로·장애·어린이) 비중 순으로도 볼 수 있다. "
    "'어르신이 많이 타는 정류소', '이용 많은/적은 정류소', '주말 승차 많은 정류소' 질문에 사용.",
    {"type": "object", "properties": {
        **PERIOD_PARAMS,
        "sort_by": {"type": "string", "enum": ["boardings", "vulnerable_share", "senior_share", "weekend_ratio"], "description": "정렬 기준(기본 boardings)"},
        "ascending": {"type": "boolean", "description": "오름차순(적은 순)으로 볼지"},
        "city_type": {"type": "string", "enum": ["시내", "시외"], "description": "시내/시외 정류소 필터"},
        "min_boardings": {"type": "integer", "description": "비중 순위에 넣을 최소 일평균 승차(기본 20) — 승차 1~2명 정류소의 100% 비중 같은 소표본 왜곡 방지"},
        "top_n": {"type": "integer", "description": "상위 몇 개(기본 10)"},
    }},
)
def bus_station_rank(db, date_from=None, date_to=None, sort_by="boardings", ascending=False, city_type=None, top_n=10, min_boardings=20) -> ToolResult:
    b = _boardings(db, date_from, date_to)
    days = max(b["date"].nunique(), 1)
    g = b.groupby(["bus_station_id", "bus_station_name"], as_index=False).agg(total=("user_count", "sum"))
    vul = b[b["user_category"].isin(VULNERABLE)].groupby("bus_station_id")["user_count"].sum().rename("vulnerable")
    sen = b[b["user_category"] == "경로"].groupby("bus_station_id")["user_count"].sum().rename("senior")
    wk = b[b["is_weekend"] == 1].groupby("bus_station_id")["user_count"].sum().rename("weekend_total")
    g = g.merge(vul, on="bus_station_id", how="left").merge(sen, on="bus_station_id", how="left").merge(wk, on="bus_station_id", how="left").fillna(0)
    wk_days = max(b[b.is_weekend == 1]["date"].nunique(), 1)
    wd_days = max(b[b.is_weekend == 0]["date"].nunique(), 1)
    g["daily_boardings"] = (g["total"] / days).round(1)
    g["vulnerable_share"] = (g["vulnerable"] / g["total"]).round(3)
    g["senior_share"] = (g["senior"] / g["total"]).round(3)
    g["weekend_ratio"] = ((g["weekend_total"] / wk_days) / ((g["total"] - g["weekend_total"]) / wd_days).replace(0, pd.NA)).astype(float).round(2)
    if db.has("bus_station"):
        st = db.table("bus_station").drop_duplicates("bus_station_id")[["bus_station_id", "bus_city_type", "latitude", "longitude"]]
        g = g.merge(st, on="bus_station_id", how="left")
        if city_type:
            g = g[g["bus_city_type"] == city_type]
    key = {"boardings": "daily_boardings", "vulnerable_share": "vulnerable_share", "senior_share": "senior_share", "weekend_ratio": "weekend_ratio"}[sort_by]
    has_wk, has_wd = b["is_weekend"].eq(1).any(), b["is_weekend"].eq(0).any()
    if sort_by == "weekend_ratio" and not (has_wk and has_wd):
        raise ValueError(f"분석 기간({days}일)에 주말과 평일이 모두 포함되어야 주말/평일 비율을 계산할 수 있습니다. 기간을 넓히세요.")
    dropped = 0
    if key != "daily_boardings" or ascending:
        n0 = len(g); g = g[g["daily_boardings"] >= (min_boardings or 0)]; dropped = n0 - len(g)
    g = g.sort_values(key, ascending=ascending).head(top_n).reset_index(drop=True)
    g.insert(0, "rank", range(1, len(g) + 1))
    cols = ["rank", "bus_station_name", "daily_boardings", "vulnerable_share", "senior_share", "weekend_ratio"] + [c for c in ["bus_city_type", "latitude", "longitude"] if c in g.columns]
    out = g[cols]
    facts = []
    if len(out):
        t = out.iloc[0]
        wr = f", 주말/평일 {t.weekend_ratio}배" if pd.notna(t.weekend_ratio) and has_wk and has_wd else ""
        sr = f"(경로 {t.senior_share:.0%})" if "경로" in b["user_category"].unique() else ""
        facts.append(f"{sort_by} 기준 1위 {t.bus_station_name}: 일평균 승차 {t.daily_boardings:,.0f}명, 교통약자 비중 {t.vulnerable_share:.0%}{sr}{wr}")
        facts.append(f"분석기간 {days}일, 전체 정류소 교통약자 평균 비중 {(b[b.user_category.isin(VULNERABLE)].user_count.sum() / b.user_count.sum()):.0%}")
    mp = None
    if "latitude" in out.columns:
        mp = out.rename(columns={"bus_station_name": "label", "latitude": "lat", "longitude": "lon", key: "value"})[["label", "lat", "lon", "value"]]
    return ToolResult("bus_station_rank", f"정류소 순위 ({sort_by}{', 오름차순' if ascending else ''})", out, facts, [CAVEAT_BUS],
                      chart={"type": "bar", "x": "bus_station_name", "y": key, "title": "정류소별 지표"}, map_points=mp,
                      method="bus_station_usage 승차 행을 정류소별 합산, 교통약자(경로·장애·유공·어린이) 비중과 주말/평일 일평균 비율 계산",
                      params=dict(date_from=date_from, date_to=date_to, sort_by=sort_by, ascending=ascending, city_type=city_type, top_n=top_n),
                      datasets=["bus_station_usage", "bus_station"])


@register(
    "bus_route_efficiency",
    "노선별 승차 수요와 효율(승객/km/일), 시간대별 저이용 구간을 분석한다. '비효율 노선', '텅 빈 시간대', "
    "'준공영제 노선 효율', '특정 노선 시간대별 이용' 질문에 사용. route를 주면 그 노선의 시간대 프로필을 반환.",
    {"type": "object", "properties": {
        **PERIOD_PARAMS,
        "route": {"type": "string", "description": "노선 번호 또는 이름 일부(예: 251). 생략 시 전체 노선 순위"},
        "ascending": {"type": "boolean", "description": "효율 낮은 순(기본 true)"},
        "top_n": {"type": "integer", "description": "상위 몇 개(기본 10)"},
    }},
)
def bus_route_efficiency(db, date_from=None, date_to=None, route=None, ascending=True, top_n=10) -> ToolResult:
    b = _boardings(db, date_from, date_to, key="bus_route_usage")
    days = max(b["date"].nunique(), 1)
    routes = db.table("bus_route").drop_duplicates("bus_route_id")[["bus_route_id", "bus_route_number", "bus_route_length", "bus_station_count"]] if db.has("bus_route") else None
    if route:
        names = sorted(b["bus_route_name"].dropna().unique().tolist())
        name = fuzzy_match(route, names) or next((n for n in names if n.split("(")[0] == route), None)
        if not name:
            raise ValueError(f"'{route}' 노선을 찾을 수 없습니다. 노선 목록: {', '.join(n.split('(')[0] for n in names[:30])}")
        s = b[b["bus_route_name"] == name]
        prof = s.groupby("hour")["user_count"].sum().reindex(range(5, 24), fill_value=0) / days
        prof = prof.round(1).reset_index().rename(columns={"user_count": "avg_boardings"})
        peak = prof["avg_boardings"].max()
        low = prof[prof["avg_boardings"] < peak * 0.15]["hour"].tolist()
        wk = s[s.is_weekend == 1].groupby("hour")["user_count"].sum().reindex(range(5, 24), fill_value=0) / max(s[s.is_weekend == 1]["date"].nunique(), 1)
        wd = s[s.is_weekend == 0].groupby("hour")["user_count"].sum().reindex(range(5, 24), fill_value=0) / max(s[s.is_weekend == 0]["date"].nunique(), 1)
        prof["weekday"] = wd.round(1).values
        prof["weekend"] = wk.round(1).values
        cat = s.groupby("user_category")["user_count"].sum().sort_values(ascending=False)
        total_daily = s["user_count"].sum() / days
        facts = [f"{name}: 일평균 승차 {total_daily:,.0f}명, 피크 {int(prof.loc[prof.avg_boardings.idxmax(), 'hour'])}시({peak:.0f}명/시)",
                 f"저이용 시간대(피크의 15% 미만): {', '.join(f'{h}시' for h in low) if low else '없음'}",
                 f"이용자 구성: " + ", ".join(f"{k} {v / cat.sum():.0%}" for k, v in cat.head(4).items())]
        if routes is not None:
            r = routes[routes.bus_route_id == s["bus_route_id"].iloc[0]]
            if len(r):
                facts.append(f"노선거리 {r.bus_route_length.iloc[0] / 1000:.1f}km, 정류소 {int(r.bus_station_count.iloc[0])}개 → {total_daily / (r.bus_route_length.iloc[0] / 1000):.1f}명/km/일")
        return ToolResult("bus_route_efficiency", f"{name} 시간대별 이용", prof, facts, [CAVEAT_BUS],
                          chart={"type": "line", "x": "hour", "y": ["weekday", "weekend"], "title": f"{name} 시간대별 일평균 승차"},
                          method="bus_route_usage 승차 행을 시간대별 합산 ÷ 일수; 저이용=피크의 15% 미만",
                          params=dict(route=name, date_from=date_from, date_to=date_to), datasets=["bus_route_usage", "bus_route"])
    g = b.groupby(["bus_route_id", "bus_route_name"], as_index=False).agg(total=("user_count", "sum"))
    g["daily_boardings"] = (g["total"] / days).round(1)
    hourly = b.groupby(["bus_route_id", "hour"])["user_count"].sum().reset_index()
    peak = hourly.groupby("bus_route_id")["user_count"].max().rename("peak")
    hourly = hourly.merge(peak, on="bus_route_id")
    low_hours = hourly[(hourly["hour"].between(5, 23)) & (hourly["user_count"] < hourly["peak"] * 0.15)].groupby("bus_route_id")["hour"].apply(lambda s: ",".join(str(int(h)) for h in sorted(s))).rename("low_hours")
    g = g.merge(low_hours, on="bus_route_id", how="left").fillna({"low_hours": ""})
    g["n_low_hours"] = g["low_hours"].apply(lambda s: 0 if not s else len(s.split(",")))
    if routes is not None:
        g = g.merge(routes, on="bus_route_id", how="left")
        g["boardings_per_km"] = (g["daily_boardings"] / (g["bus_route_length"] / 1000)).round(2)
        g["boardings_per_station"] = (g["daily_boardings"] / g["bus_station_count"]).round(1)
    key = "boardings_per_km" if "boardings_per_km" in g.columns else "daily_boardings"
    g = g.sort_values(key, ascending=ascending).head(top_n).reset_index(drop=True)
    g.insert(0, "rank", range(1, len(g) + 1))
    cols = ["rank", "bus_route_name", "daily_boardings"] + [c for c in ["boardings_per_km", "boardings_per_station", "bus_route_length"] if c in g.columns] + ["n_low_hours", "low_hours"]
    out = g[cols]
    facts = []
    if len(out):
        t = out.iloc[0]
        facts.append(f"효율 {'최저' if ascending else '최고'} 노선 {t.bus_route_name}: 일평균 승차 {t.daily_boardings:,.0f}명" + (f", {t.boardings_per_km}명/km/일" if "boardings_per_km" in out.columns else "") + f", 저이용 시간대 {t.n_low_hours}개({t.low_hours or '없음'})")
        facts.append(f"분석 노선 {b['bus_route_id'].nunique()}개, 전체 일평균 승차 {b.user_count.sum() / days:,.0f}명")
    return ToolResult("bus_route_efficiency", f"노선 효율 순위 ({'낮은 순' if ascending else '높은 순'})", out, facts, [CAVEAT_BUS],
                      chart={"type": "bar", "x": "bus_route_name", "y": key, "title": "노선별 승객/km/일"},
                      method="bus_route_usage 승차 합산 ÷ 일수 ÷ 노선거리(km); 저이용 시간대 = 시간대 승차가 피크의 15% 미만",
                      params=dict(date_from=date_from, date_to=date_to, ascending=ascending, top_n=top_n), datasets=["bus_route_usage", "bus_route"])


@register(
    "hourly_profile",
    "정류소의 시간대별·요일별 승차 패턴. '몇 시에 가장 붐비나', '출퇴근 시간 정류소' 질문에 사용.",
    {"type": "object", "properties": {"station": {"type": "string", "description": "정류소명(부분 일치)"}, **PERIOD_PARAMS,
                                      "user_category": {"type": "string", "description": "이용자 구분 필터(일반, 경로, 청소년, 어린이 등)"}},
     "required": ["station"]},
)
def hourly_profile(db, station, date_from=None, date_to=None, user_category=None) -> ToolResult:
    b = _boardings(db, date_from, date_to)
    name = fuzzy_match(station, sorted(b["bus_station_name"].dropna().unique().tolist()))
    if not name:
        raise ValueError(f"'{station}' 정류소를 찾을 수 없습니다.")
    s = b[b["bus_station_name"] == name]
    if user_category:
        s = s[s["user_category"] == user_category]
    wd_days = max(s[s.is_weekend == 0]["date"].nunique(), 1)
    wk_days = max(s[s.is_weekend == 1]["date"].nunique(), 1)
    wd = s[s.is_weekend == 0].groupby("hour")["user_count"].sum().reindex(range(24), fill_value=0) / wd_days
    wk = s[s.is_weekend == 1].groupby("hour")["user_count"].sum().reindex(range(24), fill_value=0) / wk_days
    prof = pd.DataFrame({"hour": range(24), "weekday": wd.round(1).values, "weekend": wk.round(1).values})
    facts = [f"{name}{'(' + user_category + ')' if user_category else ''}: 평일 피크 {int(prof.loc[prof.weekday.idxmax(), 'hour'])}시 {prof.weekday.max():.0f}명, 주말 피크 {int(prof.loc[prof.weekend.idxmax(), 'hour'])}시 {prof.weekend.max():.0f}명",
             f"평일 일평균 {prof.weekday.sum():.0f}명, 주말 {prof.weekend.sum():.0f}명"]
    return ToolResult("hourly_profile", f"{name} 시간대별 승차", prof, facts, [CAVEAT_BUS],
                      chart={"type": "line", "x": "hour", "y": ["weekday", "weekend"], "title": f"{name} 시간대별 승차(일평균)"},
                      method="bus_station_usage 승차 행을 시간대×평일/주말로 합산 ÷ 해당 일수",
                      params=dict(station=name, date_from=date_from, date_to=date_to, user_category=user_category), datasets=["bus_station_usage"])


@register(
    "place_access",
    "혼잡 관광지 주변(반경 km 이내) 버스 정류소와 그 승차 수요·시간대를 묶어 보여준다. "
    "'성산일출봉 가는 버스 정류소 얼마나 붐비나', '혼잡 관광지 대중교통 접근' 질문에 사용.",
    {"type": "object", "properties": {"place": {"type": "string", "description": "관광지(와이파이 장소명, 부분 일치)"},
                                      "radius_km": {"type": "number", "description": "반경 km(기본 1.5)"}, **PERIOD_PARAMS},
     "required": ["place"]},
)
def place_access(db, place, radius_km=1.5, date_from=None, date_to=None) -> ToolResult:
    u = db.table("wifi_ap_usage")
    name = fuzzy_match(place, sorted(u["ap_place"].dropna().unique().tolist()))
    if not name:
        raise ValueError(f"'{place}' 장소를 찾을 수 없습니다.")
    p = u[u.ap_place == name].iloc[0]
    plat, plon = float(p["ap_latitude"]), float(p["ap_longitude"])
    st = db.table("bus_station").drop_duplicates("bus_station_id").copy()
    st["dist_km"] = st.apply(lambda r: haversine_km(plat, plon, float(r["latitude"]), float(r["longitude"])), axis=1)
    near = st[st["dist_km"] <= radius_km]
    b = _boardings(db, date_from, date_to)
    b = b[b["bus_station_id"].isin(near["bus_station_id"])]
    days = max(b["date"].nunique(), 1)
    g = b.groupby(["bus_station_id", "bus_station_name"], as_index=False).agg(total=("user_count", "sum"))
    g["daily_boardings"] = (g["total"] / days).round(1)
    peak = b.groupby(["bus_station_id", "hour"])["user_count"].sum().reset_index().sort_values("user_count", ascending=False).drop_duplicates("bus_station_id")[["bus_station_id", "hour"]].rename(columns={"hour": "peak_hour"})
    g = g.merge(peak, on="bus_station_id", how="left").merge(near[["bus_station_id", "dist_km", "latitude", "longitude"]], on="bus_station_id").sort_values("dist_km")
    g["dist_km"] = g["dist_km"].round(2)
    daily_place = u[u.ap_place == name].groupby("date").agg(v=wifi_users_agg(u))["v"]
    facts = [f"{name} 반경 {radius_km}km 내 정류소 {len(near)}개, 합산 일평균 승차 {g.daily_boardings.sum():,.0f}명 (와이파이 일평균 이용자 {daily_place.mean():.0f}명)"]
    if len(g):
        facts.append(f"가장 가까운 정류소 {g.iloc[0].bus_station_name}({g.iloc[0].dist_km}km), 피크 {int(g.iloc[0].peak_hour) if pd.notna(g.iloc[0].peak_hour) else '-'}시")
    out = g[["bus_station_name", "dist_km", "daily_boardings", "peak_hour", "latitude", "longitude"]]
    mp = out.rename(columns={"bus_station_name": "label", "latitude": "lat", "longitude": "lon", "daily_boardings": "value"})[["label", "lat", "lon", "value"]]
    mp = pd.concat([pd.DataFrame({"label": [f"★{name}"], "lat": [plat], "lon": [plon], "value": [daily_place.mean()]}), mp], ignore_index=True)
    return ToolResult("place_access", f"{name} 주변 대중교통 접근", out, facts, [CAVEAT_BUS, "정류소-관광지 거리는 직선거리입니다."],
                      chart={"type": "bar", "x": "bus_station_name", "y": "daily_boardings", "title": f"{name} 주변 정류소 일평균 승차"}, map_points=mp,
                      method="와이파이 장소 좌표 기준 haversine 거리로 정류소 선별 → bus_station_usage 승차 합산",
                      params=dict(place=name, radius_km=radius_km, date_from=date_from, date_to=date_to), datasets=["wifi_ap_usage", "bus_station", "bus_station_usage"])
