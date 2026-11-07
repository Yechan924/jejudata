# 재현 실행 요약

- 실행: 2026-11-07 11:47 · Python 3.13.18 · Linux · 엔진 `sqlite` · 모드 offline(규칙)
- 데이터: 동봉 축약본(data/demo) · 합성 포함 아니오
- 문항 18개 · 도구 오류 0건 · 골든 비교 18/18 동일

| 코드 | 데이터 | 출처 | 행 | 기간 |
|---|---|---|---|---|
| 4-05 | 와이파이 AP별 이용 현황 | raw | 38,366 | 2025-09-01~2025-09-30 |
| 4-01 | 와이파이 AP 현황(고정형, 최신) | raw | 3,046 | 2025-09-15~2025-09-15 |
| 4-06 | 와이파이 일별 이용자 현황 | missing | 0 |  |
| 3-04 | 버스 정류소별 이용 현황 | raw | 107,101 | 2025-09-01~2025-09-18 |
| 3-02 | 버스 노선별 이용 현황 | raw | 54,659 | 2025-09-01~2025-09-30 |
| 3-03 | 버스 정류소 정보 | raw | 4,352 |  |
| 3-01 | 버스 노선 정보 | raw | 745 |  |
| 5-02 | 일일 날씨 정보 | raw | 120 | 2025-09-01~2025-09-30 |
| 5-01 | 날씨 관측소 정보 | raw | 4 |  |

| 질문 | 도구 | 골든 |
|---|---|---|
| 주말에 가장 붐비는 관광지는 어디야? | congestion_rank → place_access | ✓ 동일 |
| 외국인 관광객이 많이 가는 곳은? | congestion_rank → place_access | ✓ 동일 |
| 성산일출봉은 언제 가장 붐비나? | place_profile → place_access | ✓ 동일 |
| 성산일출봉 가는 버스 정류소는 얼마나 붐비나? | place_access | ✓ 동일 |
| 해변 중에 주말에 가장 붐비는 곳은? | congestion_rank → place_access | ✓ 동일 |
| 비 오면 어디가 한산해지나? | weather_effect | ✓ 동일 |
| 251번 노선 시간대별 이용 현황 알려줘 | bus_route_efficiency | ✓ 동일 |
| 효율이 낮은 버스 노선은? | bus_route_efficiency | ✓ 동일 |
| 어르신이 많이 타는 정류소는? | bus_station_rank | ✓ 동일 |
| 한라병원 정류소는 몇 시에 가장 붐비나? | bus_station_rank | ✓ 동일 |
| 9월에 가장 붐빈 관광지는? | congestion_rank → place_access | ✓ 동일 |
| 9월 전반과 후반에 어디가 늘고 줄었어? | compare_periods | ✓ 동일 |
| 평일과 주말 차이가 큰 정류소는? | compare_periods | ✓ 동일 |
| 이번 주말 어디가 붐빌까? | congestion_forecast | ✓ 동일 |
| 9월 월간 브리핑 보여줘 | monthly_brief | ✓ 동일 |
| 어떤 데이터가 있어? | describe_data | ✓ 동일 |
| 눈에 띄는 게 있어? | explore_anomalies | ✓ 동일 |
| __discover__ | congestion_rank → explore_anomalies | ✓ 동일 |