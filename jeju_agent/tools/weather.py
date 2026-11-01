"""날씨(5-02)와 혼잡·버스 수요를 결합하는 도구.

정의: 강우일 = 가장 가까운 관측소의 일강수량 ≥ 1mm. 효과 = 강우일 일평균 ÷ 비강우일 일평균 − 1 (평일/주말 분리).
한계: 날씨 외 요인(공휴일·행사)이 통제되지 않은 단순 비교다. 인과가 아니라 상관으로 해석해야 한다.
"""
from __future__ import annotations

import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, fuzzy_match, haversine_km, period_filter, register

CAVEAT_WX = "강우일/비강우일 단순 비교이며 공휴일·행사 등 다른 요인은 통제되지 않았습니다(상관 관계)."


def nearest_station(db, lat: float, lon: float) -> str:
    ws = db.table("weather_station") if db.has("weather_station") else None
    if ws is None or ws.empty:
        return "184"
    ws = ws.copy()
    # 명세서 예시값은 위·경도가 뒤바뀌어 있어 둘 다 시도해 제주 범위(33~34N)에 맞는 쪽을 쓴다
    la = pd.to_numeric(ws["latitude"], errors="coerce"); lo = pd.to_numeric(ws["longitude"], errors="coerce")
    if la.between(33, 34.5).mean() < 0.5 and lo.between(33, 34.5).mean() >= 0.5:
        la, lo = lo, la
    ws["d"] = [haversine_km(lat, lon, a, b) if pd.notna(a) and pd.notna(b) else 9e9 for a, b in zip(la, lo)]
    return str(ws.sort_values("d").iloc[0]["observatory_code"])


def _rain_days(db, code: str, date_from, date_to, threshold=1.0) -> pd.DataFrame:
    w = period_filter(db.table("weather_daily"), date_from, date_to)
    w = w[w["observatory_code"].astype(str) == str(code)][["date", "daily_rainfall", "average_temperature"]].drop_duplicates("date")
    w["rain"] = (w["daily_rainfall"] >= threshold).astype(int)
    return w


@register(
    "weather_effect",
    "비 오는 날과 맑은 날의 혼잡(와이파이 이용자) 또는 버스 승차 차이를 계산한다. "
    "'비 오면 어디가 한산해지나', '우천 시 버스 이용 변화', '날씨에 민감한 관광지' 질문에 사용. "
    "place를 주면 그 장소, 생략하면 전체 장소를 민감도 순으로 나열.",
    {"type": "object", "properties": {
        "target": {"type": "string", "enum": ["wifi", "bus_station", "bus_route"], "description": "분석 대상(기본 wifi)"},
        "place": {"type": "string", "description": "장소명/정류소명/노선(부분 일치). 생략 시 전체 순위"},
        **PERIOD_PARAMS,
        "rain_mm": {"type": "number", "description": "강우일 기준 mm(기본 1.0)"},
    }},
)
def weather_effect(db, target="wifi", place=None, date_from=None, date_to=None, rain_mm=1.0) -> ToolResult:
    if target == "wifi":
        u = period_filter(db.table("wifi_ap_usage"), date_from, date_to)
        daily = u.groupby(["ap_place", "date", "is_weekend"], as_index=False).agg(value=("user_id", "nunique"), lat=("ap_latitude", "first"), lon=("ap_longitude", "first"))
        label, ds = "ap_place", ["wifi_ap_usage", "weather_daily"]
    elif target == "bus_station":
        b = period_filter(db.table("bus_station_usage"), date_from, date_to)
        b = b[b.get_on_off_type == "승차"]
        daily = b.groupby(["bus_station_name", "date", "is_weekend"], as_index=False).agg(value=("user_count", "sum"))
        if db.has("bus_station"):
            st = db.table("bus_station").drop_duplicates("bus_station_name")[["bus_station_name", "latitude", "longitude"]].rename(columns={"latitude": "lat", "longitude": "lon"})
            daily = daily.merge(st, on="bus_station_name", how="left")
        label, ds = "bus_station_name", ["bus_station_usage", "weather_daily"]
    else:
        b = period_filter(db.table("bus_route_usage"), date_from, date_to)
        b = b[b.get_on_off_type == "승차"]
        daily = b.groupby(["bus_route_name", "date", "is_weekend"], as_index=False).agg(value=("user_count", "sum"))
        daily["lat"], daily["lon"] = 33.5, 126.53
        label, ds = "bus_route_name", ["bus_route_usage", "weather_daily"]
    if place:
        nm = fuzzy_match(place, sorted(daily[label].dropna().unique().tolist()))
        if not nm:
            raise ValueError(f"'{place}'를 찾을 수 없습니다.")
        daily = daily[daily[label] == nm]
    rows = []
    for nm, g in daily.groupby(label):
        lat, lon = float(pd.to_numeric(g["lat"].iloc[0], errors="coerce") or 33.5), float(pd.to_numeric(g["lon"].iloc[0], errors="coerce") or 126.53)
        w = _rain_days(db, nearest_station(db, lat, lon), date_from, date_to, rain_mm)
        m = g.merge(w, on="date", how="inner")
        if m.empty or m["rain"].sum() < 3:
            continue
        r = {label: nm, "rain_days": int(m["rain"].sum()), "dry_days": int((1 - m["rain"]).sum())}
        for wk, tag in ((0, "weekday"), (1, "weekend")):
            s = m[m.is_weekend == wk]
            rd, dd = s[s.rain == 1]["value"].mean(), s[s.rain == 0]["value"].mean()
            r[f"{tag}_dry_avg"] = round(dd, 1) if pd.notna(dd) else None
            r[f"{tag}_rain_avg"] = round(rd, 1) if pd.notna(rd) else None
            r[f"{tag}_rain_effect"] = round(rd / dd - 1, 3) if pd.notna(rd) and pd.notna(dd) and dd else None
        r["lat"], r["lon"] = lat, lon
        rows.append(r)
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("강우일이 3일 미만이거나 날씨 데이터와 기간이 겹치지 않습니다.")
    out = out.sort_values("weekday_rain_effect").reset_index(drop=True)
    facts = []
    t, h = out.iloc[0], out.iloc[-1]
    facts.append(f"비 올 때(≥{rain_mm}mm) 평일 기준 가장 크게 줄어드는 곳: {t[label]} ({t.weekday_rain_effect:+.0%}, 맑은 날 {t.weekday_dry_avg} → 비 오는 날 {t.weekday_rain_avg})")
    if len(out) > 1:
        facts.append(f"가장 영향 적거나 오히려 늘어나는 곳: {h[label]} ({h.weekday_rain_effect:+.0%})")
    facts.append(f"분석 대상 {len(out)}곳, 강우일 {int(out.rain_days.median())}일/비강우일 {int(out.dry_days.median())}일(중앙값)")
    mp = out.rename(columns={label: "label", "weekday_rain_effect": "value"})[["label", "lat", "lon", "value"]]
    return ToolResult("weather_effect", f"강우 영향 ({target}{', ' + place if place else ''})", out.drop(columns=["lat", "lon"]), facts, [CAVEAT_WX],
                      chart={"type": "bar", "x": label, "y": "weekday_rain_effect", "title": "비 오는 날 평일 변화율"}, map_points=mp,
                      method="대상별 일별 지표에 가장 가까운 관측소의 일강수량을 결합, 강우일/비강우일 평균 비율",
                      params=dict(target=target, place=place, date_from=date_from, date_to=date_to, rain_mm=rain_mm), datasets=ds)
