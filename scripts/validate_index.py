"""혼잡지수(와이파이 이용자) 타당성 검증 — "와이파이 접속자 수가 실제 방문 규모를 따라가는가"를 독립 데이터로 확인한다.

  python scripts/validate_index.py                       # data/raw(또는 demo)로 검증 → docs/VALIDATION.md + docs/img/validation_*.png
  python scripts/validate_index.py --visitors FILE.csv   # (있으면) 허브 유료관광지 입장객·방문객 CSV 로 외부 검증까지
  python scripts/validate_index.py --radius 1.0          # 정류소 반경(km, 기본 1.0)

검증 A — 외부 기준(선택): 관광지별 입장객(일별 또는 월별)과 같은 장소의 와이파이 이용자를 대조.
  · 일별이면 장소별 시계열 상관(피어슨·스피어만), 월별이면 장소 간 횡단 상관(순위 상관).
  · 장소명 연결은 와이파이 장소명과 부분 일치(정규화) — 연결 결과 표를 함께 남긴다.
검증 B — 내부 교차(항상): 같은 장소 반경 r km 안 버스 정류소 승차(3-04, 카드 태그)와 와이파이 이용자(4-05)를 대조.
  · 두 데이터는 수집 주체·방식이 다르다(통신사 AP 로그 vs 티머니 카드). 둘이 같이 움직이면 "장소의 방문 규모"라는 공통 원인이 있다고 볼 근거가 된다.
  · (B1) 장소 간: 기간 평균 와이파이 이용자 vs 반경 내 일평균 승차 — 스피어만 순위 상관(대중교통 접근 장소만).
  · (B2) 날짜 간: 장소별로 일별 와이파이 vs 일별 승차 — 피어슨 상관의 분포(중앙값, 양의 상관 비율).
  · (B3) 요일 패턴: 장소별 주말/평일 비율이 두 데이터에서 같은 방향인지(부호 일치율).
한계: 버스 승차는 그 정류소를 쓰는 모든 통행(통근·환승)을 포함하므로 관광지 방문만 재지 않는다. 상관은 인과가 아니다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jeju_agent.data.loader import JejuDB  # noqa: E402
from jeju_agent.tools.base import df_to_md, haversine_km, wifi_users_agg  # noqa: E402
from jeju_agent.tools.congestion import TOURIST_CATS, _coords  # noqa: E402

OUT_MD = ROOT / "docs" / "VALIDATION.md"
IMG = ROOT / "docs" / "img"


def _norm(s: str) -> str:
    return re.sub(r"[\s\(\)\[\]·\-_]", "", str(s))


def wifi_daily(db) -> pd.DataFrame:
    u = db.table("wifi_ap_usage")
    d = u.groupby(["ap_place", "date", "is_weekend"], as_index=False).agg(wifi=wifi_users_agg(u))
    return d.merge(_coords(db), on="ap_place", how="left")


def bus_daily_near(db, places: pd.DataFrame, radius: float) -> pd.DataFrame:
    """장소별 반경 내 정류소 승차 합(일별)."""
    st = db.table("bus_station").drop_duplicates("bus_station_id")[["bus_station_id", "latitude", "longitude"]].dropna()
    b = db.table("bus_station_usage"); b = b[b["get_on_off_type"] == "승차"]
    bd = b.groupby(["bus_station_id", "date"], as_index=False)["user_count"].sum()
    rows = []
    lat_s, lon_s = st["latitude"].astype(float).values, st["longitude"].astype(float).values
    for p in places.itertuples():
        if pd.isna(p.lat) or pd.isna(p.lon):
            continue
        d = np.array([haversine_km(float(p.lat), float(p.lon), la, lo) for la, lo in zip(lat_s, lon_s)])
        ids = st["bus_station_id"].values[d <= radius]
        if len(ids) == 0:
            continue
        s = bd[bd["bus_station_id"].isin(ids)].groupby("date", as_index=False)["user_count"].sum()
        s["ap_place"], s["n_stations"] = p.ap_place, len(ids)
        rows.append(s.rename(columns={"user_count": "bus"}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["date", "bus", "ap_place", "n_stations"])


def internal_validation(db, radius: float, min_daily: int = 20):
    w = wifi_daily(db)
    w = w[w["category"].isin(TOURIST_CATS) & ~w["ap_place"].astype(str).str.match(r"^(버스정류소|전기차충전소)_")]
    places = w.groupby("ap_place", as_index=False).agg(lat=("lat", "first"), lon=("lon", "first"), wifi_mean=("wifi", "mean"), days=("date", "nunique"))
    places = places[(places["wifi_mean"] >= min_daily) & (places["days"] >= 14)]
    bus = bus_daily_near(db, places, radius)
    m = w.merge(bus, on=["ap_place", "date"], how="inner")
    if m.empty:
        return None
    # B1 장소 간
    cross = m.groupby("ap_place", as_index=False).agg(wifi_mean=("wifi", "mean"), bus_mean=("bus", "mean"), n_stations=("n_stations", "first"), days=("date", "nunique"))
    cross = cross[cross["days"] >= 14]
    b1_s = cross[["wifi_mean", "bus_mean"]].corr(method="spearman").iloc[0, 1]
    b1_p = np.corrcoef(np.log1p(cross["wifi_mean"]), np.log1p(cross["bus_mean"]))[0, 1]
    # B2 날짜 간 (장소별)
    per = []
    for nm, g in m.groupby("ap_place"):
        if g["date"].nunique() >= 14 and g["wifi"].std() > 0 and g["bus"].std() > 0:
            per.append({"ap_place": nm, "days": g["date"].nunique(), "r": float(np.corrcoef(g["wifi"], g["bus"])[0, 1]),
                        "rho": float(g[["wifi", "bus"]].corr(method="spearman").iloc[0, 1])})
    per = pd.DataFrame(per)
    # B3 요일 패턴 부호 일치
    wk = m.groupby(["ap_place", "is_weekend"], as_index=False).agg(wifi=("wifi", "mean"), bus=("bus", "mean"))
    pv = wk.pivot(index="ap_place", columns="is_weekend", values=["wifi", "bus"]).dropna()
    pv.columns = ["wifi_wd", "wifi_we", "bus_wd", "bus_we"]
    pv["wifi_ratio"], pv["bus_ratio"] = pv["wifi_we"] / pv["wifi_wd"], pv["bus_we"] / pv["bus_wd"]
    # 버스는 통근 때문에 거의 모든 곳에서 주말에 줄므로(전체 중앙값 < 1) 절대 방향 대신 '전체 중앙값 대비 주말 쏠림'의 방향을 비교한다
    med_w, med_b = float(pv["wifi_ratio"].median()), float(pv["bus_ratio"].median())
    pv["wifi_lean"], pv["bus_lean"] = pv["wifi_ratio"] / med_w, pv["bus_ratio"] / med_b
    pv = pv[(abs(np.log(pv["wifi_lean"])) > np.log(1.15)) | (abs(np.log(pv["bus_lean"])) > np.log(1.15))]   # 어느 한쪽이라도 ±15% 넘게 치우친 장소
    agree = float(((pv["wifi_lean"] > 1) == (pv["bus_lean"] > 1)).mean()) if len(pv) else float("nan")
    # 범주별 요약
    cats = m.drop_duplicates("ap_place").set_index("ap_place")["category"]
    rows = []
    for cat, names in cats.groupby(cats).groups.items():
        c_ = cross[cross["ap_place"].isin(names)]
        p_ = per[per["ap_place"].isin(names)] if len(per) else per
        v_ = pv[pv.index.isin(names)]
        rows.append({"범주": cat, "장소": len(c_), "B1 ρ": round(float(c_[["wifi_mean", "bus_mean"]].corr(method="spearman").iloc[0, 1]), 2) if len(c_) >= 5 else None,
                     "B2 r 중앙값": round(float(p_["r"].median()), 2) if len(p_) else None, "B2 양의 상관": f"{(p_['r'] > 0).mean():.0%}" if len(p_) else None,
                     "B3 일치": f"{((v_['wifi_lean'] > 1) == (v_['bus_lean'] > 1)).mean():.0%} ({len(v_)}곳)" if len(v_) >= 3 else None})
    by_cat = pd.DataFrame(rows).sort_values("장소", ascending=False)
    return {"cross": cross, "b1_spearman": b1_s, "b1_pearson_log": b1_p, "per": per, "weekend": pv, "agree": agree, "n_places": len(cross), "radius": radius,
            "by_cat": by_cat, "med_w": med_w, "med_b": med_b}


def external_validation(db, path: Path):
    """허브 입장객 CSV(장소명·날짜 또는 월·인원) ↔ 와이파이. 컬럼은 이름에서 추정한다."""
    df = JejuDB.read_csv(path)
    cols = {c: _norm(c) for c in df.columns}
    name_c = next((c for c, n in cols.items() if any(k in n for k in ("관광지명", "장소명", "관광지", "시설명", "명칭"))), None)
    date_c = next((c for c, n in cols.items() if any(k in n for k in ("일자", "날짜", "기준일", "date"))), None)
    month_c = next((c for c, n in cols.items() if any(k in n for k in ("년월", "월", "기준월", "month"))), None)
    val_c = next((c for c, n in cols.items() if any(k in n for k in ("입장객", "방문객", "인원", "이용객", "유동", "count"))), None)
    if not (name_c and val_c and (date_c or month_c)):
        return {"error": f"컬럼을 추정하지 못했습니다: {list(df.columns)} — 장소명·날짜(또는 월)·인원 컬럼이 필요합니다."}
    df["_v"] = pd.to_numeric(df[val_c].astype(str).str.replace(",", ""), errors="coerce")
    w = wifi_daily(db)
    names = sorted(w["ap_place"].dropna().unique())
    nmap = {}
    for n in df[name_c].dropna().unique():
        k = _norm(n)
        hit = [p for p in names if k and (k in _norm(p) or _norm(p) in k)]
        if hit:
            nmap[n] = sorted(hit, key=len)[0]
    df["ap_place"] = df[name_c].map(nmap)
    linked = df.dropna(subset=["ap_place"])
    out = {"file": path.name, "n_names": int(df[name_c].nunique()), "n_linked": int(linked[name_c].nunique()), "links": pd.DataFrame(sorted(nmap.items()), columns=["입장객 데이터 장소", "와이파이 장소"])}
    if date_c:
        linked = linked.assign(date=pd.to_datetime(linked[date_c].astype(str), errors="coerce").dt.strftime("%Y-%m-%d"))
        m = linked.groupby(["ap_place", "date"], as_index=False)["_v"].sum().merge(w, on=["ap_place", "date"])
        per = []
        for nm, g in m.groupby("ap_place"):
            if len(g) >= 10 and g["_v"].std() > 0 and g["wifi"].std() > 0:
                per.append({"ap_place": nm, "days": len(g), "r": float(np.corrcoef(g["_v"], g["wifi"])[0, 1]), "rho": float(g[["_v", "wifi"]].corr(method="spearman").iloc[0, 1])})
        out["per"] = pd.DataFrame(per)
        cross = m.groupby("ap_place", as_index=False).agg(visitors=("_v", "mean"), wifi=("wifi", "mean"))
    else:
        linked = linked.assign(ym=linked[month_c].astype(str).str.replace(r"\D", "", regex=True).str[:6])
        wm = w.assign(ym=w["date"].str[:7].str.replace("-", "")).groupby(["ap_place", "ym"], as_index=False)["wifi"].sum()
        m = linked.groupby(["ap_place", "ym"], as_index=False)["_v"].sum().merge(wm, on=["ap_place", "ym"])
        cross = m.groupby("ap_place", as_index=False).agg(visitors=("_v", "mean"), wifi=("wifi", "mean"))
    out["cross"] = cross
    if len(cross) >= 3:
        out["rho"] = float(cross[["visitors", "wifi"]].corr(method="spearman").iloc[0, 1])
        out["r_log"] = float(np.corrcoef(np.log1p(cross["visitors"]), np.log1p(cross["wifi"]))[0, 1])
        cross["wifi_share"] = (cross["wifi"] / cross["visitors"]).round(3)
    return out


def plots(internal, external):
    try:
        from jeju_agent.charts import _setup   # 한글 폰트 설정 공용
        plt = _setup()
    except Exception:  # noqa: BLE001
        return []
    IMG.mkdir(parents=True, exist_ok=True)
    made = []
    if internal:
        c = internal["cross"]
        fig, ax = plt.subplots(1, 2, figsize=(10, 4))
        ax[0].scatter(c["bus_mean"], c["wifi_mean"], s=18, alpha=.7)
        ax[0].set_xscale("log"); ax[0].set_yscale("log")
        ax[0].set_xlabel(f"반경 {internal['radius']}km 정류소 일평균 승차(3-04)"); ax[0].set_ylabel("와이파이 일평균 이용자(4-05)")
        ax[0].set_title(f"장소 간 (n={len(c)}, Spearman ρ={internal['b1_spearman']:.2f})")
        if len(internal["per"]):
            ax[1].hist(internal["per"]["r"], bins=15, color="#d9822b", alpha=.8)
            ax[1].axvline(0, color="k", lw=.8); ax[1].set_xlabel("장소별 일별 상관 r (와이파이 vs 승차)"); ax[1].set_ylabel("장소 수")
            ax[1].set_title(f"날짜 간 (중앙값 r={internal['per']['r'].median():.2f})")
        fig.tight_layout(); p = IMG / "validation_internal.png"; fig.savefig(p, dpi=130); plt.close(fig); made.append(p)
    if external and "cross" in external and len(external["cross"]) >= 3:
        c = external["cross"]
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.scatter(c["visitors"], c["wifi"], s=22)
        for r in c.itertuples():
            ax.annotate(r.ap_place, (r.visitors, r.wifi), fontsize=7)
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("입장객(외부 기준)"); ax.set_ylabel("와이파이 이용자")
        ax.set_title(f"외부 검증 (n={len(c)}, ρ={external.get('rho', float('nan')):.2f})")
        fig.tight_layout(); p = IMG / "validation_external.png"; fig.savefig(p, dpi=130); plt.close(fig); made.append(p)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visitors", help="허브 유료관광지 입장객/방문객 CSV")
    ap.add_argument("--radius", type=float, default=1.0)
    a = ap.parse_args()
    db = JejuDB(); db.load_all()
    prov = db.summary()
    period = prov[prov.key == "wifi_ap_usage"]["period"].iloc[0]
    internal = internal_validation(db, a.radius)
    external = external_validation(db, Path(a.visitors)) if a.visitors else None
    imgs = plots(internal, external)

    md = ["# 혼잡지수 타당성 검증", "",
          f"데이터: 와이파이 4-05 {period}, 버스 정류소 3-04 {prov[prov.key == 'bus_station_usage']['period'].iloc[0]} ({'동봉 축약본' if db._demo_used else 'data/raw'})",
          "", "**검증 질문** — 이 서비스의 모든 혼잡 결과는 \"공공 와이파이 접속자 수가 장소의 방문 규모를 따라간다\"는 가정 위에 있다. 이 가정을 수집 주체·방식이 다른 독립 데이터로 확인한다.", ""]
    if external:
        md += ["## A. 외부 기준 검증 — 관광지 입장객 vs 와이파이 이용자", ""]
        if "error" in external:
            md += [f"- ⚠️ {external['error']}", ""]
        else:
            md += [f"- 파일 `{external['file']}`: 장소 {external['n_names']}곳 중 와이파이 장소와 이름이 연결된 {external['n_linked']}곳",
                   (f"- 장소 간 순위 상관 Spearman ρ = **{external['rho']:.2f}**, log-log Pearson r = {external['r_log']:.2f} (n={len(external['cross'])})" if "rho" in external else "- 연결 장소가 3곳 미만이라 상관을 내지 못함"),
                   ]
            if "per" in external and len(external["per"]):
                md += [f"- 장소별 일별 상관(10일 이상): 중앙값 r = {external['per']['r'].median():.2f}, 양의 상관 {int((external['per']['r'] > 0).sum())}/{len(external['per'])}곳"]
            md += ["", "연결 표:", "", df_to_md(external["links"], 60), ""]
            if "cross" in external and len(external["cross"]):
                md += ["장소별 평균(와이파이 ÷ 입장객 = 표본 비율):", "", df_to_md(external["cross"].round(1), 40), ""]
            if (IMG / "validation_external.png").exists():
                md += ["![외부 검증](img/validation_external.png)", ""]
    else:
        md += ["## A. 외부 기준 검증 — (미수행)", "", "허브에서 관광지별 입장객·방문객 데이터를 확보하면 `python scripts/validate_index.py --visitors FILE.csv` 로 이 절이 채워진다.", ""]
    md += ["## B. 내부 교차 검증 — 같은 장소 반경 내 버스 승차(티머니) vs 와이파이 이용자", ""]
    if internal is None:
        md += ["- 좌표가 있는 관광지 장소와 반경 내 정류소를 찾지 못해 수행하지 못함.", ""]
    else:
        per = internal["per"]
        md += [f"- 대상: 관광 범주(관광지·해변·시장·올레·숲길·공원 등) 장소 중 일평균 20명·관측 14일 이상이고 반경 {internal['radius']}km 안에 정류소가 있는 **{internal['n_places']}곳**",
               f"- **B1 장소 간**: 기간 평균 와이파이 이용자 vs 반경 내 일평균 승차 — Spearman ρ = **{internal['b1_spearman']:.2f}**, log-log Pearson r = {internal['b1_pearson_log']:.2f}",
               (f"- **B2 날짜 간**: 장소별 일별 상관(14일 이상 {len(per)}곳) — 중앙값 r = **{per['r'].median():.2f}**, 양의 상관 {int((per['r'] > 0).sum())}곳({(per['r'] > 0).mean():.0%}), r ≥ 0.3 인 곳 {int((per['r'] >= 0.3).sum())}곳" if len(per) else "- B2: 장소별 일별 상관을 낼 만한 장소가 없음"),
               (f"- **B3 요일 패턴**: 주말/평일 비율의 전체 중앙값은 와이파이 {internal['med_w']:.2f}, 버스 {internal['med_b']:.2f}(버스는 통근 때문에 주말에 줄어듦). 그 중앙값 대비 ±15% 넘게 치우친 {len(internal['weekend'])}곳에서 두 데이터의 쏠림 방향(주말형/평일형)이 일치한 비율 **{internal['agree']:.0%}**" if len(internal["weekend"]) else "- B3: 요일 쏠림이 뚜렷한 장소 없음"),
               "", "범주별:", "", df_to_md(internal["by_cat"].fillna("-"), 20),
               "", "읽는 법: B1이 높으면 '붐비는 장소 순위'가, B2가 높으면 '언제 붐비나(일별 추이)'가, B3가 높으면 '주말 쏠림' 결론이 독립 데이터로 뒷받침된다. "
               "버스 승차는 통근·환승 통행을 포함하므로 완전한 기준은 아니며, 상관은 인과가 아니다.", ""]
        if (IMG / "validation_internal.png").exists():
            md += ["![내부 교차 검증](img/validation_internal.png)", ""]
        md += ["장소 간 상위 20곳 (와이파이 일평균 순):", "", df_to_md(internal["cross"].sort_values("wifi_mean", ascending=False).head(20).round(1), 20), ""]
        if len(per):
            md += ["장소별 일별 상관 상위·하위 5곳:", "", df_to_md(pd.concat([per.sort_values("r", ascending=False).head(5), per.sort_values("r").head(5)]).round(2), 10), ""]
    md += ["## 결론·한계", ""]
    if internal is not None and len(internal["per"]):
        per = internal["per"]; bc = internal["by_cat"].set_index("범주")
        strong = [c for c in ("관광지", "해변") if c in bc.index and bc.loc[c, "B1 ρ"] is not None and float(bc.loc[c, "B1 ρ"]) >= 0.3]
        weak = [c for c in ("전통시장", "테마거리", "올레코스") if c in bc.index and bc.loc[c, "B1 ρ"] is not None and float(bc.loc[c, "B1 ρ"]) < 0.2]
        md += [f"- **요약(내부 교차)**: 장소의 {(per['r'] > 0).mean():.0%}에서 와이파이와 인근 버스 승차가 같은 방향으로 움직이고(일별 상관 중앙값 {per['r'].median():.2f}), 요일 쏠림 방향은 {internal['agree']:.0%} 일치한다. "
               + (f"장소 간 순위는 {'·'.join(strong)}(ρ {', '.join(str(bc.loc[c, 'B1 ρ']) for c in strong)})에서 ρ 0.3대로 따라가지만(표본 20곳 안팎이라 확정적이지 않음) " if strong else "장소 간 순위 상관은 전체적으로 약하며 ")
               + (f"{'·'.join(weak)}처럼 도심 상권·통근 동선과 겹치는 범주에서는 약하다(버스 승차가 통근을 포함). " if weak else "")
               + "→ 혼잡지수는 '방향·순위' 수준의 상대 지표로 쓰고, 절대 규모나 도심 상권의 세밀한 순위에는 쓰지 않는다는 서비스의 해석 원칙을 데이터가 뒷받침한다."]
    md += [
           "- 검증은 '와이파이 이용자 = 방문객 수'가 아니라 **'와이파이 이용자가 방문 규모와 같은 방향으로 움직인다'**를 확인하는 것이다. 서비스의 모든 답변은 절대 방문객 수가 아닌 상대 지표(혼잡지수 = 평소 평일 대비 배수)로만 결과를 말한다.",
           "- 와이파이 표본은 AP 수·설치 위치·실내외·이용자 연령(젊은 층 과대)에 따라 편향될 수 있다. 외부 기준(입장객)이 있는 장소에서 표본 비율(와이파이 ÷ 입장객)이 장소마다 다르면 장소 간 비교는 '순위' 수준으로만 해석해야 한다.",
           "- 이 문서는 `python scripts/validate_index.py` 로 재생성된다(데이터가 바뀌면 숫자도 바뀜).", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md[:30]))
    print(f"\n저장: {OUT_MD}" + (f", {', '.join(str(p.relative_to(ROOT)) for p in imgs)}" if imgs else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
