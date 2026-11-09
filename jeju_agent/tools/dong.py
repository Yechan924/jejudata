"""읍면동 유동인구 결합 도구 — 허브 공개 API「읍면동 단위 내국인 유동인구」(통신사 기반)와 공공 와이파이 이용자(4-05)를 읍면동 단위로 대조한다.

왜: 이 서비스의 혼잡지수는 "와이파이 접속자 = 방문 규모"라는 가정 위에 있다. 통신사 유동인구는 그 가정을 확인할 수 있는
    가장 가까운 외부 기준이며, 허브 공개본은 읍면동·일 단위 집계라 집에서도 받을 수 있다(센터 원천 격자 데이터는 반출 불가).
정의
- 허브 응답은 읍면동×성별×연령대 행마다 거주인구(resdPop)·근무인구(workPop)·방문자수(visitPop)를 준다.
  유동인구(읍면동, 날) = 성별·연령 전체 합. 기본 지표는 **방문자수**(와이파이가 재는 '방문'에 대응), metric="total" 이면 세 값의 합.
- 와이파이(읍면동, 날) = 그 읍면동에 주소를 둔 AP 장소의 일별 이용자 합(4-05 × 4-01 address_dong). 정류소·충전소 AP 제외.
- 읍면동 연결: 4-01 주소의 법정동(화북이동·아라일동·구좌읍 상도리 …)을 행정동(화북동·아라동·구좌읍)으로 바꾼 뒤 이름 일치.
  유동인구 쪽이 법정동이면 변환 없이 먼저 맞춰 본다. 변환표는 LEGAL_TO_ADMIN (서귀동처럼 여러 행정동에 걸치는 법정동은 제외).
- 표본 비율(‰) = 와이파이 일평균 ÷ 유동인구 일평균 × 1000. 비율이 읍면동마다 크게 다르면 혼잡지수는 '순위' 수준으로만 해석해야 한다.
한계: 유동인구는 통신사 가입자 기반 추정치이고 와이파이는 AP가 있는 장소만 잰다. 둘의 상관은 인과가 아니다.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .base import PERIOD_PARAMS, ToolResult, period_filter, register, wifi_users_agg
from .congestion import TOURIST_CATS, _coords, infer_category

DATASET = "dong_population"

# 제주 법정동 → 행정동 (제주특별자치도 행정구역 기준). 읍·면은 그대로(리는 버림).
LEGAL_TO_ADMIN = {
    # 제주시
    "일도일동": "일도1동", "일도이동": "일도2동", "이도일동": "이도1동", "이도이동": "이도2동", "도남동": "이도2동",
    "삼도일동": "삼도1동", "삼도이동": "삼도2동", "용담일동": "용담1동", "용담이동": "용담2동", "용담삼동": "용담2동",
    "건입동": "건입동", "화북일동": "화북동", "화북이동": "화북동", "삼양일동": "삼양동", "삼양이동": "삼양동", "삼양삼동": "삼양동",
    "도련일동": "삼양동", "도련이동": "삼양동", "봉개동": "봉개동", "회천동": "봉개동", "용강동": "봉개동",
    "아라일동": "아라동", "아라이동": "아라동", "오등동": "아라동", "영평동": "아라동", "월평동": "아라동",
    "오라일동": "오라동", "오라이동": "오라동", "오라삼동": "오라동", "연동": "연동", "노형동": "노형동", "해안동": "노형동",
    "외도일동": "외도동", "외도이동": "외도동", "내도동": "외도동", "도평동": "외도동",
    "이호일동": "이호동", "이호이동": "이호동", "도두일동": "도두동", "도두이동": "도두동",
    # 서귀포시
    "동홍동": "동홍동", "서홍동": "서홍동", "보목동": "송산동", "토평동": "영천동", "상효동": "영천동",
    "신효동": "효돈동", "하효동": "효돈동", "법환동": "대륜동", "서호동": "대륜동", "호근동": "대륜동",
    "강정동": "대천동", "도순동": "대천동", "영남동": "대천동", "월평동(서귀포시)": "대천동",
    "중문동": "중문동", "회수동": "중문동", "대포동": "중문동", "하원동": "중문동",
    "상예동": "예래동", "하예동": "예래동", "색달동": "예래동",
}
AMBIGUOUS_LEGAL = {"서귀동"}   # 송산·정방·중앙·천지동에 걸침 → 연결하지 않음


def _norm(s: str) -> str:
    return re.sub(r"[\s\(\)\[\]·\-_]", "", str(s or ""))


def admin_dong(address_dong: str, city_hint: str | None = None) -> str | None:
    """'제주특별자치도 제주시 화북이동' / '서귀포시 강정동' / '제주시 구좌읍 상도리' → 행정동명. 모르면 None."""
    toks = [t for t in str(address_dong or "").split() if t and not t.startswith("제주특별")]
    if not toks:
        return None
    city = next((t for t in toks if t in ("제주시", "서귀포시")), city_hint)
    rest = [t for t in toks if t not in ("제주시", "서귀포시")]
    if not rest:
        return None
    first = rest[0]
    if first.endswith(("읍", "면")):
        return first
    if first in AMBIGUOUS_LEGAL:
        return None
    if first == "월평동" and city == "서귀포시":
        return "대천동"
    if first in LEGAL_TO_ADMIN:
        return LEGAL_TO_ADMIN[first]
    if re.fullmatch(r".+\d동", first) or first.endswith("동"):
        return first
    return None


def wifi_by_dong(db, date_from=None, date_to=None, exclude_bus_stops: bool = True, tourist_only: bool = False) -> tuple[pd.DataFrame, float]:
    """(읍면동, 날짜, is_weekend, wifi, n_places) 와 연결률. tourist_only 면 관광 범주(관광지·해변·시장·올레·공원 …) AP 장소만 — 관공서·도서관 등 평일형 공공시설을 뺀다."""
    u = period_filter(db.table("wifi_ap_usage"), date_from, date_to)
    if exclude_bus_stops or tourist_only:
        cat = u["ap_category"].where(u["ap_category"].notna() & (u["ap_category"] != "미분류"), u["ap_place"].map(infer_category))
        u = u[~(cat.isin(["버스정류소", "전기차충전소"]) | u["ap_place"].astype(str).str.match(r"^(버스정류소|전기차충전소)_"))]
    if tourist_only:
        tcat = _coords(db).set_index("ap_place")["category"]
        u = u[u["ap_place"].map(tcat).isin(TOURIST_CATS)]
    daily = u.groupby(["ap_place", "date", "is_weekend"], as_index=False).agg(wifi=wifi_users_agg(u))
    ap = db.table("wifi_ap") if db.has("wifi_ap") else None
    if ap is None or "address_dong" not in ap.columns or "ap_group_name" not in ap.columns:
        return pd.DataFrame(columns=["dong", "date", "is_weekend", "wifi", "n_places"]), 0.0
    ref = ap.dropna(subset=["address_dong"]).groupby("ap_group_name")["address_dong"].agg(lambda s: s.mode().iloc[0])
    ref = ref.map(admin_dong).dropna()
    key = {_norm(k): v for k, v in ref.items()}
    place_dong = daily[["ap_place"]].drop_duplicates()
    place_dong["dong"] = place_dong["ap_place"].map(lambda p: key.get(_norm(p)))
    linked = place_dong["dong"].notna().mean() if len(place_dong) else 0.0
    d = daily.merge(place_dong.dropna(), on="ap_place")
    out = d.groupby(["dong", "date", "is_weekend"], as_index=False).agg(wifi=("wifi", "sum"), n_places=("ap_place", "nunique"))
    return out, float(linked)


METRIC_KO = {"visitor": "방문자수", "total": "거주+근무+방문 합계"}


def population_by_dong(db, date_from=None, date_to=None, metric: str = "visitor") -> pd.DataFrame:
    """허브 유동인구 → (dong, date, population). 성별·연령 행을 합산. metric: visitor(방문자수, 기본) | total(거주+근무+방문)."""
    p = period_filter(db.table(DATASET), date_from, date_to).copy()
    p["dong"] = p["dong_name"].astype(str).str.strip()
    cols = [c for c in ("resident_pop", "worker_pop", "visitor_pop") if c in p.columns]
    for c in cols:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    if metric == "total" and cols:
        p["population"] = p[cols].sum(axis=1, min_count=1)
    elif "visitor_pop" in p.columns:
        p["population"] = p["visitor_pop"]
    elif "population" not in p.columns:
        p["population"] = p[cols].sum(axis=1, min_count=1) if cols else np.nan
    p = p.dropna(subset=["dong_name", "population"])
    return p.groupby(["dong", "date"], as_index=False).agg(population=("population", "sum"))


def _link_names(pop_dongs: list[str], wifi_dongs: list[str]) -> dict[str, str]:
    """유동인구 쪽 읍면동명 → 와이파이 쪽(행정동 변환본) 이름. 정규화 일치 → 숫자 표기('이도2동'/'이도이동') 통일 → 포함."""
    conv = {"일": "1", "이": "2", "삼": "3"}
    def k2(s: str) -> str:
        n = _norm(s)
        return re.sub(r"(일|이|삼)동$", lambda m: conv[m.group(1)] + "동", n)
    wk = {k2(w): w for w in wifi_dongs}
    out = {}
    for pdg in pop_dongs:
        k = k2(pdg)
        if k in wk:
            out[pdg] = wk[k]
            continue
        hit = [w for kk, w in wk.items() if k and (k in kk or kk in k)]
        if len(hit) == 1:
            out[pdg] = hit[0]
    return out


def compare_dong(db, date_from=None, date_to=None, min_days: int = 7, metric: str = "visitor", tourist_only: bool = False) -> dict:
    """읍면동 단위 유동인구 vs 와이파이 — 표·상관·연결 정보. 도구와 검증 스크립트가 공용."""
    w, linked = wifi_by_dong(db, date_from, date_to, tourist_only=tourist_only)
    p = population_by_dong(db, date_from, date_to, metric)
    if w.empty or p.empty:
        return {"error": "와이파이 읍면동 연결 또는 유동인구 데이터가 비어 있습니다.", "linked_places": linked}
    nmap = _link_names(sorted(p["dong"].unique()), sorted(w["dong"].unique()))
    p = p.assign(dong=p["dong"].map(nmap)).dropna(subset=["dong"]).groupby(["dong", "date"], as_index=False)["population"].sum()
    m = w.merge(p, on=["dong", "date"], how="inner")
    if m.empty:
        return {"error": f"읍면동 이름이 연결되지 않았습니다 (유동인구 {len(nmap)}곳 연결). 유동인구 읍면동명 예: {', '.join(sorted(set(p['dong']))[:5])}", "linked_places": linked, "name_links": nmap}
    cross = m.groupby("dong", as_index=False).agg(population=("population", "mean"), wifi=("wifi", "mean"), n_places=("n_places", "max"), days=("date", "nunique"))
    cross = cross[cross["days"] >= min_days]
    cross["wifi_per_mille"] = (cross["wifi"] / cross["population"] * 1000).round(2)
    wk = m.groupby(["dong", "is_weekend"], as_index=False).agg(population=("population", "mean"), wifi=("wifi", "mean"))
    pv = wk.pivot(index="dong", columns="is_weekend", values=["population", "wifi"]).dropna()
    if len(pv):
        pv.columns = ["pop_wd", "pop_we", "wifi_wd", "wifi_we"]
        pv["pop_weekend_ratio"] = (pv["pop_we"] / pv["pop_wd"]).round(2)
        pv["wifi_weekend_ratio"] = (pv["wifi_we"] / pv["wifi_wd"]).round(2)
        cross = cross.merge(pv[["pop_weekend_ratio", "wifi_weekend_ratio"]], left_on="dong", right_index=True, how="left")
    per = []
    for nm, g in m.groupby("dong"):
        if g["date"].nunique() >= max(min_days, 10) and g["wifi"].std() > 0 and g["population"].std() > 0:
            per.append({"dong": nm, "days": int(g["date"].nunique()), "r": float(np.corrcoef(g["wifi"], g["population"])[0, 1]),
                        "rho": float(g[["wifi", "population"]].corr(method="spearman").iloc[0, 1])})
    per = pd.DataFrame(per)
    out = {"cross": cross.sort_values("population", ascending=False).reset_index(drop=True), "per": per, "linked_places": linked,
           "name_links": nmap, "n_dong": int(len(cross)), "days": int(m["date"].nunique()), "metric": metric, "tourist_only": tourist_only,
           "date_min": str(m["date"].min()), "date_max": str(m["date"].max())}
    if len(cross) >= 3:
        out["rho"] = float(cross[["population", "wifi"]].corr(method="spearman").iloc[0, 1])
        out["r_log"] = float(np.corrcoef(np.log1p(cross["population"]), np.log1p(cross["wifi"]))[0, 1])
        if "pop_weekend_ratio" in cross.columns:
            v = cross.dropna(subset=["pop_weekend_ratio", "wifi_weekend_ratio"])
            v = v[(abs(np.log(v["pop_weekend_ratio"])) > np.log(1.1)) | (abs(np.log(v["wifi_weekend_ratio"])) > np.log(1.1))]
            out["weekend_agree"] = float(((v["pop_weekend_ratio"] > 1) == (v["wifi_weekend_ratio"] > 1)).mean()) if len(v) else float("nan")
            out["weekend_n"] = int(len(v))
    return out


@register(
    "dong_population_compare",
    "읍면동별 통신사 유동인구(제주데이터허브 공개 API, 읍면동·일 집계)와 공공 와이파이 이용자를 같은 읍면동·같은 날짜로 대조한다. "
    "'읍면동별 유동인구', '어느 동네에 사람이 가장 많나', '유동인구 대비 와이파이 비율', '혼잡지수가 유동인구와 맞나' 질문에 사용. "
    "유동인구 데이터(dong_population)가 적재되어 있어야 한다.",
    {"type": "object", "properties": {
        **PERIOD_PARAMS,
        "top_n": {"type": "integer", "description": "표시할 읍면동 수(기본 20)"},
        "sort_by": {"type": "string", "enum": ["population", "wifi", "wifi_per_mille", "pop_weekend_ratio"], "description": "정렬 기준(기본 population)"},
        "metric": {"type": "string", "enum": ["visitor", "total"], "description": "유동인구 지표: visitor=방문자수(기본), total=거주+근무+방문 합계"},
        "places": {"type": "string", "enum": ["all", "tourist"], "description": "와이파이 집계 대상: all=정류소·충전소 제외 전체 AP 장소(기본), tourist=관광 범주 장소만"},
    }},
)
def dong_population_compare(db, date_from=None, date_to=None, top_n=20, sort_by="population", metric="visitor", places="all") -> ToolResult:
    if not db.has(DATASET):
        raise ValueError("읍면동 유동인구 데이터(dong_population)가 적재되지 않았습니다. `python scripts/fetch_hub.py --only dong_population` 으로 허브 공개 API에서 받아 넣으세요.")
    metric = metric if metric in METRIC_KO else "visitor"
    tourist = places == "tourist"
    r = compare_dong(db, date_from, date_to, metric=metric, tourist_only=tourist)
    if "error" in r:
        raise ValueError(r["error"])
    t = r["cross"].copy()
    if sort_by in t.columns:
        t = t.sort_values(sort_by, ascending=False)
    t = t.head(top_n).rename(columns={"dong": "읍면동", "population": "유동인구 일평균", "wifi": "와이파이 일평균", "n_places": "AP 장소 수", "days": "관측일",
                                      "wifi_per_mille": "와이파이/유동인구(‰)", "pop_weekend_ratio": "유동인구 주말/평일", "wifi_weekend_ratio": "와이파이 주말/평일"})
    for c in ("유동인구 일평균", "와이파이 일평균"):
        t[c] = t[c].round(0)
    facts = [f"기간 {r['date_min']}~{r['date_max']}, 연결된 읍면동 {r['n_dong']}곳 (와이파이 장소의 {r['linked_places']:.0%}가 읍면동에 연결), 유동인구 지표 = {METRIC_KO[metric]}, 와이파이 = {'관광 범주 장소만' if tourist else '전체 AP 장소(정류소·충전소 제외)'}"]
    top = r["cross"].iloc[0]
    facts.append(f"유동인구 1위 {top['dong']} 일평균 {top['population']:,.0f}명, 같은 동 와이파이 일평균 {top['wifi']:,.0f}명")
    if "rho" in r:
        facts.append(f"읍면동 간 순위 상관(유동인구 vs 와이파이) Spearman ρ={r['rho']:.2f}, log-log r={r['r_log']:.2f}")
    if len(r["per"]):
        facts.append(f"읍면동별 일별 상관 중앙값 r={r['per']['r'].median():.2f}, 양의 상관 {int((r['per']['r'] > 0).sum())}/{len(r['per'])}곳")
    if "weekend_agree" in r and r["weekend_n"]:
        facts.append(f"주말 쏠림 방향 일치 {r['weekend_agree']:.0%} ({r['weekend_n']}곳)")
    med = r["cross"]["wifi_per_mille"].median()
    facts.append(f"와이파이/유동인구 비율 중앙값 {med:.1f}‰ (읍면동 간 {r['cross']['wifi_per_mille'].min():.1f}~{r['cross']['wifi_per_mille'].max():.1f}‰)")
    caveats = ["유동인구는 통신사 가입자 기반 추정치(읍면동·일 집계), 와이파이는 AP가 있는 장소만 잽니다. 비율이 읍면동마다 다르므로 와이파이 기반 혼잡은 '순위·방향' 지표로만 해석하세요.",
               "법정동→행정동 변환표로 연결했으며 서귀동처럼 여러 행정동에 걸치는 법정동은 제외했습니다. 상관은 인과가 아닙니다."]
    chart = {"type": "bar", "x": "읍면동", "y": "유동인구 일평균", "title": f"읍면동별 유동인구({METRIC_KO[metric]}) 일평균 (허브 공개 API)"}
    return ToolResult("dong_population_compare", "읍면동별 유동인구 vs 와이파이 이용자", t.reset_index(drop=True), facts, caveats, chart=chart,
                      method=f"허브 읍면동 유동인구 {METRIC_KO[metric]}(성별·연령 합) ↔ 4-05 와이파이 이용자를 4-01 주소(법정동→행정동)로 읍면동 합산, 같은 날짜로 결합. 정류소·충전소 AP 제외.",
                      params={"date_from": date_from, "date_to": date_to, "top_n": top_n, "sort_by": sort_by, "metric": metric, "places": places},
                      datasets=[DATASET, "wifi_ap_usage", "wifi_ap"])
