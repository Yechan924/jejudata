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
        for attempt in range(3):
            r = requests.get(url, params=p, timeout=60)
            calls[0] += 1
            if r.status_code == 429 or "quota" in r.text.lower() or "쿼터" in r.text:
                raise Quota(r.text[:200])
            if r.ok:
                break
            time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"{ds['title']} HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        rows += j.get("data", [])
        if not j.get("hasMore") or not j.get("data"):
            return rows
        page += 1


def to_rows(ds: dict, data: list[dict]) -> list[dict]:
    m = ds["map"]
    return [{m[k]: v for k, v in d.items() if k in m} for d in data]


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
                    write_csv(RAW_DIR / ds["file"].format(ym=d.strftime("%Y%m")), rows, append=True)
                    done.add(ymd)
                    n_days += 1
                    st[name] = {"dates": sorted(done)}
                    _save_state(st)
                    print(f"  {d} {len(rows):>6,}행  (누적 호출 {calls[0]})")
                print(f"  {n_days}일 추가, 저장된 날짜 {len(done)}일")
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
    a = ap.parse_args()
    if a.status:
        status()
        return 0
    key = os.getenv("JEJUHUB_PROJECT_KEY", "").strip()
    if not key:
        print("JEJUHUB_PROJECT_KEY 가 없습니다. .env 에 추가하세요 (허브 마이페이지 → 프로젝트 → 키).")
        return 1
    only = [x.strip() for x in a.only.split(",") if x.strip() in DATASETS]
    run(key, only, date.fromisoformat(a.d0), date.fromisoformat(a.d1), a.max_calls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
