"""예측·브리핑 도구 — 우리 데이터(반출 가능·일별)의 강점을 살린 '앞을 보는' 기능.

congestion_forecast: 다음 주말(또는 지정 날짜) 장소별 예상 혼잡.
  방법(단순·설명 가능): 예측값 = 최근 4회 같은 요일 이용자 중앙값 × 추세계수,
  추세계수 = 최근 4주 평균 ÷ 그 전 4주 평균 (0.7~1.5로 제한). 예상 혼잡지수 = 예측값 ÷ 평일 중앙값.
  한계: 공휴일·행사·날씨 예보를 반영하지 않는 패턴 기반 추정이며, 구간(최근 4회의 최소~최대)을 함께 제시한다.

monthly_brief: 특정 월의 혼잡·외국인·노선·강우를 한 장으로 묶은 브리핑(관광지표 대응 상황실용).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, register, wifi_is_aggregated, wifi_users_agg, wifi_users_count
from .bus import VULNERABLE
from .congestion import CAVEAT_WIFI, _coords, _daily_users, _with_index


def _next_weekend(after: str | None) -> tuple[date, date]:
    d = date.fromisoformat(after) if after else date.today()
    sat = d + timedelta(days=(5 - d.weekday()) % 7 or 7)
    return sat, sat + timedelta(days=1)


@register(
    "congestion_forecast",
    "다음 주말(또는 지정 날짜)의 장소별 예상 혼잡을 최근 같은 요일 패턴과 추세로 추정한다. "
    "'이번 주말 어디가 붐빌까', '다음 토요일 성산 혼잡 예상', '연휴 분산 안내할 곳' 질문에 사용. "
    "데이터의 마지막 날짜 이후를 예측하므로 target_date는 데이터 범위 밖이어도 된다.",
    {"type": "object", "properties": {
        "target_date": {"type": "string", "description": "예측 대상 날짜 YYYY-MM-DD (생략 시 데이터 마지막 날짜 다음 토요일)"},
        "place": {"type": "string", "description": "특정 장소만 볼 때(부분 일치)"},
        "top_n": {"type": "integer", "description": "상위 몇 개(기본 10)"},
    }},
)
def congestion_forecast(db, target_date=None, place=None, top_n=10) -> ToolResult:
    daily = _with_index(_daily_users(db))
    last = date.fromisoformat(str(daily["date"].max()))
    if target_date:
        tgt = date.fromisoformat(target_date)
    else:
        tgt = _next_weekend(str(last))[0]
    wd = tgt.weekday()
    daily["d"] = pd.to_datetime(daily["date"])
    hist = daily[daily["d"] <= pd.Timestamp(last)]
    cats = _coords(db)[["ap_place", "category"]]
    hist = hist.merge(cats, on="ap_place", how="left")
    if not place:
        hist = hist[~hist["category"].isin(["버스정류소", "전기차충전소"])]
    total_days = max(int(hist["date"].nunique()), 1)
    rows = []
    for nm, g in hist.groupby("ap_place"):
        if place and place.replace(" ", "") not in nm.replace(" ", ""):
            continue
        same = g[g["d"].dt.weekday == wd].sort_values("d").tail(4)
        if len(same) < 2:
            continue
        recent14 = g[g["d"] > pd.Timestamp(last) - pd.Timedelta(days=14)]
        if not place and (len(recent14) < 9 or recent14["users"].mean() < 20 or len(same) < 3):
            continue   # 최근 2주 관측 9일 미만 또는 일평균 20명 미만인 소표본 장소는 예측 순위에서 제외
        recent = g[g["d"] > pd.Timestamp(last) - pd.Timedelta(days=28)]["users"].mean()
        prior = g[(g["d"] <= pd.Timestamp(last) - pd.Timedelta(days=28)) & (g["d"] > pd.Timestamp(last) - pd.Timedelta(days=56))]["users"].mean()
        trend = float(min(max(recent / prior, 0.7), 1.5)) if prior and pd.notna(prior) and pd.notna(recent) else 1.0
        base = float(same["users"].median())
        lo, hi = float(same['users'].min()), float(same['users'].max())
        pred = min(max(base * trend, lo * 0.9), hi * 1.1)  # 관측 구간을 크게 벗어나지 않도록
        baseline = float(g["baseline"].iloc[0]) if pd.notna(g["baseline"].iloc[0]) else None
        rows.append({"ap_place": nm, "predicted_users": round(pred), "range_low": int(same["users"].min()), "range_high": int(same["users"].max()),
                     "trend_factor": round(trend, 2), "predicted_congestion_index": round(pred / baseline, 2) if baseline else None,
                     "basis_days": len(same)})
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("예측에 쓸 같은 요일 데이터가 2일 미만입니다.")
    out = out.sort_values("predicted_congestion_index", ascending=False).head(top_n).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    out = out.merge(_coords(db), on="ap_place", how="left")
    dow = "월화수목금토일"[wd]
    facts = [f"{tgt}({dow}) 예상 혼잡 1위 {out.iloc[0].ap_place}: 예상 이용자 {out.iloc[0].predicted_users}명(최근 같은 요일 {out.iloc[0].range_low}~{out.iloc[0].range_high}명), 예상 혼잡지수 {out.iloc[0].predicted_congestion_index}",
             f"추세계수(최근 4주÷이전 4주) 최대 {out.trend_factor.max()} ({out.loc[out.trend_factor.idxmax(), 'ap_place']}), 데이터 마지막 날짜 {last}"]
    if len(out) > 2:
        facts.append(f"예상 혼잡지수 1.5 이상(평일의 1.5배 초과) 장소 {int((out.predicted_congestion_index >= 1.5).sum())}곳 → 분산 안내 후보")
    mp = out.rename(columns={"ap_place": "label", "predicted_congestion_index": "value"})[["label", "lat", "lon", "value"]]
    return ToolResult("congestion_forecast", f"{tgt}({dow}) 예상 혼잡", out.drop(columns=["lat", "lon", "category"], errors="ignore"), facts,
                      [CAVEAT_WIFI, "공휴일·행사·날씨 예보를 반영하지 않은 요일 패턴 기반 단순 추정입니다. 구간(최근 같은 요일 최소~최대)과 함께 보세요.",
                       "최근 2주 관측 9일 미만이거나 일평균 20명 미만인 장소와 정류소·충전소 AP는 제외했습니다."],
                      chart={"type": "bar", "x": "ap_place", "y": "predicted_congestion_index", "title": f"{tgt} 예상 혼잡지수"}, map_points=mp,
                      method="최근 4회 같은 요일 이용자 중앙값 × 추세계수(최근4주/이전4주, 0.7~1.5) ÷ 평일 중앙값",
                      params=dict(target_date=str(tgt), place=place, top_n=top_n), datasets=["wifi_ap_usage"])


@register(
    "monthly_brief",
    "특정 월의 혼잡 관광지·외국인 비중 변화·저이용 노선·강우일 영향을 한 장으로 묶은 월간 브리핑을 만든다. "
    "'5월 관광 상황 요약', '월간 브리핑', '지난달 어땠어' 질문에 사용.",
    {"type": "object", "properties": {"month": {"type": "string", "description": "YYYY-MM (생략 시 데이터 마지막 달)"}}},
)
def monthly_brief(db, month=None) -> ToolResult:
    u = db.table("wifi_ap_usage")
    month = month or str(u["ym"].max())
    prev = (pd.Period(month) - 1).strftime("%Y-%m")
    cur = u[u["ym"] == month]
    pre = u[u["ym"] == prev]
    if cur.empty:
        raise ValueError(f"{month} 데이터가 없습니다. 데이터 범위: {u['ym'].min()}~{u['ym'].max()}")
    agg_mode = wifi_is_aggregated(u)
    def stats(df):
        users = wifi_users_count(df)
        if agg_mode:
            return users, float("nan"), float("nan")
        return users, (df["nationality"] != "대한민국").sum() / max(len(df), 1), (df["visit_type"] == "관광").sum() / max(len(df), 1)
    cu, cf, ct = stats(cur)
    pu, pf, pt = stats(pre) if not pre.empty else (None, None, None)
    top = cur.groupby("ap_place").agg(v=wifi_users_agg(u))["v"].sort_values(ascending=False).head(5)
    top_f = (cur[cur["nationality"] != "대한민국"].groupby("ap_place")["user_id"].nunique().sort_values(ascending=False).head(3)
             if not agg_mode else pd.Series(dtype=float))
    label_users = "와이파이 이용자(장소×일 합)" if agg_mode else "와이파이 고유 이용자"
    rows = [{"지표": label_users, "값": f"{cu:,}", "전월": f"{pu:,}" if pu else "-", "변화": f"{(cu / pu - 1):+.1%}" if pu else "-"}]
    if not agg_mode:
        rows += [{"지표": "외국인 비중", "값": f"{cf:.1%}", "전월": f"{pf:.1%}" if pf is not None else "-", "변화": f"{(cf - pf) * 100:+.1f}%p" if pf is not None else "-"},
                 {"지표": "관광 방문 비중", "값": f"{ct:.1%}", "전월": f"{pt:.1%}" if pt is not None else "-", "변화": f"{(ct - pt) * 100:+.1f}%p" if pt is not None else "-"}]
    rows.append({"지표": "이용자 상위 5곳", "값": ", ".join(f"{k}({v:,})" for k, v in top.items()), "전월": "", "변화": ""})
    if not agg_mode:
        rows.append({"지표": "외국인 상위 3곳", "값": ", ".join(f"{k}({v:,})" for k, v in top_f.items()), "전월": "", "변화": ""})
    facts = [f"{month} {label_users} {cu:,}명" + (f" (전월比 {(cu / pu - 1):+.1%})" if pu else "")]
    if not agg_mode:
        facts.append(f"외국인 비중 {cf:.1%}" + (f" (전월 {pf:.1%})" if pf is not None else "") + f", 관광 방문 비중 {ct:.1%}")
        facts.append(f"이용자 상위: {', '.join(top.index[:3])} / 외국인 상위: {', '.join(top_f.index[:3])}")
    else:
        facts.append(f"이용자 상위: {', '.join(top.index[:3])} (허브 집계본이라 국적·방문유형 비중은 없음)")
    datasets = ["wifi_ap_usage"]
    if db.has("bus_route_usage"):
        b = db.table("bus_route_usage"); b = b[(b["ym"] == month) & (b["get_on_off_type"] == "승차")]
        days = max(b["date"].nunique(), 1)
        if len(b):
            hourly = b.groupby(["bus_route_name", "hour"])["user_count"].sum().reset_index()
            peak = hourly.groupby("bus_route_name")["user_count"].transform("max")
            low = hourly[(hourly.hour.between(5, 23)) & (hourly.user_count < peak * 0.15)].groupby("bus_route_name").size().sort_values(ascending=False).head(3)
            vul = b[b.user_category.isin(VULNERABLE)].user_count.sum() / b.user_count.sum()
            rows.append({"지표": "버스 일평균 승차(노선 합)", "값": f"{b.user_count.sum() / days:,.0f}", "전월": "", "변화": ""})
            has_hour = b["hour"].notna().any()
            if has_hour:
                rows.append({"지표": "저이용 시간대 많은 노선", "값": ", ".join(f"{k.split('(')[0]}({v}개)" for k, v in low.items()), "전월": "", "변화": ""})
            else:
                eff = b.groupby("bus_route_name")["user_count"].sum().sort_values().head(3)
                rows.append({"지표": "승차 최저 노선 3", "값": ", ".join(f"{k.split('(')[0]}({v / days:,.0f}명/일)" for k, v in eff.items()), "전월": "", "변화": ""})
            facts.append(f"버스 일평균 승차 {b.user_count.sum() / days:,.0f}명, 교통약자 비중 {vul:.0%}, " +
                         (f"저이용 시간대 최다 노선 {', '.join(k.split('(')[0] for k in low.index)}" if has_hour else
                          f"승차 최저 노선 {', '.join(k.split('(')[0] for k in eff.index)} (시간대 컬럼 없음 → 일 단위)"))
            datasets.append("bus_route_usage")
    if db.has("weather_daily"):
        w = db.table("weather_daily"); w = w[(w["ym"] == month) & (w["observatory_code"].astype(str) == "184")]
        if len(w):
            rain_days = int((w["daily_rainfall"] >= 1).sum())
            rain_days5 = int((w["daily_rainfall"] >= 5).sum())
            rows.append({"지표": "강우일(제주 184)", "값": f"≥1mm {rain_days}일 · ≥5mm {rain_days5}일 / {w['date'].nunique()}일", "전월": "", "변화": ""})
            facts.append(f"강우일(제주 관측소) 1mm 이상 {rain_days}일·5mm 이상 {rain_days5}일, 평균기온 {w['average_temperature'].mean():.1f}℃")
            datasets.append("weather_daily")
    out = pd.DataFrame(rows)
    return ToolResult("monthly_brief", f"{month} 월간 브리핑", out, facts, [CAVEAT_WIFI, "전월 비교는 단순 비교이며 계절·공휴일 효과가 포함됩니다."],
                      method="해당 월 와이파이 고유 이용자·국적·방문유형 집계 + 노선 저이용 시간대 + 강우일 수", params=dict(month=month), datasets=datasets)
