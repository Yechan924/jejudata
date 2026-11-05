"""공공 와이파이 AP 이용을 관광지 혼잡 센서로 쓰는 도구.

혼잡지수 정의 (재현 가능하도록 명시)
- 장소별 일일 이용자수 u(place, day) = 해당 날짜·장소 AP에 접속한 고유 user_id 수 (4-05)
- 기준선 b(place) = 분석기간 내 평일 일일 이용자수의 중앙값
- 혼잡지수 CI(place, day) = u / b  (1.0 = 평소 평일 수준, 2.0 = 평소의 두 배)
- 장소 랭킹은 기간 평균 CI 또는 총 이용자수로 정렬한다.
한계: 와이파이 접속자는 방문자의 일부(표본)이며 AP 수·설치 위치에 따라 편향된다. 절대 방문객 수가 아니라 상대 비교 지표로 해석해야 한다.
"""
from __future__ import annotations

import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, fuzzy_match, period_filter, register, wifi_is_aggregated, wifi_users_agg, wifi_users_count

CAVEAT_WIFI = "와이파이 이용자는 전체 방문객의 일부(표본)이므로 절대 방문객 수가 아닌 상대 비교 지표로 해석해야 합니다."


def _daily_users(db, date_from=None, date_to=None, visit_type=None, foreign_only=False) -> pd.DataFrame:
    df = period_filter(db.table("wifi_ap_usage"), date_from, date_to)
    if visit_type:
        df = df[df["visit_type"] == visit_type]
    if foreign_only:
        df = df[df["nationality"] != "대한민국"]
    if wifi_is_aggregated(df):
        # 허브 API 집계본: 행 = 장소×일, user_count 가 이용자 수. 국적·방문유형 정보 없음 → 0 으로 두고 caveat 로 알린다.
        g = (df.groupby(["ap_place", "date", "is_weekend", "dow"], as_index=False)
               .agg(users=("user_count", "sum"), sessions=("user_count", "sum")))
        g["tourists"] = 0
        g["foreigners"] = 0
        return g
    g = (df.groupby(["ap_place", "date", "is_weekend", "dow"], as_index=False)
           .agg(users=("user_id", "nunique"), sessions=("user_id", "size"),
                tourists=("visit_type", lambda s: (s == "관광").sum()),
                foreigners=("nationality", lambda s: (s != "대한민국").sum())))
    return g


CAVEAT_AGG = "현재 와이파이 데이터는 허브 공개 API의 장소×일 집계본이라 국적·방문유형 비중은 계산되지 않습니다(센터 원본 4-05 적재 시 활성화)."


def _with_index(daily: pd.DataFrame) -> pd.DataFrame:
    base = (daily[daily["is_weekend"] == 0].groupby("ap_place")["users"].median().rename("baseline"))
    daily = daily.merge(base, on="ap_place", how="left")
    daily["congestion_index"] = (daily["users"] / daily["baseline"].replace(0, pd.NA)).astype(float)
    return daily


def _coords(db) -> pd.DataFrame:
    u = db.table("wifi_ap_usage")
    c = (u.groupby("ap_place", as_index=False).agg(lat=("ap_latitude", "first"), lon=("ap_longitude", "first"),
                                                    category=("ap_category", "first")))
    return c


@register(
    "congestion_rank",
    "공공 와이파이 접속 기반으로 제주 관광지·장소별 혼잡도를 순위화한다. '어디가 붐비나', '혼잡한 관광지', "
    "'외국인이 많이 가는 곳', '주말에 몰리는 곳' 류의 질문에 사용.",
    {"type": "object", "properties": {
        **PERIOD_PARAMS,
        "weekend_only": {"type": "boolean", "description": "주말(토·일)만 볼지"},
        "foreign_only": {"type": "boolean", "description": "외국인(대한민국 외 국적) 이용자만 볼지"},
        "sort_by": {"type": "string", "enum": ["congestion_index", "users", "foreign_share", "tourist_share"],
                    "description": "정렬 기준 (기본 congestion_index)"},
        "top_n": {"type": "integer", "description": "상위 몇 개 (기본 10)"},
    }},
)
def congestion_rank(db, date_from=None, date_to=None, weekend_only=False, foreign_only=False,
                    sort_by="congestion_index", top_n=10) -> ToolResult:
    if (foreign_only or sort_by in ("foreign_share", "tourist_share")) and wifi_is_aggregated(db.table("wifi_ap_usage")):
        raise ValueError("현재 적재된 와이파이 데이터(허브 API 집계본)에는 국적·방문유형이 없어 외국인/관광객 기준 분석이 불가합니다. "
                         "sort_by=congestion_index 또는 users 로 다시 호출하세요.")
    daily = _with_index(_daily_users(db, date_from, date_to, foreign_only=foreign_only))
    sub = daily[daily["is_weekend"] == 1] if weekend_only else daily
    agg = (sub.groupby("ap_place", as_index=False)
             .agg(days=("date", "nunique"), avg_daily_users=("users", "mean"), total_users=("users", "sum"),
                  tourists=("tourists", "sum"), foreigners=("foreigners", "sum"),
                  avg_congestion_index=("congestion_index", "mean"), peak_congestion_index=("congestion_index", "max")))
    agg["tourist_share"] = (agg["tourists"] / agg["total_users"]).round(3)
    agg["foreign_share"] = (agg["foreigners"] / agg["total_users"]).round(3)
    key = {"congestion_index": "avg_congestion_index", "users": "avg_daily_users",
           "foreign_share": "foreign_share", "tourist_share": "tourist_share"}[sort_by]
    agg = agg.sort_values(key, ascending=False).head(top_n).reset_index(drop=True)
    agg.insert(0, "rank", range(1, len(agg) + 1))
    agg = agg.merge(_coords(db), on="ap_place", how="left")
    out = agg[["rank", "ap_place", "category", "days", "avg_daily_users", "avg_congestion_index", "peak_congestion_index",
               "tourist_share", "foreign_share", "lat", "lon"]].round(2)
    facts = []
    if len(out):
        top = out.iloc[0]
        label = {"avg_congestion_index": "혼잡지수", "avg_daily_users": "이용자 수", "foreign_share": "외국인 비중", "tourist_share": "관광객 비중"}[key]
        facts.append(f"{'주말' if weekend_only else '전체'}{'·외국인' if foreign_only else ''} 기준 {label} 1위는 {top.ap_place}(평균 혼잡지수 {top.avg_congestion_index:.2f}, 일평균 {top.avg_daily_users:.0f}명)")
        if len(out) > 1 and not wifi_is_aggregated(db.table("wifi_ap_usage")):
            facts.append(f"상위 {len(out)}곳 중 관광 비중 최고는 {out.loc[out.tourist_share.idxmax(), 'ap_place']}({out.tourist_share.max():.0%}), 외국인 비중 최고는 {out.loc[out.foreign_share.idxmax(), 'ap_place']}({out.foreign_share.max():.0%})")
    mp = out.rename(columns={"avg_congestion_index": "value", "ap_place": "label"})[["label", "lat", "lon", "value"]]
    cav = [CAVEAT_WIFI, "혼잡지수 = 일일 고유 이용자수 ÷ 평일 중앙값"] + ([CAVEAT_AGG] if wifi_is_aggregated(db.table("wifi_ap_usage")) else [])
    return ToolResult("congestion_rank", f"장소별 혼잡도 순위 ({'주말' if weekend_only else '전체'}{', 외국인' if foreign_only else ''})",
                      out, facts, cav,
                      chart={"type": "bar", "x": "ap_place", "y": key, "title": "장소별 혼잡도"}, map_points=mp,
                      method="wifi_ap_usage를 ap_place×date로 고유 user_id 집계 → 평일 중앙값 대비 비율 평균",
                      params=dict(date_from=date_from, date_to=date_to, weekend_only=weekend_only, foreign_only=foreign_only, sort_by=sort_by, top_n=top_n),
                      datasets=["wifi_ap_usage"])


@register(
    "place_profile",
    "특정 관광지·장소 하나의 혼잡 패턴을 분석한다: 일별 추이, 요일별, 월별, 국적·방문유형 구성. "
    "'성산일출봉은 언제 붐비나', '월정리 방문 추세' 같은 질문에 사용.",
    {"type": "object", "properties": {"place": {"type": "string", "description": "장소명(부분 일치 가능)"}, **PERIOD_PARAMS},
     "required": ["place"]},
)
def place_profile(db, place, date_from=None, date_to=None) -> ToolResult:
    u = period_filter(db.table("wifi_ap_usage"), date_from, date_to)
    name = fuzzy_match(place, sorted(u["ap_place"].dropna().unique().tolist()))
    if not name:
        raise ValueError(f"'{place}'와 일치하는 와이파이 설치 장소가 없습니다. list_places 도구로 목록을 확인하세요.")
    sub = u[u["ap_place"] == name]
    daily = _with_index(_daily_users(db, date_from, date_to))
    daily = daily[daily["ap_place"] == name]
    dow_order = ["월", "화", "수", "목", "금", "토", "일"]
    by_dow = daily.groupby("dow")["users"].mean().reindex(dow_order).round(1).reset_index().rename(columns={"users": "avg_users"})
    by_month = daily.assign(ym=daily["date"].str[:7]).groupby("ym")["users"].mean().round(1).reset_index().rename(columns={"users": "avg_users"})
    agg_mode = wifi_is_aggregated(u)
    total = wifi_users_count(sub)
    if agg_mode:
        nat = vt = pd.Series(dtype=float)
    else:
        nat = sub.groupby("nationality")["user_id"].nunique().sort_values(ascending=False)
        vt = sub.groupby("visit_type")["user_id"].nunique().sort_values(ascending=False)
    wk, wd = daily[daily.is_weekend == 1]["users"].mean(), daily[daily.is_weekend == 0]["users"].mean()
    facts = [f"{name}: 기간 내 고유 이용자 {total:,}명, 평일 일평균 {wd:.0f}명 vs 주말 {wk:.0f}명 (주말/평일 {wk / wd if wd else 0:.2f}배)",
             f"가장 붐비는 요일 {by_dow.loc[by_dow.avg_users.idxmax(), 'dow']}요일, 가장 붐빈 날 {daily.loc[daily.users.idxmax(), 'date']} ({daily.users.max():.0f}명, 혼잡지수 {daily.congestion_index.max():.2f})",
             ]
    if not agg_mode:
        facts += [f"방문유형: " + ", ".join(f"{k} {v / total:.0%}" for k, v in vt.head(3).items()),
                  f"국적: " + ", ".join(f"{k} {v / total:.0%}" for k, v in nat.head(4).items())]
    if len(by_month) >= 3:
        first, last = by_month.avg_users.iloc[0], by_month.avg_users.iloc[-1]
        facts.append(f"월평균 추이 {by_month.ym.iloc[0]} {first:.0f}명 → {by_month.ym.iloc[-1]} {last:.0f}명 ({(last / first - 1) if first else 0:+.0%})")
    table = daily[["date", "dow", "is_weekend", "users", "tourists", "foreigners", "congestion_index"]].round(2).sort_values("date")
    c = _coords(db); c = c[c.ap_place == name]
    mp = pd.DataFrame({"label": [name], "lat": c["lat"].values, "lon": c["lon"].values, "value": [daily.congestion_index.mean()]})
    res = ToolResult("place_profile", f"{name} 혼잡 프로필", table, facts, [CAVEAT_WIFI] + ([CAVEAT_AGG] if agg_mode else []),
                     chart={"type": "line", "x": "date", "y": "users", "title": f"{name} 일별 와이파이 이용자"}, map_points=mp,
                     method="wifi_ap_usage에서 장소 필터 → 일별 고유 user_id, 요일/월 평균, 국적·방문유형 구성비",
                     params=dict(place=name, date_from=date_from, date_to=date_to), datasets=["wifi_ap_usage"])
    res.extra = {"by_dow": by_dow, "by_month": by_month}  # type: ignore[attr-defined]
    return res


@register(
    "list_places",
    "와이파이 AP가 설치된 장소 목록(카테고리·좌표·총 이용자)을 반환한다. 장소명을 모를 때 먼저 호출.",
    {"type": "object", "properties": {"category": {"type": "string", "description": "카테고리 필터(관광지, 해수욕장, 시장 등 부분일치)"}}},
)
def list_places(db, category=None) -> ToolResult:
    u = db.table("wifi_ap_usage")
    g = u.groupby(["ap_place", "ap_category"], as_index=False, dropna=False).agg(users=wifi_users_agg(u), lat=("ap_latitude", "first"), lon=("ap_longitude", "first"))
    if category:
        g = g[g["ap_category"].astype(str).str.contains(category) | g["ap_place"].astype(str).str.contains(category)]
    g = g.sort_values("users", ascending=False).reset_index(drop=True)
    return ToolResult("list_places", "와이파이 설치 장소 목록", g, [f"총 {len(g)}개 장소"], [], datasets=["wifi_ap_usage"],
                      map_points=g.rename(columns={"ap_place": "label", "users": "value"})[["label", "lat", "lon", "value"]])
