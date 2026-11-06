"""날씨(5-02)와 혼잡·버스 수요를 결합하는 도구.

정의: 강우일 = 가장 가까운 관측소의 일강수량 ≥ 1mm. 효과 = 강우일 일평균 ÷ 비강우일 일평균 − 1 (평일/주말 분리).
한계: 날씨 외 요인(공휴일·행사)이 통제되지 않은 단순 비교다. 인과가 아니라 상관으로 해석해야 한다.
"""
from __future__ import annotations

import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, fuzzy_match, haversine_km, period_filter, register, wifi_users_agg

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


def _station_table(db) -> pd.DataFrame:
    """관측소 코드·좌표 (명세서 예시의 위경도 뒤바뀜 보정)."""
    ws = db.table("weather_station").copy() if db.has("weather_station") else pd.DataFrame()
    if ws.empty:
        return pd.DataFrame({"observatory_code": ["184"], "lat": [33.5141], "lon": [126.5297]})
    la = pd.to_numeric(ws["latitude"], errors="coerce"); lo = pd.to_numeric(ws["longitude"], errors="coerce")
    if la.between(33, 34.5).mean() < 0.5 and lo.between(33, 34.5).mean() >= 0.5:
        la, lo = lo, la
    out = pd.DataFrame({"observatory_code": ws["observatory_code"].astype(str), "lat": la, "lon": lo}).dropna()
    return out.drop_duplicates("observatory_code").reset_index(drop=True)


def _auto_threshold(w: pd.DataFrame, rain_mm: float | None) -> tuple[float, str]:
    """강우일 기준. 지정하지 않으면 1mm 부터 올려 가며 강우일 비율이 15~85% 안에 들어오는 첫 기준을 쓴다
    (재분석 자료는 이슬비까지 잡아 1mm 기준으로는 한 달 거의 전부가 강우일이 되기 때문)."""
    if rain_mm is not None:
        return float(rain_mm), ""
    for t in (1.0, 3.0, 5.0, 10.0, 20.0):
        share = (w["daily_rainfall"] >= t).mean()
        if 0.15 <= share <= 0.6:
            return t, ("" if t == 1.0 else f"1mm 기준으로는 강우일이 {(w['daily_rainfall'] >= 1.0).mean():.0%}라 기준을 {t:g}mm 로 올렸습니다.")
    return 1.0, ""


@register(
    "weather_effect",
    "비 오는 날과 맑은 날의 혼잡(와이파이 이용자) 또는 버스 승차 차이를 계산한다. "
    "'비 오면 어디가 한산해지나', '우천 시 버스 이용 변화', '날씨에 민감한 관광지' 질문에 사용. "
    "place를 주면 그 장소, 생략하면 감소폭·증가폭 상위 각 top_n 을 나열.",
    {"type": "object", "properties": {
        "target": {"type": "string", "enum": ["wifi", "bus_station", "bus_route"], "description": "분석 대상(기본 wifi)"},
        "place": {"type": "string", "description": "장소명/정류소명/노선(부분 일치). 생략 시 전체 순위"},
        **PERIOD_PARAMS,
        "rain_mm": {"type": "number", "description": "강우일 기준 mm. 생략 시 강우일 비율이 15~85%가 되는 기준(1·3·5·10·20mm)을 자동 선택"},
        "top_n": {"type": "integer", "description": "감소·증가 상위 각 n개(기본 10)"},
        "min_daily": {"type": "number", "description": "맑은 날 일평균이 이 값 이상인 대상만(기본 20)"},
    }},
)
def weather_effect(db, target="wifi", place=None, date_from=None, date_to=None, rain_mm=None, top_n=10, min_daily=20) -> ToolResult:
    if not db.has("weather_daily"):
        note = db.provenance["weather_daily"].note if "weather_daily" in db.provenance else ""
        raise ValueError("날씨 데이터(5-02)가 적재되지 않아 강우 영향을 계산할 수 없습니다. " + (note or "scripts/fetch_weather.py 로 수집하세요.") + " 이 질문은 다른 도구로 대체하지 말고 사용자에게 그대로 안내하세요.")
    excluded = ""
    if target == "wifi":
        from .congestion import _coords, infer_category
        u = period_filter(db.table("wifi_ap_usage"), date_from, date_to)
        daily = u.groupby(["ap_place", "date", "is_weekend"], as_index=False).agg(value=wifi_users_agg(u))
        c = _coords(db)
        c["category"] = c["category"].where(c["category"].notna() & (c["category"] != "미분류"), c["ap_place"].map(infer_category))
        daily = daily.merge(c, on="ap_place", how="left")
        if not place:
            daily = daily[~(daily["category"].isin(["버스정류소", "전기차충전소"]) | daily["ap_place"].astype(str).str.match(r"^(버스정류소|전기차충전소)_"))]
            excluded = "버스정류소·전기차충전소 AP 제외"
        label, ds = "ap_place", ["wifi_ap_usage", "weather_daily"]
    elif target == "bus_station":
        b = period_filter(db.table("bus_station_usage"), date_from, date_to)
        b = b[b.get_on_off_type == "승차"]
        daily = b.groupby(["bus_station_name", "date", "is_weekend"], as_index=False).agg(value=("user_count", "sum"))
        if db.has("bus_station"):
            st = db.table("bus_station").drop_duplicates("bus_station_name")[["bus_station_name", "latitude", "longitude"]].rename(columns={"latitude": "lat", "longitude": "lon"})
            daily = daily.merge(st, on="bus_station_name", how="left")
        else:
            daily["lat"], daily["lon"] = None, None
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
        min_daily = 0
    daily = daily.copy()
    daily["lat"] = pd.to_numeric(daily["lat"], errors="coerce").fillna(33.5)
    daily["lon"] = pd.to_numeric(daily["lon"], errors="coerce").fillna(126.53)
    # 대상별 가장 가까운 관측소 (벡터 계산)
    st = _station_table(db)
    ent = daily.drop_duplicates(label)[[label, "lat", "lon"]].reset_index(drop=True)
    dist = [((ent["lat"] - r.lat) ** 2 + ((ent["lon"] - r.lon) * 0.83) ** 2) for r in st.itertuples()]
    ent["observatory_code"] = st["observatory_code"].values[pd.concat(dist, axis=1).values.argmin(axis=1)]
    daily = daily.merge(ent[[label, "observatory_code"]], on=label, how="left")
    w = period_filter(db.table("weather_daily"), date_from, date_to)[["date", "observatory_code", "daily_rainfall"]].copy()
    w["observatory_code"] = w["observatory_code"].astype(str)
    w = w.drop_duplicates(["date", "observatory_code"])
    thr, thr_note = _auto_threshold(w, rain_mm)
    w["rain"] = (w["daily_rainfall"] >= thr).astype(int)
    m = daily.merge(w[["date", "observatory_code", "rain"]], on=["date", "observatory_code"], how="inner")
    if m.empty:
        raise ValueError("날씨 데이터와 기간이 겹치지 않습니다.")
    g = m.groupby([label, "is_weekend", "rain"])["value"].agg(["mean", "size"]).unstack(["is_weekend", "rain"])
    def col(stat, wk, rn):
        key = (stat, wk, rn)
        return g[key] if key in g.columns else pd.Series(index=g.index, dtype=float)
    out = pd.DataFrame({label: g.index})
    out["rain_days"] = (col("size", 0, 1).fillna(0) + col("size", 1, 1).fillna(0)).values.astype(int)
    out["dry_days"] = (col("size", 0, 0).fillna(0) + col("size", 1, 0).fillna(0)).values.astype(int)
    for wk, tag in ((0, "weekday"), (1, "weekend")):
        dd, rd = col("mean", wk, 0).values, col("mean", wk, 1).values
        out[f"{tag}_dry_avg"] = pd.Series(dd).round(1).values
        out[f"{tag}_rain_avg"] = pd.Series(rd).round(1).values
        eff = pd.Series(rd / dd - 1).where(pd.Series(dd) > 0)
        out[f"{tag}_rain_effect"] = eff.round(3).values
    out = out.merge(ent[[label, "lat", "lon"]], on=label, how="left")
    n0 = len(out)
    out = out[(out["rain_days"] >= 3) & (out["dry_days"] >= 3) & out["weekday_rain_effect"].notna()
              & (out["weekday_dry_avg"].fillna(0) >= (min_daily or 0))]
    dropped = n0 - len(out)
    if out.empty:
        raise ValueError(f"강우일·비강우일이 각각 3일 이상이고 맑은 날 일평균 {min_daily} 이상인 대상이 없습니다(기준 {thr:g}mm). rain_mm 또는 min_daily 를 조정하세요.")
    out = out.sort_values("weekday_rain_effect").reset_index(drop=True)
    if len(out) > 2 * top_n and not place:
        out = pd.concat([out.head(top_n), out.tail(top_n)]).reset_index(drop=True)
    facts = []
    t, h = out.iloc[0], out.iloc[-1]
    facts.append(f"비 올 때(≥{thr:g}mm) 평일 기준 가장 크게 줄어드는 곳: {t[label]} ({t.weekday_rain_effect:+.0%}, 맑은 날 {t.weekday_dry_avg} → 비 오는 날 {t.weekday_rain_avg})")
    if len(out) > 1:
        facts.append(f"가장 영향 적거나 오히려 늘어나는 곳: {h[label]} ({h.weekday_rain_effect:+.0%}, 맑은 날 {h.weekday_dry_avg} → 비 오는 날 {h.weekday_rain_avg})")
    per = w.groupby("observatory_code")["rain"].agg(["sum", "size"])
    facts.append(f"분석 대상 {n0 - dropped}곳(표에는 감소·증가 상위 각 {top_n}), 관측소별 강우일 {int(per['sum'].median())}일/비강우일 {int((per['size'] - per['sum']).median())}일(중앙값, 기준 {thr:g}mm)")
    mp = out.rename(columns={label: "label", "weekday_rain_effect": "value"})[["label", "lat", "lon", "value"]]
    cav = [CAVEAT_WX, f"강우일 기준 {thr:g}mm" + (" — " + thr_note if thr_note else ""),
           f"강우일·비강우일 각 3일 이상, 맑은 날 일평균 {min_daily} 이상인 대상만({dropped}곳 제외)" + (" · " + excluded if excluded else "")]
    return ToolResult("weather_effect", f"강우 영향 ({target}{', ' + place if place else ''})", out.drop(columns=["lat", "lon"]), facts, cav,
                      chart={"type": "bar", "x": label, "y": "weekday_rain_effect", "title": "비 오는 날 평일 변화율"}, map_points=mp,
                      method=f"대상별 일별 지표에 가장 가까운 관측소의 일강수량을 결합, 강우일(≥{thr:g}mm)/비강우일 평균 비율 − 1 (평일·주말 분리)",
                      params=dict(target=target, place=place, date_from=date_from, date_to=date_to, rain_mm=thr, top_n=top_n, min_daily=min_daily), datasets=ds)
