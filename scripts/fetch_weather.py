"""날씨(5-01·5-02) 실데이터 수집·변환기 → data/raw/weather_*.csv

제주데이터허브 공개 API에는 날씨가 없다. 강우 영향(weather_effect) 기능을 실데이터로 돌리려면 아래 셋 중 하나로 받는다.
로더는 명세서 5-02(일일 날씨 정보)·5-01(날씨 관측소 정보) 컬럼명으로 저장된 CSV를 그대로 읽는다.

  A. 기상청 기상자료개방포털 CSV 변환 (권장 — 관측값)
     https://data.kma.go.kr → 데이터 → 기상관측 → 지상 → 종관기상관측(ASOS) → 자료형태 '일 자료'
     → 지점 제주(184)·고산(185)·성산(188)·서귀포(189) 선택 → 기간 → CSV 다운로드 (로그인 필요, 무료)
       python scripts/fetch_weather.py --kma-csv ~/Downloads/OBS_ASOS_DD_20251107.csv
     ※ 다운로드한 CSV를 변환 없이 data/raw/ 에 '일일 날씨 정보_기상청.csv' 이름으로 넣어도 별칭 규칙으로 읽힌다.

  B. Open-Meteo 과거 기상 API (키 없음, 무료, 비상업·연구용) — ERA5 재분석 격자값(관측소 실측이 아님)
       python scripts/fetch_weather.py --open-meteo --from 2025-09-01 --to 2025-09-30
     4개 관측소 좌표의 격자값을 받아 같은 관측소 코드로 저장한다. 출처: https://open-meteo.com (CC BY 4.0)

  C. 기상청 API허브 (키 필요: https://apihub.kma.go.kr 가입 → 인증키, .env 에 KMA_API_KEY)
       python scripts/fetch_weather.py --kma-api --from 2025-09-01 --to 2025-09-30
     지상 일자료(kma_sfcdd) 텍스트 응답을 변환한다. 응답 헤더(#  TM  STN ...)의 열 이름으로 매핑하므로
     헤더가 바뀌면 --debug 로 원문을 확인한다.

저장
  data/raw/weather_daily/일일 날씨 정보_<출처>_<YYYYMM>.csv      (5-02)
  data/raw/weather_station/날씨 관측소 정보_<출처>.csv            (5-01, config/weather_stations_jeju.csv 기반)
저장 후: python scripts/check_data.py  → weather_daily 가 source=raw 로 보이면 끝.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from jeju_agent.config import CONFIG_DIR, RAW_DIR  # noqa: E402

STATIONS_CSV = CONFIG_DIR / "weather_stations_jeju.csv"
DAILY_COLS = ["base_date", "pt_year", "pt_month", "observatory_code", "average_temperature", "lowest_temperature",
              "lowest_temperature_time", "highest_temperature", "highest_temperature_time", "daily_rainfall",
              "maximum_wind_speed", "maximum_wind_speed_time", "average_wind_speed", "maximum_wind_speed_direction"]
# 기상자료개방포털 ASOS 일자료 헤더 → 5-02 (헤더의 단위·공백은 무시하고 '포함' 비교)
KMA_CSV_MAP = [("일시", "base_date"), ("지점명", "_name"), ("지점", "observatory_code"), ("평균기온", "average_temperature"),
               ("최저기온시각", "lowest_temperature_time"), ("최저기온", "lowest_temperature"),
               ("최고기온시각", "highest_temperature_time"), ("최고기온", "highest_temperature"),
               ("일강수량", "daily_rainfall"), ("최대순간풍속풍향", "maximum_wind_speed_direction"),
               ("최대순간풍속시각", "maximum_wind_speed_time"), ("최대순간풍속", "maximum_wind_speed"), ("평균풍속", "average_wind_speed")]
# 기상청 API허브 kma_sfcdd 열 이름 → 5-02
KMA_API_MAP = {"TM": "base_date", "STN": "observatory_code", "TA_AVG": "average_temperature", "TA_MIN": "lowest_temperature",
               "TA_MIN_TM": "lowest_temperature_time", "TA_MAX": "highest_temperature", "TA_MAX_TM": "highest_temperature_time",
               "RN_DAY": "daily_rainfall", "WS_INS": "maximum_wind_speed", "WS_INS_TM": "maximum_wind_speed_time",
               "WS_AVG": "average_wind_speed", "WD_INS": "maximum_wind_speed_direction"}


def _norm(s: str) -> str:
    import re
    return re.sub(r"[\s_\-()（）,./·°℃%]", "", str(s)).lower()


def stations() -> list[dict]:
    with open(STATIONS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def finish_row(r: dict) -> dict:
    """base_date 를 YYYYMMDD 로, 년·월 파생, 강수 결측(기상청은 무강수일을 빈칸으로 둠)은 0."""
    d = "".join(ch for ch in str(r.get("base_date", "")) if ch.isdigit())[:8]
    r["base_date"], r["pt_year"], r["pt_month"] = d, d[:4], d[4:6]
    if r.get("daily_rainfall") in (None, "", "nan"):
        r["daily_rainfall"] = "0.0"
    r["observatory_code"] = str(r.get("observatory_code", "")).strip()
    return r


def save(rows: list[dict], tag: str) -> None:
    if not rows:
        print("저장할 행이 없습니다.")
        return
    by_ym: dict[str, list[dict]] = {}
    for r in rows:
        by_ym.setdefault(r["base_date"][:6], []).append(r)
    for ym, rs in sorted(by_ym.items()):
        p = RAW_DIR / "weather_daily" / f"일일 날씨 정보_{tag}_{ym}.csv"
        write_csv(p, rs, DAILY_COLS)
        print(f"  {len(rs):,}행 → {p.relative_to(ROOT)}")
    codes = {r["observatory_code"] for r in rows}
    st = [s for s in stations() if s["observatory_code"] in codes] or stations()
    p = RAW_DIR / "weather_station" / f"날씨 관측소 정보_{tag}.csv"
    write_csv(p, st, list(st[0].keys()))
    print(f"  관측소 {len(st)}개 → {p.relative_to(ROOT)}")
    dates = sorted({r["base_date"] for r in rows})
    rain = sum(1 for r in rows if float(r["daily_rainfall"] or 0) >= 1.0)
    print(f"기간 {dates[0]}~{dates[-1]} · 관측소 {sorted(codes)} · 강우(≥1mm) 관측소-일 {rain}/{len(rows)}")
    print("다음: python scripts/check_data.py  (weather_daily 가 source=raw 인지 확인)")


# ---------- A. 기상자료개방포털 CSV ----------
def from_kma_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SystemExit("인코딩을 알 수 없습니다 (utf-8/cp949 아님)")
    rd = csv.DictReader(io.StringIO(text))
    headers = rd.fieldnames or []
    colmap: dict[str, str] = {}
    used: set[str] = set()
    for key, std in KMA_CSV_MAP:          # 긴 키(…시각, …풍향)부터 매칭되도록 순서를 둔 목록
        h = next((h for h in headers if h not in used and _norm(key) in _norm(h)), None)
        if h:
            colmap[h] = std
            used.add(h)
    if "observatory_code" not in colmap.values() or "base_date" not in colmap.values() or "daily_rainfall" not in colmap.values():
        raise SystemExit(f"필수 열(지점·일시·일강수량)을 찾지 못했습니다. 헤더: {headers}")
    rows = [finish_row({colmap[h]: v for h, v in row.items() if h in colmap}) for row in rd]
    rows = [r for r in rows if r["base_date"]]
    print(f"[기상청 CSV] {path.name}: {len(rows):,}행, 열 매핑 {len(colmap)}개")
    return rows


# ---------- B. Open-Meteo ----------
def from_open_meteo(d0: date, d1: date) -> list[dict]:
    import requests
    rows: list[dict] = []
    for s in stations():
        p = {"latitude": s["latitude"], "longitude": s["longitude"], "start_date": d0.isoformat(), "end_date": d1.isoformat(),
             "daily": "precipitation_sum,temperature_2m_mean,temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant",
             "timezone": "Asia/Seoul", "wind_speed_unit": "ms"}
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", params=p, timeout=60)
        if not r.ok:
            raise SystemExit(f"Open-Meteo HTTP {r.status_code}: {r.text[:200]}")
        d = r.json().get("daily", {})
        n = 0
        for i, day in enumerate(d.get("time", [])):
            def g(k):
                v = (d.get(k) or [None])[i] if i < len(d.get(k) or []) else None
                return "" if v is None else v
            rows.append(finish_row({"base_date": day, "observatory_code": s["observatory_code"], "average_temperature": g("temperature_2m_mean"),
                                    "lowest_temperature": g("temperature_2m_min"), "highest_temperature": g("temperature_2m_max"),
                                    "daily_rainfall": g("precipitation_sum"), "maximum_wind_speed": g("wind_gusts_10m_max"),
                                    "average_wind_speed": g("wind_speed_10m_max"), "maximum_wind_speed_direction": g("wind_direction_10m_dominant")}))
            n += 1
        print(f"[Open-Meteo] {s['observatory_name']}({s['observatory_code']}) {n}일")
    return rows


# ---------- C. 기상청 API허브 ----------
def from_kma_api(d0: date, d1: date, debug: bool = False) -> list[dict]:
    import requests
    key = os.getenv("KMA_API_KEY", "")
    if not key:
        raise SystemExit(".env 에 KMA_API_KEY 가 없습니다 (https://apihub.kma.go.kr)")
    rows: list[dict] = []
    for s in stations():
        p = {"tm1": d0.strftime("%Y%m%d"), "tm2": d1.strftime("%Y%m%d"), "stn": s["observatory_code"], "help": 1, "authKey": key}
        r = requests.get("https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd.php", params=p, timeout=60)
        if not r.ok:
            raise SystemExit(f"API허브 HTTP {r.status_code}: {r.text[:200]}")
        text = r.text
        if debug:
            print(text[:1500])
        header: list[str] = []
        n = 0
        for line in text.splitlines():
            if line.startswith("#"):
                toks = line.lstrip("#").split()
                if "TM" in toks and "STN" in toks:
                    header = toks
                continue
            toks = line.replace(",", " ").split()
            if not header or len(toks) < len(header) - 2 or not toks[0].isdigit():
                continue
            rec = dict(zip(header, toks))
            row = {KMA_API_MAP[k]: v for k, v in rec.items() if k in KMA_API_MAP}
            for k, v in list(row.items()):   # -9, -99.0 류는 결측
                if v.startswith("-9") and k not in ("average_temperature", "lowest_temperature", "highest_temperature"):
                    row[k] = ""
            rows.append(finish_row(row))
            n += 1
        print(f"[API허브] {s['observatory_name']}({s['observatory_code']}) {n}일" + ("" if n else " — 응답 형식 확인 필요(--debug)"))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--kma-csv", type=Path, help="기상자료개방포털 ASOS 일자료 CSV 경로")
    g.add_argument("--open-meteo", action="store_true", help="Open-Meteo 과거 기상 API(키 없음)")
    g.add_argument("--kma-api", action="store_true", help="기상청 API허브(KMA_API_KEY)")
    ap.add_argument("--from", dest="d0", default="2025-09-01")
    ap.add_argument("--to", dest="d1", default=(date.today() - timedelta(days=7)).isoformat())
    ap.add_argument("--tag", default="", help="저장 파일 꼬리표(기본: 출처명)")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    d0, d1 = datetime.strptime(a.d0, "%Y-%m-%d").date(), datetime.strptime(a.d1, "%Y-%m-%d").date()
    if a.kma_csv:
        rows, tag = from_kma_csv(a.kma_csv), a.tag or "기상청"
    elif a.open_meteo:
        rows, tag = from_open_meteo(d0, d1), a.tag or "OpenMeteo"
    else:
        rows, tag = from_kma_api(d0, d1, a.debug), a.tag or "기상청API"
    save(rows, tag)


if __name__ == "__main__":
    main()
