"""제주데이터허브 공개 API → data/raw/*.csv 수집기.

제주빅데이터센터의 원본 4-05·3-04 CSV는 센터 분석실에서만 받을 수 있다("데이터검색" 메뉴는 분석실 전용).
대신 허브의 공개 '데이터' 메뉴에 같은 계열(제공처: 제주빅데이터센터)의 API가 있어 집에서도 수집할 수 있다.
이 스크립트는 그 API를 기간 단위로 호출해 명세서 컬럼명으로 바꿔 CSV로 저장한다. 저장 후 로더가 그대로 읽는다.

준비
  1. https://jejudatahub.net 로그인 → 마이페이지 → 프로젝트 → 프로젝트 키 복사
  2. .env 에  JEJUHUB_PROJECT_KEY=여기에키  추가 (절대 커밋 금지)
실행
  python scripts/fetch_hub.py --from 2025-07-01 --to 2025-09-30            # 기본: 와이파이·버스 정류소·노선·정류소정보·노선정보·AP정보
  python scripts/fetch_hub.py --from 2025-09-01 --to 2025-09-30 --only wifi,bus_station
  python scripts/fetch_hub.py --status                                     # 남은 쿼터 추정·저장된 기간 확인
쿼터: 데이터셋당 하루 1,000회(허브 표기). 페이지당 최대 100행.
  와이파이 일일(장소 ~1,400개) ≈ 14회/일, 정류소 일일(~6,400행) ≈ 64회/일, 노선 일일 ≈ 20회/일.
  → 한 번에 약 12일치 정류소 데이터까지 안전. 더 긴 기간은 날짜를 나눠 며칠에 걸쳐 실행하면 이어서 저장된다(이미 받은 날짜는 건너뜀).

컬럼 대응 (API → 명세서)
  와이파이 AP그룹별 일일 사용량  : apGroupName→ap_place, baseDate→base_date, userCount→user_count  (국적·방문유형·user_id 없음 → 로더가 기본값/집계 모드로 처리)
  일일 정류소별 버스 이용자 정보 : stationId→bus_station_id, stationName→bus_station_name, moveType→get_on_off_type, priceType→user_category, payType→pay_type
  일일 노선별 버스 이용자 정보   : routeName→bus_route_number, routeSubName→bus_route_name
  버스 정류소 기본 정보          : stationId, stationName, latitude, longitude
  버스 노선 기본 정보            : routeId→bus_route_id, routeNumber→bus_route_number, routeLength→bus_route_length, stationCount→bus_station_count
  제주 공공와이파이 AP 정보      : apGroupName→ap_group_name, category, latitude, longitude (4-01)

추가 데이터셋(허브 데이터목록 20261105 기준). API 토큰은 허브 상세 페이지(공개)의 요청 URL에 적힌 값이 기본으로 들어 있고 .env 로 바꿀 수 있다.
  weather          일별 기상 정보 (허브 579)           searchDate=YYYYMMDD · observatoryName, averageTemperature, dailyRainfall, …  → data/raw/weather_daily/  (5-02, 기상청 AWS 관측값)
  weather_station  기상관측소 정보 (허브 578)          observatoryCode, observatoryName, latitude, longitude …                      → data/raw/weather_station/ (5-01)
  dong_population  읍면동 단위 내국인 유동인구 (허브 583) startDate,endDate · baseDate, city, emd, gender, ageGroup, resdPop, workPop, visitPop → data/raw/dong_population/
  응답 키는 그대로 저장하고 로더 별칭(config/column_aliases.yaml)이 명세서 컬럼으로 맞춘다. 일별 기상은 관측소 코드가 없어 관측소 정보로 observatoryCode 를 붙인다.
  날짜 파라미터 이름을 바꿔야 하면 JEJUHUB_DATEPARAM_<이름>=searchDate 또는 =startDate,endDate.
  확인: python scripts/fetch_hub.py --probe dong_population --to 2025-09-01
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from jeju_agent.config import RAW_DIR  # noqa: E402

BASE = "https://open.jejudatahub.net/api/proxy/{api}/{key}"
PAGE = 100

# api 식별자는 허브 데이터 상세 페이지 '요청 URL'의 /proxy/ 뒤 토큰
DATASETS = {
    "wifi": {
        "api": "1aa9D5DabtDt1at935b1tt5t1D95t5at", "hub_id": 593, "title": "와이파이 AP그룹별 일일 사용량",
        "file": "wifi_ap_usage/와이파이 AP별 이용 현황_허브API_{ym}.csv", "mode": "daily", "date_param": ("searchDate",),
        "map": {"baseDate": "base_date", "apGroupName": "ap_place", "userCount": "user_count", "uploadBytes": "upload", "downloadBytes": "download", "sessionTime": "session_time"},
    },
    "bus_station": {
        "api": "1b1ta1a1ba6t6a1ttt3bD6b1tb1t1D3t", "hub_id": 613, "title": "일일 정류소별 버스 이용자 정보",
        "file": "bus_station_usage/버스 정류소별 이용 현황_허브API_{ym}.csv", "mode": "daily", "date_param": ("startDate", "endDate"),
        "map": {"baseDate": "base_date", "stationId": "bus_station_id", "stationName": "bus_station_name", "moveType": "get_on_off_type",
                "priceType": "user_category", "payType": "pay_type", "userCount": "user_count"},
    },
    "bus_route": {
        "api": "1t4tbb16b4t1t11tt11tDt1ab6114a1t", "hub_id": 614, "title": "일일 노선별 버스 이용자 정보",
        "file": "bus_route_usage/버스 노선별 이용 현황_허브API_{ym}.csv", "mode": "daily", "date_param": ("startDate", "endDate"),
        "map": {"baseDate": "base_date", "routeName": "bus_route_number", "routeSubName": "bus_route_name", "moveType": "get_on_off_type",
                "priceType": "user_category", "payType": "pay_type", "userCount": "user_count"},
    },
    "station_info": {
        "api": "DD11ab6a6t11D16baaa1a2tD26ata161", "hub_id": 612, "title": "버스 정류소 기본 정보",
        "file": "버스 정류소 정보_허브API.csv", "mode": "static", "date_param": (),
        "map": {"stationId": "bus_station_id", "stationName": "bus_station_name", "stationType": "station_type", "stationAddress": "address",
                "direction": "direction", "longitude": "longitude", "latitude": "latitude"},
    },
    "route_info": {
        "api": "aaatD6D1atta1611at1t6a1b6at1b11a", "hub_id": 611, "title": "버스 노선 기본 정보",
        "file": "버스 노선 정보_허브API.csv", "mode": "static", "date_param": (),
        "map": {"routeId": "bus_route_id", "routeName": "bus_route_name", "routeNumber": "bus_route_number", "stationCount": "bus_station_count",
                "routeLength": "bus_route_length", "startStationName": "start_station", "endStationName": "end_station"},
    },
    "ap_info": {
        "api": "Dtb18ta1btbD1Da1a81aaDttab6tDabb", "hub_id": 618, "title": "제주 공공와이파이 AP 정보",
        "file": "와이파이 AP 현황_허브API.csv", "mode": "snapshot", "date_param": ("baseDate",),
        "map": {"baseDate": "base_date", "apGroupName": "ap_group_name", "installLocationDetail": "install_location_detail", "category": "category",
                "categoryDetail": "category_detail", "addressDong": "address_dong", "addressDetail": "address_detail", "latitude": "latitude", "longitude": "longitude"},
    },
}

def _env_api(name: str) -> str | None:
    return os.getenv(f"JEJUHUB_API_{name.upper()}", "").strip() or None


def _env_dateparam(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    v = os.getenv(f"JEJUHUB_DATEPARAM_{name.upper()}", "").strip()
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else default


# 토큰이 .env 에 있을 때만 활성화되는 추가 데이터셋 (응답 키 그대로 저장: map=None)
EXTRA_DATASETS = {
    # API 토큰은 허브 상세 페이지(로그인 없이 공개)의 요청 URL에 적힌 값. .env 로 바꿀 수 있다.
    "weather": {   # https://jejudatahub.net/data/view/data/579 — 관측소명(observatoryName)만 있고 코드가 없어 fetch 시 관측소 정보로 코드를 붙인다
        "api": _env_api("weather") or "1aD5taat1attaa51Db1511b51ab9Da19", "hub_id": 579, "title": "일별 기상 정보", "env": "JEJUHUB_API_WEATHER",
        "file": "weather_daily/일일 날씨 정보_허브API_{ym}.csv", "mode": "daily", "date_param": _env_dateparam("weather", ("searchDate",)), "map": None,
    },
    "weather_station": {   # https://jejudatahub.net/data/view/data/578 — observatoryCode, observatoryName, latitude/longitude(예시는 뒤바뀜, 로더가 보정)
        "api": _env_api("weather_station") or "t1bab75a555a15715btaa7a5abaa1aab", "hub_id": 578, "title": "기상관측소 정보", "env": "JEJUHUB_API_WEATHER_STATION",
        "file": "weather_station/날씨 관측소 정보_허브API.csv", "mode": "static", "date_param": (), "map": None,
    },
    "dong_population": {   # https://jejudatahub.net/data/view/data/POPULATION/583 — baseDate, city, emd, gender, ageGroup, resdPop, workPop, visitPop
        "api": _env_api("dong_population") or "Daaa1t3at3tt8a8DD3t55538t35Dab1t", "hub_id": 583, "title": "읍면동 단위 내국인 유동인구", "env": "JEJUHUB_API_DONG_POPULATION",
        "file": "dong_population/읍면동 단위 내국인 유동인구_허브API_{ym}.csv", "mode": "daily", "date_param": _env_dateparam("dong_population", ("startDate", "endDate")), "map": None,
    },
}
DATASETS.update(EXTRA_DATASETS)
DEFAULT_ONLY = ["wifi", "bus_station", "bus_route", "station_info", "route_info", "ap_info"]
STATE = RAW_DIR / ".hub_fetch_state.json"


def _state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def _save_state(s: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


class Quota(Exception):
    pass


def fetch_all(key: str, ds: dict, params: dict, calls: list[int]) -> list[dict]:
    """페이지를 끝까지 돌며 행을 모은다. 429/쿼터 초과 시 Quota 예외."""
    url = BASE.format(api=ds["api"], key=key)
    rows, page = [], 1
    while True:
        p = {**params, "limit": PAGE, "number": page}
        for attempt in range(5):
            try:
                r = requests.get(url, params=p, timeout=60)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 4:
                    raise
                time.sleep(min(3 * 2 ** attempt, 30))
                continue
            calls[0] += 1
            if r.status_code == 429 or "quota" in r.text.lower() or "쿼터" in r.text:
                raise Quota(r.text[:200])
            if r.ok:
                break
            time.sleep(min(3 * 2 ** attempt, 30))
        else:
            raise RuntimeError(f"{ds['title']} HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        rows += j.get("data", [])
        if not j.get("hasMore") or not j.get("data"):
            return rows
        page += 1


def to_rows(ds: dict, data: list[dict]) -> list[dict]:
    m = ds.get("map")
    if not m:   # 응답 키 그대로 (로더의 별칭 규칙이 명세서 컬럼으로 맞춘다)
        return [dict(d) for d in data]
    return [{m[k]: v for k, v in d.items() if k in m} for d in data]


def probe(key: str, name: str, d1: date) -> int:
    """응답 키와 첫 행만 출력 — 추가 데이터셋의 컬럼·날짜 파라미터를 확인할 때."""
    ds = DATASETS[name]
    if not ds.get("api"):
        print(f"{name}: 토큰이 없습니다. .env 에 {ds.get('env', 'JEJUHUB_API_' + name.upper())}=<허브 상세 페이지 요청 URL의 /proxy/ 뒤 토큰> 추가")
        return 1
    dp = ds["date_param"]
    ymd = d1.strftime("%Y%m%d")
    params = {} if not dp else ({dp[0]: ymd} if len(dp) == 1 else {dp[0]: ymd, dp[1]: ymd})
    url = BASE.format(api=ds["api"], key=key)
    r = requests.get(url, params={**params, "limit": 3, "number": 1}, timeout=60)
    print(f"[{name}] {ds['title']} (허브 #{ds['hub_id']}) HTTP {r.status_code}  params={params}")
    try:
        j = r.json()
    except ValueError:
        print(r.text[:500]); return 1
    data = j.get("data") or []
    print(f"  totCnt={j.get('totCnt')} hasMore={j.get('hasMore')} rows={len(data)}")
    if data:
        print("  keys:", ", ".join(data[0].keys()))
        print("  row1:", json.dumps(data[0], ensure_ascii=False)[:600])
    else:
        print("  응답 원문:", json.dumps(j, ensure_ascii=False)[:600])
        print("  → 날짜 파라미터 이름이 다르면 .env 에 JEJUHUB_DATEPARAM_" + name.upper() + "=searchDate 처럼 지정")
    return 0


def write_csv(path: Path, rows: list[dict], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    cols = list(rows[0].keys())
    new = not path.exists() or not append
    with open(path, "a" if append and path.exists() else "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


_STATION_CODES: dict[str, str] | None = None


def station_code_map(key: str, calls: list[int]) -> dict[str, str]:
    """관측소명 → 관측소코드. 저장된 관측소 파일이 있으면 그것을, 없으면 허브에서 받아 저장한다(일별 기상에 코드를 붙이기 위해)."""
    global _STATION_CODES
    if _STATION_CODES is not None:
        return _STATION_CODES
    ds = DATASETS["weather_station"]
    path = RAW_DIR / ds["file"]
    rows: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    if not rows and ds.get("api"):
        rows = to_rows(ds, fetch_all(key, ds, {}, calls))
        write_csv(path, rows, append=False)
        st = _state(); st["weather_station"] = {"done": True, "rows": len(rows), "at": datetime.now().isoformat(timespec="seconds")}; _save_state(st)
        print(f"  (관측소 정보 {len(rows):,}행 먼저 저장 → {ds['file']})")
    _STATION_CODES = {str(r.get("observatoryName", "")).strip(): str(r.get("observatoryCode", "")).strip() for r in rows if r.get("observatoryName") and r.get("observatoryCode")}
    return _STATION_CODES


def attach_station_code(rows: list[dict], codes: dict[str, str]) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        if not r.get("observatoryCode"):
            r["observatoryCode"] = codes.get(str(r.get("observatoryName", "")).strip(), "")
        out.append(r)
    return out


def daterange(d0: date, d1: date):
    d = d0
    while d <= d1:
        yield d
        d += timedelta(days=1)


def run(key: str, only: list[str], d0: date, d1: date, max_calls: int) -> None:
    st = _state()
    calls = [0]
    for name in only:
        ds = DATASETS[name]
        if not ds.get("api"):
            print(f"\n[{name}] {ds['title']} — 토큰 없음: .env 에 {ds.get('env')}=<토큰> 추가 후 다시 실행 (건너뜀)")
            continue
        done: set[str] = set(st.get(name, {}).get("dates", []))
        print(f"\n[{name}] {ds['title']} (허브 #{ds['hub_id']})")
        try:
            if ds["mode"] == "static":
                if st.get(name, {}).get("done"):
                    print("  이미 저장됨 — 건너뜀 (다시 받으려면 state 파일에서 삭제)")
                    continue
                rows = to_rows(ds, fetch_all(key, ds, {}, calls))
                write_csv(RAW_DIR / ds["file"], rows, append=False)
                st[name] = {"done": True, "rows": len(rows), "at": datetime.now().isoformat(timespec="seconds")}
                print(f"  {len(rows):,}행 저장 → {ds['file']}")
            elif ds["mode"] == "snapshot":
                if st.get(name, {}).get("done"):
                    print("  이미 저장됨 — 건너뜀")
                    continue
                rows = to_rows(ds, fetch_all(key, ds, {ds["date_param"][0]: d1.strftime("%Y%m%d")}, calls))
                write_csv(RAW_DIR / ds["file"], rows, append=False)
                st[name] = {"done": True, "rows": len(rows), "base_date": d1.isoformat()}
                print(f"  {len(rows):,}행 저장 (기준일 {d1}) → {ds['file']}")
            else:
                n_days = 0
                empty_days: list[str] = []
                for d in daterange(d0, d1):
                    ymd = d.strftime("%Y%m%d")
                    if ymd in done:
                        continue
                    if calls[0] >= max_calls:
                        print(f"  호출 상한({max_calls}) 도달 — {d} 이후는 다음 실행에서 이어받습니다.")
                        break
                    dp = ds["date_param"]
                    params = {dp[0]: ymd} if len(dp) == 1 else {dp[0]: ymd, dp[1]: ymd}
                    rows = to_rows(ds, fetch_all(key, ds, params, calls))
                    if name == "weather":
                        rows = attach_station_code(rows, station_code_map(key, calls))
                    write_csv(RAW_DIR / ds["file"].format(ym=d.strftime("%Y%m")), rows, append=True)
                    if rows:
                        done.add(ymd)
                        n_days += 1
                    else:
                        empty_days.append(ymd)   # 0행은 '받음'으로 기록하지 않는다 → 다음 실행에서 다시 시도
                    st[name] = {"dates": sorted(done)}
                    _save_state(st)
                    print(f"  {d} {len(rows):>6,}행  (누적 호출 {calls[0]})")
                print(f"  {n_days}일 추가, 저장된 날짜 {len(done)}일" + (f", 0행 {len(empty_days)}일(제공처 공백 또는 미제공 기간 — 저장 안 함)" if empty_days else ""))
                if empty_days and n_days == 0:
                    print(f"  ⚠️ 요청 기간 전체가 0행입니다. 허브 상세 페이지 'API 미리보기'로 그 날짜에 데이터가 있는지 확인하세요"
                          + (" (일별 기상 정보는 2025-01 까지는 있고 2025-06 이후는 비어 있음을 확인함 — 2025-09 분석에는 fetch_weather.py 사용)" if name == "weather" else ""))
        except Quota as e:
            print(f"  ⚠️ 쿼터 초과로 중단: {e}\n  내일 같은 명령을 다시 실행하면 이어서 받습니다.")
            _save_state(st)
            return
    _save_state(st)
    print(f"\n총 호출 {calls[0]}회. 다음: python scripts/check_data.py")


def status() -> None:
    st = _state()
    if not st:
        print("아직 받은 데이터가 없습니다.")
        return
    for k, v in st.items():
        if "dates" in v:
            print(f"{k:14s} {len(v['dates'])}일  {v['dates'][0]}~{v['dates'][-1]}")
        else:
            print(f"{k:14s} 완료 {v.get('rows', '?')}행")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", default=(date.today() - timedelta(days=45)).isoformat())
    ap.add_argument("--to", dest="d1", default=(date.today() - timedelta(days=3)).isoformat())
    ap.add_argument("--only", default=",".join(DEFAULT_ONLY))
    ap.add_argument("--max-calls", type=int, default=900, help="이번 실행에서 쓸 최대 호출 수(일일 쿼터 1,000 보호)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--probe", help="데이터셋 이름 — 응답 키·첫 행만 확인 (예: --probe dong_population --to 2025-09-01)")
    ap.add_argument("--reset", help="데이터셋 이름(쉼표 구분) — 수집 상태를 지워 다시 받게 함 (파일은 직접 지우세요)")
    a = ap.parse_args()
    if a.status:
        status()
        return 0
    if a.reset:
        st = _state()
        for n in [x.strip() for x in a.reset.split(",") if x.strip()]:
            if st.pop(n, None) is not None:
                print(f"{n}: 수집 상태 초기화 (data/raw 의 해당 CSV 는 직접 삭제 후 다시 받으세요)")
            else:
                print(f"{n}: 상태 없음")
        _save_state(st)
        return 0
    key = os.getenv("JEJUHUB_PROJECT_KEY", "").strip()
    if not key:
        print("JEJUHUB_PROJECT_KEY 가 없습니다. .env 에 추가하세요 (허브 마이페이지 → 프로젝트 → 키).")
        return 1
    if a.probe:
        if a.probe not in DATASETS:
            print(f"알 수 없는 데이터셋: {a.probe} (가능: {', '.join(DATASETS)})"); return 1
        return probe(key, a.probe, date.fromisoformat(a.d1))
    unknown = [x.strip() for x in a.only.split(",") if x.strip() and x.strip() not in DATASETS]
    if unknown:
        print(f"알 수 없는 데이터셋: {', '.join(unknown)} (가능: {', '.join(DATASETS)})")
    only = [x.strip() for x in a.only.split(",") if x.strip() in DATASETS]
    if "weather" in only and "weather_station" in only:   # 일별 기상에 코드를 붙이려면 관측소 정보가 먼저
        only.remove("weather_station"); only.insert(only.index("weather"), "weather_station")
    run(key, only, date.fromisoformat(a.d0), date.fromisoformat(a.d1), a.max_calls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
