"""자율 탐색 도구 — 질문이 없어도 Agent가 데이터를 훑어 '오늘의 발견'을 만든다.

정의 (재현 가능하도록 명시)
- 대상: 장소(4-05 와이파이 이용자), 정류소(3-04 승차), 노선(3-02 승차)의 일별 지표.
- 평소값 b(대상, 요일구분) = 같은 대상의 평일/주말별 중앙값(관측 3일 미만이면 전체 중앙값).
- 배율 r(대상, 날) = 그날 값 ÷ 평소값.
- 발견 유형
  · 급증: r 최대 날이 1.8배 이상            · 급감: r 최소 날이 0.55배 이하
  · 최근 붐빔/한산: 최근 7일 평균 ÷ 그 이전 평균이 1.4배 이상 / 0.7배 이하 (관측 14일 이상일 때)
- 점수 = |log2(배율)| × (1 + log10(평소값 ÷ 최소 일평균)) — 같은 배율이면 큰 곳을 먼저. 관측값 10명 미만(수집 공백 의심)은 ×0.3.
- 제외: 전체 합계가 전체 중앙값의 50% 미만인 날(제공처 공백일), 일 중앙값이 min_daily 미만이거나 관측일이 기간의 2/3 미만인 대상,
  정류소·충전소 AP(장소).
- 다양성: 상위 top_n 을 고를 때 유형(급증/급감/추세)이 겹치지 않게, 다음은 (구분, 유형)이 겹치지 않게 고른 뒤 남는 자리를 점수순으로 채운다.
한계: 이상치는 '평소와 다르다'는 신호일 뿐 원인(행사·날씨·공사·수집 오류)은 데이터에 없다. 원인은 현장 확인이 필요하다.
"""
from __future__ import annotations

import math

import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, register
from .compare import _daily
from .congestion import CAVEAT_WIFI, infer_category

SPIKE, DROP, TREND_UP, TREND_DOWN = 1.8, 0.55, 1.4, 0.7
KIND_Q = {   # 발견을 눌렀을 때 이어서 물어볼 질문 (규칙 플래너가 처리 가능한 형태)
    "장소": lambda n: f"{n} 언제 가장 붐비나?",
    "정류소": lambda n: f"{n} 정류소는 언제 붐비나?",
    "노선": lambda n: f"{n.split('(')[0]}번 노선 이용 현황 알려줘",
}


def _series(db, target: str, date_from, date_to, min_daily: int, exclude_bus_stops: bool):
    d, kind, ds = _daily(db, target)
    if date_from:
        d = d[d["date"] >= date_from]
    if date_to:
        d = d[d["date"] <= date_to]
    if d.empty:
        return None, kind, ds, 0, []
    if target == "wifi" and exclude_bus_stops:
        cat = d["category"].where(d["category"].notna() & (d["category"] != "미분류"), d["label"].map(infer_category)) if "category" in d.columns else d["label"].map(infer_category)
        d = d[~(cat.isin(["버스정류소", "전기차충전소"]) | d["label"].astype(str).str.match(r"^(버스정류소|전기차충전소)_"))]
    # 제공처 공백일: 전체 합계가 전체 중앙값의 절반 미만인 날은 급감 판정에서 제외
    tot = d.groupby("date")["value"].sum()
    gap_days = sorted(tot[tot < tot.median() * 0.5].index.tolist())
    d = d[~d["date"].isin(gap_days)]
    n_days = d["date"].nunique()
    stat = d.groupby("label")["value"].agg(["median", "count"])
    keep = stat[(stat["median"] >= min_daily) & (stat["count"] >= max(7, int(n_days * 2 / 3)))].index
    dropped = int(len(stat) - len(keep))
    d = d[d["label"].isin(keep)].copy()
    return d, kind, ds, dropped, gap_days


def _findings(d: pd.DataFrame, kind: str, min_daily: int) -> list[dict]:
    out: list[dict] = []
    if d is None or d.empty:
        return out
    dates = sorted(d["date"].unique())
    cut = dates[-7] if len(dates) >= 14 else None
    for label, g in d.groupby("label"):
        g = g.sort_values("date")
        overall = float(g["value"].median())
        base = {}
        for we in (0, 1):
            s = g[g["is_weekend"] == we]["value"]
            base[we] = float(s.median()) if len(s) >= 3 else overall
        g = g.assign(base=g["is_weekend"].map(base))
        g = g[g["base"] > 0]
        if g.empty:
            continue
        g = g.assign(ratio=g["value"] / g["base"])
        lat = g["lat"].iloc[0] if "lat" in g.columns else None
        lon = g["lon"].iloc[0] if "lon" in g.columns else None

        def add(kind_, typ, when, val, ref, ratio, desc):
            out.append({"구분": kind_, "대상": label, "유형": typ, "날짜": when, "관측값": round(float(val), 1), "평소": round(float(ref), 1),
                        "배율": round(float(ratio), 2), "설명": desc, "lat": lat, "lon": lon,
                        "_score": abs(math.log2(max(ratio, 1e-9))) * (1 + math.log10(max(ref / max(min_daily, 1), 1))) * (0.3 if val < 10 else 1.0)})

        hi = g.loc[g["ratio"].idxmax()]
        if hi["ratio"] >= SPIKE:
            add(kind, "급증", hi["date"], hi["value"], hi["base"], hi["ratio"],
                f"{hi['date']}({'주말' if hi['is_weekend'] else '평일'}) {hi['value']:,.0f}명으로 평소 {'주말' if hi['is_weekend'] else '평일'} {hi['base']:,.0f}명의 {hi['ratio']:.1f}배")
        lo = g.loc[g["ratio"].idxmin()]
        if lo["ratio"] <= DROP:
            v = "10명 미만" if lo["value"] < 10 else f"{lo['value']:,.0f}명"
            add(kind, "급감", lo["date"], lo["value"], lo["base"], lo["ratio"],
                f"{lo['date']}({'주말' if lo['is_weekend'] else '평일'}) {v}으로 평소의 {lo['ratio'] * 100:.0f}% 수준" + (" — 수집 공백 가능성" if lo["value"] < 10 else ""))
        if cut:
            rec, pri = g[g["date"] >= cut]["value"], g[g["date"] < cut]["value"]
            if len(rec) >= 4 and len(pri) >= 7 and pri.mean() > 0:
                tr = float(rec.mean() / pri.mean())
                if tr >= TREND_UP:
                    add(kind, "최근 붐빔", f"{cut}~{dates[-1]}", rec.mean(), pri.mean(), tr, f"최근 7일 일평균 {rec.mean():,.0f}명, 그 전 {pri.mean():,.0f}명의 {tr:.1f}배")
                elif tr <= TREND_DOWN:
                    add(kind, "최근 한산", f"{cut}~{dates[-1]}", rec.mean(), pri.mean(), tr, f"최근 7일 일평균 {rec.mean():,.0f}명, 그 전 {pri.mean():,.0f}명의 {tr * 100:.0f}%")
    return out


def _diverse_top(rows: list[dict], n: int) -> list[dict]:
    """점수순이되 1라운드는 유형(급증/급감/추세)이, 2라운드는 (구분, 유형)이 겹치지 않게 고르고, 남는 자리는 점수순."""
    rows = sorted(rows, key=lambda r: r["_score"], reverse=True)
    picked: list[dict] = []
    for keyf in (lambda r: r["유형"], lambda r: (r["구분"], r["유형"]), lambda r: id(r)):
        seen = {keyf(p) for p in picked}
        for r in rows:
            if len(picked) >= n:
                return picked
            k = keyf(r)
            if k not in seen and r not in picked:
                picked.append(r); seen.add(k)
    return picked


@register(
    "explore_anomalies",
    "질문 없이도 데이터를 스스로 훑어 눈에 띄는 변화(갑자기 붐빈 장소, 평소보다 한산해진 정류소, 최근 승차가 줄어든 노선 등)를 근거와 함께 찾아낸다. "
    "'눈에 띄는 게 있어?', '특이한 점 찾아줘', '오늘의 발견', '알아서 둘러봐', '이상한 데 없어?', '뭐부터 봐야 해?' 같은 열린 질문에 사용. "
    "target=all 이면 장소·정류소·노선을 모두 본다.",
    {"type": "object", "properties": {
        **PERIOD_PARAMS,
        "target": {"type": "string", "enum": ["all", "wifi", "bus_station", "bus_route"], "description": "탐색 대상(기본 all)"},
        "top_n": {"type": "integer", "description": "보여줄 발견 개수(기본 3, 최대 20)"},
        "min_daily": {"type": "number", "description": "일 중앙값이 이 값 이상인 대상만(기본 20)"},
    }},
)
def explore_anomalies(db, date_from=None, date_to=None, target="all", top_n=3, min_daily=20) -> ToolResult:
    top_n = max(1, min(int(top_n or 3), 20))
    targets = ["wifi", "bus_station", "bus_route"] if target in (None, "all") else [target]
    rows, datasets, cav_extra, scanned = [], [], [], {}
    for t in targets:
        key = {"wifi": "wifi_ap_usage", "bus_station": "bus_station_usage", "bus_route": "bus_route_usage"}[t]
        if not db.has(key):
            continue
        d, kind, ds, dropped, gaps = _series(db, t, date_from, date_to, min_daily, True)
        datasets += ds
        if d is None:
            continue
        scanned[kind] = (int(d["label"].nunique()), dropped)
        if gaps:
            cav_extra.append(f"{kind} 데이터에서 전체 합계가 평소의 절반 미만인 {len(gaps)}일({', '.join(x[5:] for x in gaps[:5])}{'…' if len(gaps) > 5 else ''})은 제공처 공백으로 보고 제외")
        rows += _findings(d, kind, min_daily)
    if not rows:
        raise ValueError("기준(급증 1.8배·급감 0.55배·최근 추세 1.4/0.7배)에 걸리는 변화가 없습니다. min_daily 를 낮추거나 기간을 넓혀 보세요.")
    picked = _diverse_top(rows, top_n)
    out = pd.DataFrame(picked).drop(columns=["_score"])
    out.insert(0, "rank", range(1, len(out) + 1))
    questions = [KIND_Q[r["구분"]](r["대상"]) for r in picked]
    facts = [f"{r['구분']} '{r['대상']}' {r['유형']}: {r['설명']}" for r in picked]
    facts.append("탐색 범위: " + ", ".join(f"{k} {n}곳(표본 미달 {x}곳 제외)" for k, (n, x) in scanned.items()) + f" → 기준에 걸린 변화 {len(rows)}건 중 상위 {len(picked)}건")
    cav = [CAVEAT_WIFI, "평소값 = 같은 대상의 평일/주말별 중앙값. 급증 1.8배 이상·급감 0.55배 이하·최근 7일 추세 1.4배 이상/0.7배 이하만 '발견'으로 봅니다.",
           f"일 중앙값 {min_daily}명 미만 또는 관측일이 기간의 2/3 미만인 대상과 정류소·충전소 AP는 제외했습니다.",
           "변화의 원인(행사·날씨·공사·수집 오류)은 데이터에 없으므로 현장 확인이 필요합니다(상관≠인과)."] + cav_extra
    mp = out[out["lat"].notna()].rename(columns={"대상": "label", "배율": "value"})[["label", "lat", "lon", "value"]] if out["lat"].notna().any() else None
    res = ToolResult("explore_anomalies", f"오늘의 발견 — 평소와 다른 변화 {len(picked)}건", out, facts, cav,
                     chart={"type": "bar", "x": "대상", "y": "배율", "title": "평소 대비 배율 (1.0 = 평소)"}, map_points=mp,
                     method="대상별 일별 값 ÷ 평일/주말 중앙값 = 배율; 급증·급감은 배율 극값, 추세는 최근 7일 평균 ÷ 이전 평균; 점수 = |log2 배율|×(1+log10(평소/최소기준))로 정렬 후 (구분,유형) 다양성 선택",
                     params=dict(date_from=date_from, date_to=date_to, target=target or "all", top_n=top_n, min_daily=min_daily),
                     datasets=sorted(set(datasets)))
    res.extra = {"questions": questions}  # type: ignore[attr-defined]
    return res
