# 실데이터 확보·적재 가이드

이 서비스는 **4-05 와이파이 AP별 이용 현황**과 **3-04 버스 정류소별 이용 현황**을 필수로 쓰고, 나머지 7종(4-01·4-06·3-02·3-01·3-03·5-02·5-01)은 있으면 쓰고 없으면 해당 기능만 비활성화됩니다.

## 0. 먼저 알아야 할 것 — "반출 가능" ≠ "집에서 다운로드 가능" (2026-11-05 확인)

제주데이터허브 → 제주빅데이터센터 → **제공데이터** 페이지에는 4-05·3-04를 비롯한 9종 모두 "반출 가능"으로 표시됩니다. 그러나 실제 조회·다운로드 메뉴인 **데이터검색**을 누르면 다음 안내가 뜹니다.

> 해당 메뉴는 빅데이터센터 분석실에서만 이용 가능합니다. 이용을 원하시는 분은 빅데이터센터 '분석실 예약'을 통해 이용 가능합니다.

즉 명세서의 원본 CSV(4-05의 user_id·국적·방문유형, 3-04의 시간대)는 **센터 분석실에서만** 받을 수 있습니다. 집에서는 아래 두 경로 중 하나를 씁니다.

| 경로 | 무엇을 | 장단점 |
|---|---|---|
| **A. 허브 공개 API (권장, 집에서 가능)** | 같은 제공처(제주빅데이터센터)의 공개 API 6종을 `scripts/fetch_hub.py`로 수집 → `data/raw/` CSV | 2025년 최신 데이터, 10분이면 끝. 단 4-05의 국적·방문유형·user_id, 3-04의 시간대 없음 → 그 기능만 비활성 |
| **B. 센터 분석실 (원본)** | 분석실 예약 → 데이터검색에서 4-05·3-04 원본 CSV 반출 | 모든 기능 활성. 방문 필요 |

A로 먼저 개발·검증하고, 가능하면 B로 원본을 받아 교체하는 순서를 권합니다. 로더는 두 형식을 자동으로 구분합니다(§3).

## 1-A. 허브 공개 API로 받기 (10분)

### 1) 프로젝트 키
https://jejudatahub.net 로그인 → **마이페이지 → 프로젝트** → 프로젝트 키 복사(없으면 프로젝트 생성). 데이터 상세 페이지의 "프로젝트 담기"로 아래 6종을 프로젝트에 담아 두면 됩니다.
`.env`에 추가:
```
JEJUHUB_PROJECT_KEY=여기에_키
```
(`.env`는 `.gitignore`에 있어 커밋되지 않습니다. 키를 코드·문서에 적지 마세요.)

### 2) 수집
```bash
python scripts/fetch_hub.py --from 2025-08-01 --to 2025-09-30          # 6종 전부 (아래 표)
python scripts/fetch_hub.py --from 2025-09-01 --to 2025-09-30 --only wifi,bus_station   # 필수 2종만
python scripts/fetch_hub.py --status                                   # 받은 기간 확인
```
- 쿼터: 데이터셋당 하루 1,000회, 페이지당 100행. 정류소 일일은 하루치가 ~64회라 **한 번에 약 12일치**가 안전합니다. 스크립트가 `--max-calls`(기본 900)에서 멈추고, 다음 날 같은 명령을 다시 실행하면 **받은 날짜는 건너뛰고 이어서** 받습니다(`data/raw/.hub_fetch_state.json`).
- 2개월(약 60일)을 받으려면 정류소는 5~6일에 걸쳐 실행. 와이파이는 하루치 14회라 2개월을 한 번에 받습니다.

| 스크립트 키 | 허브 데이터 (제공처: 제주빅데이터센터) | 명세서 대응 | 저장 파일 |
|---|---|---|---|
| `wifi` | 와이파이 AP그룹별 일일 사용량 (#593) | **4-05** (장소×일 집계) | `data/raw/wifi_ap_usage/…_허브API_YYYYMM.csv` |
| `bus_station` | 일일 정류소별 버스 이용자 정보 (#613) | **3-04** (시간대 없음) | `data/raw/bus_station_usage/…` |
| `bus_route` | 일일 노선별 버스 이용자 정보 (#614) | 3-02 | `data/raw/bus_route_usage/…` |
| `station_info` | 버스 정류소 기본 정보 (#612) | 3-03 (좌표) | `data/raw/버스 정류소 정보_허브API.csv` |
| `route_info` | 버스 노선 기본 정보 (#611) | 3-01 (노선거리) | `data/raw/버스 노선 정보_허브API.csv` |
| `ap_info` | 제주 공공와이파이 AP 정보 (#618) | 4-01 (AP 좌표·카테고리) | `data/raw/와이파이 AP 현황_허브API.csv` |

#### 선택 데이터 3종 (허브 데이터목록 20261105 기준) — 같은 프로젝트 키로 받습니다
새 키 발급은 없습니다. 허브에 로그인해 아래 세 데이터의 상세 페이지에서 **"프로젝트 담기"**만 눌러 기존 프로젝트에 추가한 뒤 실행하세요(API 토큰은 상세 페이지에 공개된 값이 스크립트에 들어 있어 `.env`에 더 넣을 것이 없습니다).

| 스크립트 키 | 허브 데이터 (제공: 제주특별자치도) | 요청 변수 | 응답 키 | 저장 |
|---|---|---|---|---|
| `dong_population` | [읍면동 단위 내국인 유동인구](https://jejudatahub.net/data/view/data/POPULATION/583) | `startDate`,`endDate` (+city, emd, gender, ageGroup) | baseDate, city, emd, gender, ageGroup, **resdPop·workPop·visitPop** | `data/raw/dong_population/…_YYYYMM.csv` |
| `weather` | [일별 기상 정보](https://jejudatahub.net/data/view/data/579) | `searchDate` (+observatoryName) | observatoryName, baseDate, averageTemperature, dailyRainfall, 최저·최고기온, 풍속 … (코드 없음 → 스크립트가 관측소 정보로 `observatoryCode` 추가) | `data/raw/weather_daily/…_YYYYMM.csv` |
| `weather_station` | [기상관측소 정보](https://jejudatahub.net/data/view/data/578) | (없음) | observatoryCode, observatoryName, latitude, longitude(예시는 뒤바뀜 → 로더 보정), seaLevelAltitude … | `data/raw/weather_station/…csv` |

```bash
python scripts/fetch_hub.py --probe dong_population --to 2025-09-01        # 응답 키·첫 행 확인
python scripts/fetch_hub.py --from 2025-09-01 --to 2025-09-30 --only dong_population,weather_station   # weather 는 2025-01 이전 기간에만
python scripts/check_data.py && python scripts/validate_index.py           # 유동인구가 들어오면 VALIDATION.md 에 A1절(외부 검증) 자동 추가
```
- 쿼터: 유동인구는 하루치가 읍면동(43)×성별(2)×연령대(~9) ≈ 770행 → 8회, 한 달 ≈ 240회. 일별 기상은 하루 1회(관측소 ~30곳), 관측소 정보 1회. 기존 정류소 수집과 쿼터가 분리되어 있습니다.
- 응답 키는 그대로 저장되며 로더 별칭(`config/column_aliases.yaml`)이 명세서 컬럼으로 맞춥니다(emd→dong_name, visitPop→visitor_pop, dailyRainfall→daily_rainfall …). 토큰이 바뀌면 `.env`에 `JEJUHUB_API_DONG_POPULATION=` 등으로 덮어쓸 수 있습니다.
- 허브 일별 기상은 기상청 AWS 관측소 42곳이라 ASOS 4개 지점보다 촘촘하지만, **2025-01까지만 적재되어 있고 2025-06 이후는 0행**입니다(2026-11-09 API 미리보기로 확인; 0행인 날은 스크립트가 '받음'으로 기록하지 않음). 2025-09 분석에는 아래 `fetch_weather.py` 경로를 쓰세요. 관측소 정보(좌표)는 허브 것을 그대로 씁니다.

날씨를 허브 대신 기상청·Open-Meteo로 받으려면 `python scripts/fetch_weather.py` (없으면 `weather_effect`만 비활성, 기간이 다른 합성 날씨는 실데이터에 섞지 않음).

| 경로 | 명령 | 비고 |
|---|---|---|
| **A. 기상청 기상자료개방포털 CSV (권장, 관측값)** | data.kma.go.kr → 데이터 → 기상관측 → 지상 → 종관기상관측(ASOS) → '일 자료' → 지점 제주(184)·고산(185)·성산(188)·서귀포(189) → 기간 → CSV 다운로드(무료 로그인) → `python scripts/fetch_weather.py --kma-csv OBS_ASOS_DD_….csv` | 변환 없이 `data/raw/일일 날씨 정보_기상청.csv` 로 넣어도 별칭(지점·일시·일강수량(mm)…)으로 읽힘 |
| B. Open-Meteo (키 없음) | `python scripts/fetch_weather.py --open-meteo --from 2025-09-01 --to 2025-09-30` | ERA5 재분석 격자값(관측소 실측 아님), CC BY 4.0, 비상업·연구용 |
| C. 기상청 API허브 | `.env`에 `KMA_API_KEY` → `python scripts/fetch_weather.py --kma-api --from … --to …` | apihub.kma.go.kr 가입·인증키 |

저장 위치: `data/raw/weather_daily/일일 날씨 정보_<출처>_YYYYMM.csv`(5-02), `data/raw/weather_station/날씨 관측소 정보_<출처>.csv`(5-01). 관측소 파일이 없어도 내장 `config/weather_stations_jeju.csv`로 동작합니다. 확인: `python scripts/check_data.py` 에서 weather_daily 가 `source=raw`.

### 3) 확인
```bash
python scripts/check_data.py            # 인식·헤더 매핑·기간·비활성 기능 표
python scripts/check_data.py --strict   # 필수 2종이 실데이터가 아니면 종료코드 1
```
A 경로에서 정상이면 이렇게 보입니다:
- 4-05: `source=raw`, note에 "좌표·카테고리를 4-01에서 장소명으로 보완(xx% 일치)", 비활성 기능 `nationality(외국인 비중), visit_type(관광객 비중), user_id(고유 이용자)`
- 3-04: `source=raw`, 비활성 기능 `base_hour(시간대 프로필·피크 시간)`
- 3-02: note에 "bus_route_id 를 3-01 노선번호로 보완"

### 4) 샘플·문서 재생성
```bash
python -m jeju_agent.cli --offline --batch examples/questions.txt --out outputs/samples/batch_offline.json
python scripts/llm_smoke.py                      # .env 에 LLM 키가 있을 때 → docs/LLM_EVAL.md
python -m jeju_agent.cli --offline --png "효율이 낮은 버스 노선은?"     # docs/img 갱신용
python scripts/make_submission.py                # ZIP·PDF
```
그다음 README·기획서·RESULT_REPORT의 "합성 데이터" 문구를 "허브 공개 API 실데이터(2025-08~09)"로 바꾸고, 비활성 기능은 "센터 원본 적재 시 활성화"로 적습니다(검색어 `합성`).

## 1-B. 센터 분석실에서 원본 받기

1. 허브 → 제주빅데이터센터 → **분석실 예약**.
2. 분석실 PC에서 **데이터검색** → 4-05·3-04(+3-01·3-02·3-03·4-01·4-06·5-01·5-02) → 기간 지정 → CSV 반출 신청.
3. 반출 정책(2026.09.01 개정)상 원본·발췌·집계·시각화 모두 공개 가능 표에 있는 데이터입니다. 반출 심사 시 이 서비스의 반출 게이트(10 미만 마스킹, PNG 차트)를 설명하면 됩니다.
4. 받은 파일을 `data/raw/`에 복사(파일명은 데이터명·코드·영문키 중 아무거나로 시작). 허브 API 파일과 같이 두면 **원본이 우선**됩니다(원본에 user_id가 있으면 로더가 원본 모드로 전환).

## 2. 파일 넣기 규칙

| 데이터 | 인식되는 파일명 예 |
|---|---|
| 4-05 | `와이파이 AP별 이용 현황.csv`, `4-05 와이파이 AP별 이용 현황_202507.csv`, `wifi_ap_usage.xlsx`, 폴더 `data/raw/wifi_ap_usage/*.csv` |
| 3-04 | `버스 정류소별 이용 현황.csv`, `3-04_버스정류소별이용현황.csv`, `일일 정류소별 버스 이용자 정보.csv` |
| 4-01 | `와이파이 AP 현황(고정형).csv`, `와이파이 AP 현황_허브API.csv`, `제주 공공와이파이 AP 정보.csv` |
| 그 외 | `config/schemas.yaml`의 `name`·`code`·키, 또는 `loader.py`의 `FILE_ALIASES` |

지원 형식: `.csv` `.tsv` `.txt` `.xlsx`. 인코딩·구분자·날짜 표기 자동. 헤더는 명세서 영문/한글명, 허브 API 영문명(`apGroupName`, `stationId` 등), 변형(`정류장명`, `승차인원수`)을 `config/column_aliases.yaml`로 맞춥니다.

## 3. 로더가 두 형식을 다루는 방식

| 상황 | 로더·도구 동작 |
|---|---|
| 4-05 원본(user_id 있음) | 장소×일 고유 user_id 수 = 이용자. 국적·방문유형 비중 계산 |
| 4-05 집계본(user_id 없고 user_count 있음) | user_count 합 = 이용자. 국적·방문유형 도구 인자(foreign_only 등)는 안내 오류, 답변 한계에 "집계본" 문구 자동 |
| 4-05에 좌표 없음 | 4-01(AP 현황)에서 장소명으로 좌표·카테고리 보완 |
| 3-04에 시간대 없음 | 일 단위 집계는 정상, `hourly_profile`·피크 시간은 비어 있음(점검 스크립트가 안내) |
| 3-02에 bus_route_id 없음 | 3-01 노선번호로 ID 보완, 없으면 노선명을 ID로 |

## 4. 자주 겪는 문제

| 증상 | 원인·해결 |
|---|---|
| `JEJUHUB_PROJECT_KEY 가 없습니다` | `.env`에 키 추가(§1-A) |
| `쿼터 초과로 중단` | 내일 같은 명령 재실행(이어받기) 또는 `--only`로 필수 2종만 |
| `source=synthetic` 그대로 | 파일명 미인식 → §2 표대로 이름 변경 후 "데이터 다시 읽기" |
| 지도에 점이 없음 | 4-01(`ap_info`)을 받지 않아 좌표 보완 실패 → `--only ap_info` |
| 혼잡 순위에 "외국인 비중 0%" | 집계본(A 경로)이라 정상. 원본(B 경로) 적재 시 활성 |
| 메모리 부족 | 월 단위 파일로 나눠 최근 2~3개월만. `pip install duckdb` 권장 |
