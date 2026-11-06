"""기간·요일 비교 도구 — "전반과 후반이 어떻게 달라졌나", "평일과 주말 차이", "A기간 vs B기간".

정의: 대상(장소/정류소/노선)별로 A·B 두 구간의 일평균 지표를 구하고 변화율 = B/A − 1 을 낸다.
한계: 두 구간의 요일 구성·공휴일·행사·날씨가 통제되지 않은 단순 비교다. 관측일이 적은 대상은 제외한다.
"""
from __future__ import annotations

import pandas as pd

from .base import ToolResult, fuzzy_match, register, wifi_users_agg
from .congestion import _coords, infer_category

SPLIT_LABEL = {"halves": ("전반", "후반"), "weekday_weekend": ("평일", "주말"), "custom": ("A기간", "B기간")}


def _daily(db, target: str) -> tuple[pd.DataFrame, str, list[str]]:
    """대상별 일별 지표 (label, date, is_weekend, value) + 좌표."""
    if target == "wifi":
        u = db.table("wifi_ap_usage")
        d = u.groupby(["ap_place", "date", "is_weekend"], as_index=False).agg(value=wifi_users_agg(u))
        c = _coords(db)
        d = d.merge(c, on="ap_place", how="left")
        return d.rename(columns={"ap_place": "label"}), "장소", ["wifi_ap_usage"]
    if target == "bus_station":
        b = db.table("bus_station_usage")
        b = b[b["get_on_off_type"] == "승차"]
        d = b.groupby(["bus_station_name", "date", "is_weekend"], as_index=False).agg(value=("user_count", "sum"))
        if db.has("bus_station"):
            st = db.table("bus_station").drop_duplicates("bus_station_name")[["bus_station_name", "latitude", "longitude"]].rename(columns={"latitude": "lat", "longitude": "lon"})
            d = d.merge(st, on="bus_station_name", how="left")
        else:
            d["lat"], d["lon"] = None, None
        return d.rename(columns={"bus_station_name": "label"}), "정류소", ["bus_station_usage"]
    b = db.table("bus_route_usage")
    b = b[b["get_on_off_type"] == "승차"]
    d = b.groupby(["bus_route_name", "date", "is_weekend"], as_index=False).agg(value=("user_count", "sum"))
    d["lat"], d["lon"] = None, None
    return d.rename(columns={"bus_route_name": "label"}), "노선", ["bus_route_usage"]


@register(
    "compare_periods",
    "두 구간(전반/후반, 평일/주말, 또는 임의의 두 기간)의 일평균 이용을 대상별로 비교해 변화율 순으로 나열한다. "
    "'9월 전반과 후반이 어떻게 달라졌나', '평일과 주말 차이가 큰 곳', '추석 연휴 전후 비교', '어디가 늘고 어디가 줄었나' 질문에 사용. "
    "split=halves 는 데이터(또는 date_from~date_to)를 반으로, weekday_weekend 는 평일/주말로, custom 은 a_from~a_to vs b_from~b_to 로 나눈다.",
    {"type": "object", "properties": {
        "target": {"type": "string", "enum": ["wifi", "bus_station", "bus_route"], "description": "비교 대상(기본 wifi=장소 혼잡)"},
        "split": {"type": "string", "enum": ["halves", "weekday_weekend", "custom"], "description": "구간 나누는 방식(기본 halves)"},
        "date_from": {"type": "string", "description": "halves/weekday_weekend 의 전체 범위 시작 YYYY-MM-DD"},
        "date_to": {"type": "string", "description": "전체 범위 끝 YYYY-MM-DD"},
        "a_from": {"type": "string"}, "a_to": {"type": "string"}, "b_from": {"type": "string"}, "b_to": {"type": "string"},
        "place": {"type": "string", "description": "특정 장소·정류소·노선만(부분 일치)"},
        "top_n": {"type": "integer", "description": "증가·감소 상위 각 n개(기본 10)"},
        "min_daily": {"type": "number", "description": "두 구간 모두 일평균이 이 값 이상인 대상만(기본 20)"},
        "exclude_bus_stops": {"type": "boolean", "description": "wifi 대상일 때 정류소·충전소 AP 제외(기본 true)"},
    }},
)
def compare_periods(db, target="wifi", split="halves", date_from=None, date_to=None, a_from=None, a_to=None, b_from=None, b_to=None,
                    place=None, top_n=10, min_daily=20, exclude_bus_stops=True) -> ToolResult:
    d, kind, ds = _daily(db, target)
    if date_from:
        d = d[d["date"] >= date_from]
    if date_to:
        d = d[d["date"] <= date_to]
    if d.empty:
        raise ValueError("지정한 기간에 데이터가 없습니다.")
    if target == "wifi" and exclude_bus_stops:
        cat = d["category"].where(d["category"].notna() & (d["category"] != "미분류"), d["label"].map(infer_category))
        d = d[~cat.isin(["버스정류소", "전기차충전소"])]
    if place:
        nm = fuzzy_match(place, sorted(d["label"].dropna().unique().tolist()))
        if not nm:
            raise ValueError(f"'{place}'를 찾을 수 없습니다.")
        d = d[d["label"] == nm]
        min_daily = 0
    dates = sorted(d["date"].unique())
    if split == "weekday_weekend":
        a, b = d[d["is_weekend"] == 0], d[d["is_weekend"] == 1]
        a_rng, b_rng = f"{dates[0]}~{dates[-1]} 평일", f"{dates[0]}~{dates[-1]} 주말"
    elif split == "custom":
        if not (a_from and a_to and b_from and b_to):
            raise ValueError("split=custom 에는 a_from, a_to, b_from, b_to 가 모두 필요합니다.")
        a, b = d[(d["date"] >= a_from) & (d["date"] <= a_to)], d[(d["date"] >= b_from) & (d["date"] <= b_to)]
        a_rng, b_rng = f"{a_from}~{a_to}", f"{b_from}~{b_to}"
    else:
        split = "halves"
        mid = dates[len(dates) // 2]
        a, b = d[d["date"] < mid], d[d["date"] >= mid]
        a_rng, b_rng = f"{dates[0]}~{a['date'].max() if len(a) else mid}", f"{mid}~{dates[-1]}"
    if a.empty or b.empty:
        raise ValueError(f"두 구간 모두에 데이터가 있어야 합니다 (A {len(a)}행, B {len(b)}행).")
    la, lb = SPLIT_LABEL[split]
    ga = a.groupby("label").agg(a_days=("date", "nunique"), a_avg=("value", "mean"))
    gb = b.groupby("label").agg(b_days=("date", "nunique"), b_avg=("value", "mean"))
    g = ga.join(gb, how="inner").reset_index()
    coords = d.drop_duplicates("label").set_index("label")[["lat", "lon"]]
    g = g.merge(coords, left_on="label", right_index=True, how="left")
    n0 = len(g)
    min_days = 2 if split != "halves" else max(2, min(ga["a_days"].max(), gb["b_days"].max()) // 2)
    g = g[(g["a_days"] >= min_days) & (g["b_days"] >= min_days) & (g[["a_avg", "b_avg"]].min(axis=1) >= (min_daily or 0))]
    dropped = n0 - len(g)
    if g.empty:
        raise ValueError(f"두 구간 모두에서 일평균 {min_daily}명 이상·관측 {min_days}일 이상인 {kind}이(가) 없습니다. min_daily 를 낮추세요.")
    g["change"] = (g["b_avg"] / g["a_avg"] - 1).round(3)
    g["diff"] = (g["b_avg"] - g["a_avg"]).round(1)
    g = g.sort_values("change", ascending=False).reset_index(drop=True)
    if len(g) > 2 * top_n and not place:
        g = pd.concat([g.head(top_n), g.tail(top_n)]).reset_index(drop=True)
    g.insert(0, "rank", range(1, len(g) + 1))
    out = g.rename(columns={"label": kind, "a_avg": f"{la}_일평균", "b_avg": f"{lb}_일평균", "a_days": f"{la}_일수", "b_days": f"{lb}_일수",
                            "change": "변화율", "diff": "증감"}).round(1)
    out["변화율"] = g["change"]
    cols = ["rank", kind, f"{la}_일수", f"{la}_일평균", f"{lb}_일수", f"{lb}_일평균", "증감", "변화율"]
    tot_a, tot_b = float(a.groupby("date")["value"].sum().mean()), float(b.groupby("date")["value"].sum().mean())
    facts = [f"{la}({a_rng}) 대비 {lb}({b_rng}) 전체 일평균 {tot_a:,.0f} → {tot_b:,.0f} ({tot_b / tot_a - 1:+.0%})" if tot_a else ""]
    up, dn = g.iloc[0], g.iloc[-1]
    if len(g) > 1:
        facts.append(f"가장 늘어난 {kind}: {up.label} ({up.a_avg:,.0f} → {up.b_avg:,.0f}, {up.change:+.0%})")
        facts.append(f"가장 줄어든 {kind}: {dn.label} ({dn.a_avg:,.0f} → {dn.b_avg:,.0f}, {dn.change:+.0%})")
    else:
        facts.append(f"{up.label}: {la} {up.a_avg:,.0f} → {lb} {up.b_avg:,.0f} ({up.change:+.0%})")
    facts = [f for f in facts if f]
    cav = ["두 구간의 요일 구성·공휴일·행사·날씨가 통제되지 않은 단순 비교입니다(상관 관계).",
           f"두 구간 모두 관측 {min_days}일 이상·일평균 {min_daily}명 이상인 {kind}만 포함({dropped}곳 제외)."]
    mp = g.rename(columns={"change": "value"})[["label", "lat", "lon", "value"]] if g["lat"].notna().any() else None
    return ToolResult("compare_periods", f"{kind}별 {la}·{lb} 비교 ({target})", out[cols], facts, cav,
                      chart={"type": "bar", "x": kind, "y": "변화율", "title": f"{la}→{lb} 변화율"}, map_points=mp,
                      method=f"{kind}별 일별 합계를 {la}({a_rng})·{lb}({b_rng}) 구간으로 나눠 일평균을 구하고 변화율 = {lb}/{la} − 1",
                      params=dict(target=target, split=split, date_from=date_from, date_to=date_to, a_from=a_from, a_to=a_to, b_from=b_from, b_to=b_to,
                                  place=place, top_n=top_n, min_daily=min_daily), datasets=ds)
