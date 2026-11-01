"""명세서(v12) 스키마를 그대로 따르는 개발용 합성 데이터 생성기.

⚠️ 이 데이터는 실제 제주 데이터가 아니다. 컬럼 구조와 값의 범위만 명세서를 따르며,
   수치는 계절성·주말·강우·시간대 패턴을 넣은 난수다. 실제 CSV를 data/raw/ 에 넣으면
   loader가 자동으로 raw를 우선 사용한다.

실행:  python -m jeju_agent.data.synthetic  [--start 2025-01-01 --end 2025-06-30 --scale 1.0]
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import SYNTHETIC_DIR

rng = np.random.default_rng(42)

# ---- 장소 마스터 (좌표는 대략값, 패턴 파라미터는 임의) -------------------------------
# (장소명, 카테고리, 상세카테고리, 읍면동주소, lat, lon, 기본일세션, 관광비중, 야외여부)
PLACES = [
    ("성산일출봉", "관광지", "공영관광지", "서귀포시 성산읍 성산리", 33.4587, 126.9425, 140, 0.92, True),
    ("섭지코지", "관광지", "사설관광지", "서귀포시 성산읍 고성리", 33.4240, 126.9310, 70, 0.90, True),
    ("함덕해수욕장", "관광지", "해수욕장", "제주시 조천읍 함덕리", 33.5434, 126.6695, 90, 0.80, True),
    ("월정리해변", "관광지", "해수욕장", "제주시 구좌읍 월정리", 33.5560, 126.7960, 60, 0.85, True),
    ("협재해수욕장", "관광지", "해수욕장", "제주시 한림읍 협재리", 33.3940, 126.2395, 85, 0.82, True),
    ("애월한담해안", "관광지", "해안도로", "제주시 애월읍 애월리", 33.4640, 126.3100, 65, 0.78, True),
    ("이호테우해변", "관광지", "해수욕장", "제주시 이호일동", 33.4980, 126.4530, 50, 0.60, True),
    ("용두암", "관광지", "공영관광지", "제주시 용담이동", 33.5160, 126.5120, 55, 0.75, True),
    ("동문시장", "시장", "전통시장", "제주시 일도일동", 33.5120, 126.5280, 120, 0.55, False),
    ("제주국제공항", "교통", "공항", "제주시 용담이동", 33.5070, 126.4930, 300, 0.70, False),
    ("제주항여객터미널", "교통", "항만", "제주시 건입동", 33.5200, 126.5280, 40, 0.50, False),
    ("제주버스터미널", "교통", "터미널", "제주시 오라일동", 33.4990, 126.5150, 90, 0.45, False),
    ("중문관광단지", "관광지", "공영관광지", "서귀포시 색달동", 33.2440, 126.4120, 110, 0.88, True),
    ("천지연폭포", "관광지", "공영관광지", "서귀포시 천지동", 33.2470, 126.5550, 60, 0.85, True),
    ("정방폭포", "관광지", "공영관광지", "서귀포시 동홍동", 33.2450, 126.5720, 45, 0.85, True),
    ("서귀포매일올레시장", "시장", "전통시장", "서귀포시 서귀동", 33.2500, 126.5640, 100, 0.60, False),
    ("한라산성판악", "관광지", "국립공원", "제주시 조천읍 교래리", 33.3850, 126.6200, 50, 0.80, True),
    ("사려니숲길", "관광지", "공영관광지", "제주시 조천읍 교래리", 33.4100, 126.6700, 45, 0.80, True),
    ("비자림", "관광지", "공영관광지", "제주시 구좌읍 평대리", 33.4860, 126.8090, 40, 0.85, True),
    ("만장굴", "관광지", "공영관광지", "제주시 구좌읍 김녕리", 33.5280, 126.7710, 35, 0.88, False),
    ("산방산", "관광지", "공영관광지", "서귀포시 안덕면 사계리", 33.2370, 126.3130, 45, 0.85, True),
    ("송악산", "관광지", "공영관광지", "서귀포시 대정읍 상모리", 33.2000, 126.2900, 35, 0.85, True),
    ("오설록티뮤지엄", "관광지", "사설관광지", "서귀포시 안덕면 서광리", 33.3060, 126.2890, 75, 0.90, False),
    ("제주시청", "공공기관", "관공서", "제주시 이도이동", 33.4990, 126.5310, 80, 0.10, False),
    ("서귀포시청", "공공기관", "관공서", "서귀포시 서홍동", 33.2540, 126.5600, 50, 0.08, False),
    ("제주대학교", "교육기관", "대학교", "제주시 아라일동", 33.4560, 126.5620, 110, 0.05, False),
    ("노형로터리", "상업지구", "번화가", "제주시 노형동", 33.4840, 126.4810, 90, 0.30, False),
    ("김녕해수욕장", "관광지", "해수욕장", "제주시 구좌읍 김녕리", 33.5580, 126.7590, 30, 0.80, True),
    ("표선해수욕장", "관광지", "해수욕장", "서귀포시 표선면 표선리", 33.3260, 126.8430, 30, 0.78, True),
    ("곽지해수욕장", "관광지", "해수욕장", "제주시 애월읍 곽지리", 33.4510, 126.3060, 35, 0.80, True),
]

# 정류소: 장소 근처 + 도심. (정류소명, lat, lon, 시내/시외, 기본일승차, 경로비중)
STATIONS = [(f"{p[0]}", p[4] + 0.0015, p[5] - 0.0012, "시외" if p[1] == "관광지" else "시내",
             int(p[6] * 1.3), 0.12 if p[1] != "관광지" else 0.08) for p in PLACES]
STATIONS += [
    ("애월읍사무소", 33.4630, 126.3290, "시내", 60, 0.35),
    ("한림환승정류장", 33.4110, 126.2690, "시외", 110, 0.30),
    ("대정읍사무소", 33.2250, 126.2500, "시내", 50, 0.38),
    ("성산읍사무소", 33.4380, 126.9140, "시내", 45, 0.33),
    ("남원읍사무소", 33.2790, 126.7170, "시내", 40, 0.36),
    ("구좌읍사무소", 33.5210, 126.8520, "시내", 45, 0.34),
    ("조천읍사무소", 33.5380, 126.6350, "시내", 55, 0.30),
    ("표선면사무소", 33.3270, 126.8330, "시내", 40, 0.37),
    ("제주시외버스터미널", 33.4993, 126.5147, "시외", 400, 0.15),
    ("서귀포버스터미널", 33.2540, 126.5090, "시외", 220, 0.18),
    ("신제주로터리", 33.4890, 126.4910, "시내", 300, 0.14),
    ("제주시청(아라방면)", 33.4990, 126.5315, "시내", 350, 0.12),
    ("한라병원", 33.4890, 126.4790, "시내", 180, 0.28),
    ("제주대학교병원", 33.4580, 126.5640, "시내", 150, 0.30),
]

ROUTES = [  # (노선ID, 번호, 이름, 정류소수, 길이m, 기본일승객, 비효율시간대여부)
    ("20100000", "101", "101(제주버스터미널~서귀포버스터미널,동일주)", 48, 76000, 2600, False),
    ("20200000", "102", "102(제주버스터미널~서귀포버스터미널,서일주)", 47, 74000, 2400, False),
    ("20110000", "111", "111(제주국제공항~성산항)", 30, 52000, 1500, False),
    ("20120000", "112", "112(제주국제공항~성산항)", 32, 54000, 1100, False),
    ("20150000", "151", "151(제주국제공항~운진항)", 34, 50000, 1300, False),
    ("20180000", "181", "181(제주국제공항~제주국제공항,516도로)", 36, 68000, 900, False),
    ("20200100", "201", "201(제주버스터미널~서귀포버스터미널,동일주)", 150, 80000, 3800, False),
    ("20200200", "202", "202(제주버스터미널~서귀포버스터미널,서일주)", 148, 79000, 3400, False),
    ("20250000", "251", "251(제주버스터미널~대정환승정류장)", 90, 52000, 420, True),
    ("20280000", "281", "281(제주버스터미널~서귀포버스터미널,5.16)", 92, 45000, 2900, False),
    ("20360000", "365", "365(한라대학교~제주대학교)", 72, 28000, 5200, False),
    ("20310000", "311", "311(함덕~제주대학교)", 68, 26000, 2100, False),
    ("20320000", "312", "312(함덕~제주대학교)", 66, 25000, 1600, False),
    ("20700000", "701", "701(성산항~표선~서귀포)", 85, 48000, 380, True),
    ("20770000", "771-1", "771-1(제주국제공항~애월환승정류장)", 40, 26000, 260, True),
]

WEATHER_STATIONS = [("184", "제주", 33.5141, 126.5297), ("189", "서귀포", 33.2461, 126.5653),
                    ("188", "성산", 33.3868, 126.8802), ("185", "고산", 33.2938, 126.1628)]

NATIONALITIES = ["대한민국", "중국", "일본", "대만", "미국", "베트남", "기타"]
NAT_P = [0.80, 0.11, 0.03, 0.02, 0.01, 0.01, 0.02]
PROVINCES = ["제주특별자치도", "서울특별시", "경기도", "부산광역시", "인천광역시", "대구광역시", "경상남도", "충청남도", None]
PROV_P = [0.25, 0.26, 0.20, 0.07, 0.05, 0.04, 0.04, 0.03, 0.06]
AGES = ["10", "20", "30", "40", "50", "60"]
AGE_P = [0.08, 0.30, 0.27, 0.18, 0.11, 0.06]
VISIT_TYPES = ["관광", "거주", "업무", "국제회의 및 인센티브 투어", "기타"]
USER_CATS = ["일반", "청소년", "어린이", "경로", "장애 일반", "장애 동반", "유공 일반", "유공 동반"]

HOUR_PROFILE_TOUR = np.array([0.2, 0.1, 0.05, 0.05, 0.1, 0.3, 0.6, 1.0, 1.8, 2.8, 3.6, 4.0, 3.8, 3.9, 3.7, 3.4, 3.0, 2.6, 2.0, 1.5, 1.1, 0.8, 0.5, 0.3])
HOUR_PROFILE_CITY = np.array([0.1, 0.05, 0.03, 0.03, 0.1, 0.6, 2.2, 4.5, 4.2, 2.3, 1.8, 1.9, 2.4, 2.0, 1.9, 2.2, 2.8, 4.0, 4.6, 3.2, 2.0, 1.3, 0.7, 0.3])


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def season_factor(d: date) -> float:
    m = d.month
    base = {1: 0.75, 2: 0.8, 3: 0.95, 4: 1.15, 5: 1.2, 6: 1.05, 7: 1.3, 8: 1.45, 9: 1.1, 10: 1.25, 11: 1.0, 12: 0.85}[m]
    return base


def make_weather(days: list[date]) -> pd.DataFrame:
    rows = []
    rain_global = rng.random(len(days)) < 0.28
    for i, d in enumerate(days):
        t_base = 6 + 14 * (1 - np.cos((d.timetuple().tm_yday - 20) / 365 * 2 * np.pi)) / 2 * 1.4
        for code, name, lat, lon in WEATHER_STATIONS:
            rain = rain_global[i] or rng.random() < 0.05
            mm = float(np.round(rng.gamma(2.0, 9.0), 1)) if rain else 0.0
            if d.month in (7, 8) and rain:
                mm *= 2.2
            avg = t_base + rng.normal(0, 1.5) + (1.0 if name == "서귀포" else 0)
            wind = rng.gamma(2.5, 1.2) + (2.0 if name == "고산" else 0)
            rows.append({"base_date": d.strftime("%Y%m%d"), "pt_year": str(d.year), "pt_month": str(d.month),
                         "observatory_code": code, "average_temperature": round(avg, 1),
                         "lowest_temperature": round(avg - rng.uniform(2, 5), 1), "lowest_temperature_time": "0530",
                         "highest_temperature": round(avg + rng.uniform(3, 6), 1), "highest_temperature_time": "1400",
                         "daily_rainfall": round(mm, 1), "maximum_wind_speed": round(wind * 2.6, 1),
                         "maximum_wind_speed_time": "1300", "average_wind_speed": round(wind, 1),
                         "maximum_wind_speed_direction": round(rng.uniform(0, 360), 1)})
    return pd.DataFrame(rows)


def make_wifi(days: list[date], weather: pd.DataFrame, scale: float):
    """4-01 AP 현황, 4-05 AP별 이용(세션 단위), 4-06 일별 이용자."""
    rain_by_day = weather[weather.observatory_code == "184"].set_index("base_date")["daily_rainfall"].to_dict()
    ap_rows, use_rows = [], []
    macs = {}
    for i, p in enumerate(PLACES):
        name, cat, cat_d, addr, lat, lon, base, tour_share, outdoor = p
        for k in range(2 if base > 80 else 1):
            mac = _hash(f"{name}-{k}")
            macs[(name, k)] = mac
            ap_rows.append({"base_date": "20250101", "pt_year": "2025", "pt_month": "1", "manage_no": f"25-A{i:03d}{k}",
                            "mac_address": mac, "ap_group_name": name, "install_location_detail": ["입구", "매표소", "광장"][k % 3],
                            "open_date": "20190615", "category": cat, "category_detail": cat_d,
                            "address_dong": "제주특별자치도 " + addr, "address_detail": addr + " 1",
                            "latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}"})
    monthly_trend = {}  # 월정리 상승 추세 스토리
    for d in days:
        ds = d.strftime("%Y%m%d")
        rain = rain_by_day.get(ds, 0.0)
        wk = 1.45 if d.weekday() >= 5 else 1.0
        sf = season_factor(d)
        for p in PLACES:
            name, cat, cat_d, addr, lat, lon, base, tour_share, outdoor = p
            lam = base * sf * (wk if tour_share > 0.5 else (0.7 if d.weekday() >= 5 else 1.0)) * scale
            if outdoor and rain > 1:
                lam *= 0.55 if rain < 20 else 0.3
            if name == "성산일출봉" and d.weekday() >= 5:
                lam *= 1.6
            if name == "월정리해변":
                lam *= 1 + 0.06 * ((d.year - 2025) * 12 + d.month - 1)
            n = int(rng.poisson(lam))
            if n == 0:
                continue
            nat = rng.choice(NATIONALITIES, size=n, p=NAT_P)
            if tour_share < 0.3:
                nat = np.where(rng.random(n) < 0.97, "대한민국", nat)
            tour_p = np.clip(tour_share + (0.08 if d.weekday() >= 5 else 0), 0, 0.97)
            vt = np.where(rng.random(n) < tour_p, "관광", rng.choice(["거주", "업무", "기타"], size=n, p=[0.6, 0.3, 0.1]))
            vt = np.where((nat != "대한민국") & (rng.random(n) < 0.08), "국제회의 및 인센티브 투어", vt)
            prov = rng.choice(PROVINCES, size=n, p=PROV_P)
            prov = np.where(nat != "대한민국", None, prov)
            prov = np.where((vt == "거주"), "제주특별자치도", prov)
            k_idx = rng.integers(0, 2 if base > 80 else 1, size=n)
            sess = rng.gamma(1.5, 600, size=n).astype(int)
            down = (sess * rng.uniform(800, 6000, size=n)).astype(int)
            for j in range(n):
                use_rows.append((ds, str(d.year), str(d.month), _hash(f"u{ds}{name}{j}"), macs[(name, int(k_idx[j]))],
                                 int(down[j] * 0.15), int(down[j]), int(sess[j]), nat[j], prov[j],
                                 "남성" if rng.random() < 0.47 else "여성", rng.choice(AGES, p=AGE_P), vt[j], "고정형",
                                 cat_d, name, ["입구", "매표소", "광장"][int(k_idx[j]) % 3], f"{lat:.6f}", f"{lon:.6f}", addr + " 1"))
    cols = ["base_date", "pt_year", "pt_month", "user_id", "mac_address", "upload", "download", "session_time", "nationality",
            "province", "gender", "age", "visit_type", "ap_type", "ap_category", "ap_place", "ap_place_detail",
            "ap_latitude", "ap_longitude", "ap_address_detail"]
    use = pd.DataFrame(use_rows, columns=cols)
    # 4-06 일별 이용자: 세션을 집계
    daily = (use.groupby(["base_date", "pt_year", "pt_month", "gender", "age", "nationality", "province", "visit_type"], dropna=False)
             .size().reset_index(name="user_count"))
    dow = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    daily["day_of_week"] = pd.to_datetime(daily.base_date).dt.weekday.map(dow)
    return pd.DataFrame(ap_rows), use, daily


def make_bus(days: list[date], weather: pd.DataFrame, scale: float):
    """3-01 노선정보, 3-03 정류소정보, 3-04 정류소별 이용(시간대), 3-02 노선별 이용(시간대)."""
    rain_by_day = weather[weather.observatory_code == "184"].set_index("base_date")["daily_rainfall"].to_dict()
    dow = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    st_rows = [{"serial_number": str(i + 1), "base_date": "20250101", "pt_year": "2025", "pt_month": "1",
                "bus_station_id": str(100000 + i), "bus_station_name": s[0], "bus_city_type": s[3],
                "longitude": f"{s[2]:.6f}", "latitude": f"{s[1]:.6f}"} for i, s in enumerate(STATIONS)]
    rt_rows = [{"serial_number": str(i + 1), "base_date": "20250101", "pt_year": "2025", "pt_month": "1",
                "bus_route_id": r[0], "bus_route_number": r[1], "bus_route_name": r[2], "start_date": "20170826",
                "bus_station_count": r[3], "bus_route_length": r[4], "bus_transferability": "Y"} for i, r in enumerate(ROUTES)]
    su, ru = [], []
    for d in days:
        ds = d.strftime("%Y%m%d")
        rain = rain_by_day.get(ds, 0.0)
        wkend = d.weekday() >= 5
        sf = season_factor(d)
        for i, (name, lat, lon, ctype, base, senior) in enumerate(STATIONS):
            touristy = ctype == "시외" and senior < 0.2
            day_f = (1.35 if wkend else 1.0) if touristy else (0.75 if wkend else 1.0)
            lam_day = base * (sf if touristy else 1.0) * day_f * scale
            if rain > 1:
                lam_day *= 0.8 if touristy else 1.08  # 비 오면 관광지 ↓, 도심 버스 ↑
            prof = HOUR_PROFILE_TOUR if touristy else HOUR_PROFILE_CITY
            for h in range(24):
                lam = lam_day * prof[h] / prof.sum()
                n_on = int(rng.poisson(lam))
                if n_on == 0:
                    continue
                # 경로 이용은 오전에 집중
                sp = senior * (1.6 if 8 <= h <= 11 else 0.8)
                cats = rng.choice(USER_CATS, size=n_on, p=[1 - sp - 0.14, 0.08, 0.02, sp, 0.015, 0.01, 0.01, 0.005])
                vc = pd.Series(cats).value_counts()
                for cat, cnt in vc.items():
                    su.append((ds, str(d.year), str(d.month), dow[d.weekday()], f"{h:02d}", str(100000 + i), name, "승차", cat, "카드", int(cnt)))
                    if rng.random() < 0.18:
                        su.append((ds, str(d.year), str(d.month), dow[d.weekday()], f"{h:02d}", str(100000 + i), name, "환승", cat, "카드", max(1, int(cnt * 0.2))))
                    su.append((ds, str(d.year), str(d.month), dow[d.weekday()], f"{h:02d}", str(100000 + i), name, "하차", cat, "카드", max(1, int(cnt * rng.uniform(0.7, 1.2)))))
        for rid, rno, rname, nst, length, base, ineff in ROUTES:
            lam_day = base * (1.15 if wkend and rno in ("101", "102", "111", "112", "201", "202", "701") else (0.8 if wkend else 1.0)) * scale
            if rain > 1:
                lam_day *= 0.9
            for h in range(5, 24):
                prof = HOUR_PROFILE_CITY[h] if rno in ("365", "311", "312", "281") else HOUR_PROFILE_TOUR[h]
                lam = lam_day * prof / 40
                if ineff and 10 <= h <= 15:
                    lam *= 0.12  # 비효율 스토리: 낮 시간대 거의 빈 차
                n = int(rng.poisson(lam))
                if n == 0:
                    continue
                cats = pd.Series(rng.choice(USER_CATS, size=n, p=[0.68, 0.1, 0.03, 0.15, 0.015, 0.01, 0.01, 0.005])).value_counts()
                for cat, cnt in cats.items():
                    ru.append((ds, str(d.year), str(d.month), dow[d.weekday()], f"{h:02d}", rid, rname, "승차", cat, "카드", int(cnt)))
                    if rng.random() < 0.15:
                        ru.append((ds, str(d.year), str(d.month), dow[d.weekday()], f"{h:02d}", rid, rname, "환승", cat, "카드", max(1, int(cnt * 0.2))))
    scols = ["base_date", "pt_year", "pt_month", "day_of_week", "base_hour", "bus_station_id", "bus_station_name", "get_on_off_type", "user_category", "pay_type", "user_count"]
    rcols = ["base_date", "pt_year", "pt_month", "day_of_week", "base_hour", "bus_route_id", "bus_route_name", "get_on_off_type", "user_category", "pay_type", "user_count"]
    return pd.DataFrame(rt_rows), pd.DataFrame(st_rows), pd.DataFrame(su, columns=scols), pd.DataFrame(ru, columns=rcols)


def make_weather_station() -> pd.DataFrame:
    return pd.DataFrame([{"observatory_code": c, "observatory_name": n, "start_date": "20150713", "end_date": "",
                          "management_office": "제주지방기상청(184)", "longitude": lon, "latitude": lat,
                          "legal_dongri_code": "5011000000", "sea_level_altitude": 20.0} for c, n, lat, lon in WEATHER_STATIONS])


def generate(start: date, end: date, scale: float = 1.0, out: Path = SYNTHETIC_DIR) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    weather = make_weather(days)
    ap, use, daily = make_wifi(days, weather, scale)
    routes, stations, st_use, rt_use = make_bus(days, weather, scale)
    files = {"weather_daily": weather, "weather_station": make_weather_station(), "wifi_ap": ap, "wifi_ap_usage": use,
             "wifi_daily_users": daily, "bus_route": routes, "bus_station": stations, "bus_station_usage": st_use,
             "bus_route_usage": rt_use}
    counts = {}
    for k, df in files.items():
        df.to_csv(out / f"{k}.csv", index=False, encoding="utf-8-sig")
        counts[k] = len(df)
    (out / "README_SYNTHETIC.txt").write_text(
        "이 폴더의 CSV는 명세서 스키마를 따른 개발용 합성 데이터입니다. 실제 제주 데이터가 아닙니다.\n"
        f"기간: {start} ~ {end}, scale={scale}\n" + "\n".join(f"{k}: {v:,} rows" for k, v in counts.items()), encoding="utf-8")
    return counts


if __name__ == "__main__":
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--start", default="2025-01-01")
    ap_.add_argument("--end", default="2025-06-30")
    ap_.add_argument("--scale", type=float, default=0.5)
    a = ap_.parse_args()
    c = generate(date.fromisoformat(a.start), date.fromisoformat(a.end), a.scale)
    for k, v in c.items():
        print(f"{k:20s} {v:>10,}")
