"""화면 표시 전용 포매터 — Agent 로직과 무관한 한국어 라벨·숫자 서식·공용 문구 모음.

jeju_agent 쪽은 읽기만 한다(TOOL_KO·describe_step 재사용). 여기 바꿔도 분석 결과는 달라지지 않는다.
"""
from __future__ import annotations

import pandas as pd

from jeju_agent import schemas
from jeju_agent.data.loader import INFO_TABLES
from jeju_agent.insight import TOOL_KO, describe_step  # noqa: F401  (표시용 재사용)

# ---------------- 공용 문구 (두 곳 이상에서 쓰는 사용자 노출 문자열) ----------------
SERVICE_ONELINER = "공공 와이파이 접속과 버스 승하차 데이터로 제주 관광지의 혼잡과 대중교통 수요를 진단합니다."
PRIVACY_NOTE = "모든 결과는 집계값이며 개인정보를 포함하지 않습니다. 질문에 개인정보를 적지 마세요."

ABOUT_MD = """\
- **무엇을 하나요?** 제주 공공 와이파이 접속 기록을 '혼잡 센서'로, 티머니 버스 승하차를 '이동 수요'로 삼아,
  질문 한 줄에 지도·차트·표와 쉬운 해설로 답합니다.
- **어떤 데이터를 쓰나요?** 제주데이터허브의 공공 와이파이 AP 이용(4-05), 버스 정류소·노선 이용(3-04·3-02),
  일일 날씨(5-02) 등입니다. 실제 적재 기간은 위 안내와 사이드바 '적재 데이터'에서 확인할 수 있습니다.
- **답은 어떻게 만드나요?** Agent가 질문에 맞는 분석 도구를 골라 집계를 돌리고,
  모든 답 아래 '근거 보기'에 사용한 도구·데이터·기간·한계를 남깁니다.
- **개인정보는요?** 원본 행과 식별자는 화면·외부 LLM 어디에도 나가지 않습니다. 집계값만 쓰고, 10명 미만 집계는 가려집니다.
"""

STARTERS = ["주말에 가장 붐비는 관광지는 어디야?", "효율이 낮은 버스 노선은?", "어르신이 많이 타는 정류소는?",
            "비 오면 어디가 한산해지나?", "이번 주말 어디가 붐빌까?", "9월 월간 브리핑 보여줘"]

EVIDENCE_TITLE = "🔎 근거 보기 — 어떤 데이터로 이렇게 답했나"
TECH_DETAIL_TITLE = "기술 상세"

# '오늘의 발견' 유형 → 쉬운 말 (숫자 규칙은 괄호로 뒤에 붙인다)
ANOMALY_PHRASE = {
    "급증": "평소보다 크게 늘었어요",
    "급감": "평소보다 크게 줄었어요",
    "최근 붐빔": "요즘 계속 평소보다 붐벼요",
    "최근 한산": "요즘 계속 평소보다 한산해요",
}

# ---------------- 내부 식별자 → 한국어 라벨 ----------------
# 근거 표·차트에 노출되는 영문 컬럼 키. 표시용 복사본만 바꾸며 원본 DataFrame은 건드리지 않는다.
COL_KO = {
    "rank": "순위", "ap_place": "장소", "category": "분류", "days": "관측 일수",
    "avg_daily_users": "일평균 이용자(명)", "avg_users": "일평균 이용자(명)", "user_count": "이용자 수(명)",
    "avg_congestion_index": "평균 혼잡지수", "peak_congestion_index": "최고 혼잡지수",
    "predicted_congestion_index": "예상 혼잡지수",
    "bus_station_name": "정류소", "bus_route_name": "노선", "bus_route_number": "노선 번호",
    "daily_boardings": "일평균 승차(명)", "boardings": "승차(명)", "avg_boardings": "평균 승차(명)",
    "vulnerable_share": "교통약자 비중", "senior_share": "어르신 비중", "weekend_ratio": "주말/평일 비율",
    "tourist_share": "관광객 비중", "foreign_share": "외국인 비중",
    "rain_days": "비 온 날수", "dry_days": "맑은 날수", "observatory_code": "관측소 코드",
    "weekday_dry_avg": "평일·맑은 날 평균(명)", "weekday_rain_avg": "평일·비 온 날 평균(명)", "weekday_rain_effect": "평일 강우 변화율",
    "weekend_dry_avg": "주말·맑은 날 평균(명)", "weekend_rain_avg": "주말·비 온 날 평균(명)", "weekend_rain_effect": "주말 강우 변화율",
    "basis_days": "근거 일수", "min_days": "최소 일수",
    "boardings_per_km": "km당 승차(명)", "boardings_per_station": "정류소당 승차(명)", "bus_route_length": "노선 길이(km)",
    "n_low_hours": "한산 시간대 수", "low_hours": "한산 시간대", "peak_hour": "최다 승차 시각",
    "bus_city_type": "지역 구분", "distance_km": "거리(km)",
    "date": "날짜", "dow": "요일", "ym": "월", "hour": "시각",
    "lat": "위도", "lon": "경도", "latitude": "위도", "longitude": "경도",
    "label": "대상", "value": "값",
}

# 영문 정렬 기준·대상 enum → 한국어 (describe_step 문구에 남는 내부 값 치환용)
ENUM_KO = {
    "congestion_index": "혼잡지수", "users": "이용자 수", "boardings": "승차 인원",
    "vulnerable_share": "교통약자 비중", "senior_share": "어르신 비중", "weekend_ratio": "주말/평일 비율",
    "bus_station": "버스 정류소", "bus_route": "버스 노선", "wifi": "와이파이 장소",
}


def korean_enums(text: str) -> str:
    """표시용 문장에 남은 영문 enum 값(정렬 기준 등)을 한국어로 치환한다. Agent가 만든 plan 문장에도 쓴다."""
    for en, ko in ENUM_KO.items():
        text = text.replace(f"정렬 {en}", f"정렬 {ko}")
    return text


def describe_step_ko(tool: str, args: dict) -> str:
    """describe_step 결과에서 영문 enum 값까지 한국어로 바꾼 표시용 한 줄."""
    return korean_enums(describe_step(tool, args))

DATASET_SHORT_KO = {  # 첫 화면 적재 안내용 짧은 이름 (핵심 데이터만)
    "wifi_ap_usage": "와이파이 접속", "bus_station_usage": "버스 정류소 승차",
    "bus_route_usage": "버스 노선 이용", "weather_daily": "날씨",
}

SOURCE_KO = {"raw": "실데이터", "synthetic": "개발용 합성", "missing": "미적재"}


def dataset_ko(key: str) -> str:
    """'wifi_ap_usage' → '4-05 와이파이 AP별 이용 현황'."""
    try:
        d = schemas.dataset(key)
        return f"{d['code']} {d['name']}"
    except Exception:  # noqa: BLE001
        return key


def dataset_line(db, key: str) -> str:
    """근거 보기용 한 줄: 이름 · 기간 · 행수 · 출처."""
    p = db.provenance.get(key)
    if p is None:
        return dataset_ko(key)
    period = f", {p.date_min} ~ {p.date_max}" if p.date_min else ""
    return f"{dataset_ko(key)} — {p.rows:,}행{period} · {SOURCE_KO.get(p.source, p.source)}"


def mode_ko(mode: str) -> str:
    if mode.startswith("llm:"):
        return "LLM 해설"
    if mode.startswith("offline(fallback"):
        return "오프라인 대체"
    return "오프라인"   # st.metric 값이 잘리지 않게 짧게 (규칙 기반이라는 설명은 사이드바에)


# ---------------- 숫자·표 서식 ----------------
def _short_end(dmin: str, dmax: str) -> str:
    """같은 해면 종료일의 연도를 줄인다. ~ 양옆 공백은 마크다운 취소선(~쌍) 오인 방지용."""
    if dmin and dmax and dmin[:4] == dmax[:4]:
        return f"{dmin} ~ {dmax[5:]}"
    return f"{dmin} ~ {dmax}"


def coverage_caption(db) -> str:
    """첫 줄 설명 밑에 붙는 실제 적재 범위. 하드코딩이 아니라 적재 결과(provenance)에서 읽는다."""
    bits = []
    for key, label in DATASET_SHORT_KO.items():
        p = db.provenance.get(key)
        if p is not None and p.date_min:
            bits.append(f"{label} {_short_end(p.date_min, p.date_max)}")
    cov = " · ".join(bits) if bits else "적재된 데이터가 없습니다"
    syn = " · ⚠️ 개발용 합성 데이터 포함" if db.uses_synthetic() else ""
    return f"적재 데이터: {cov}{syn} · {PRIVACY_NOTE}"


def data_range(db) -> tuple[str, str]:
    ps = [p for k, p in db.provenance.items() if p.date_min and k not in INFO_TABLES]
    return (min(p.date_min for p in ps), max(p.date_max for p in ps)) if ps else ("", "")


def no_result_text(db) -> str:
    dmin, dmax = data_range(db)
    period = f"지금은 **{dmin} ~ {dmax}** 기간의 와이파이·버스 데이터만 들어 있어요." if dmin else "지금은 적재된 데이터가 없어요."
    return ("이 질문에 맞는 집계 결과를 만들지 못했어요. 질문의 장소·기간이 데이터 밖이거나, 질문을 이해하지 못했을 수 있어요. "
            f"{period} 아래 버튼으로 다시 시작해 보세요.")


def unit_period_caption(r, db) -> str:
    """표·차트 밑에 붙는 '단위 · 기간' 설명."""
    ds = set(r.datasets)
    units = []
    if any(k.startswith("wifi") for k in ds):
        units.append("이용자 수는 명(와이파이 접속 기준)")
    if any(k.startswith("bus") for k in ds):
        units.append("승차 인원은 명")
    if "혼잡" in (r.title or "") or any("congestion" in str(c) for c in r.table.columns):
        units.append("혼잡지수는 단위 없는 상대값")
    bits = ["단위: " + ", ".join(units)] if units else []
    df_, dt_ = r.params.get("date_from"), r.params.get("date_to")
    if df_ or dt_:
        bits.append(f"기간: {df_ or '데이터 시작'} ~ {dt_ or '데이터 끝'}")
    else:
        ps = [db.provenance[k] for k in ds if k in db.provenance and db.provenance[k].date_min and k not in INFO_TABLES]
        if ps:
            bits.append(f"기간: {min(p.date_min for p in ps)} ~ {max(p.date_max for p in ps)}")
    return " · ".join(bits)


def _num(v, pct: bool = False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if pd.isna(f):
        return ""
    if pct:
        return f"{f:.1%}"
    if f == int(f) and abs(f) < 1e15:
        return f"{int(f):,}"
    return f"{f:,.1f}"


def styled(df: pd.DataFrame):
    """표시용 표: 컬럼명 한국어화 + 천 단위 구분 + 소수 1자리 + 비중은 %."""
    d = df.rename(columns={c: COL_KO.get(str(c), str(c)) for c in df.columns})
    fmts = {}
    for c in d.columns:
        if pd.api.types.is_numeric_dtype(d[c]):
            pct = ("비중" in str(c)) or ("변화율" in str(c))
            fmts[c] = (lambda p: (lambda v: _num(v, p)))(pct)
    return d.style.format(fmts, na_rep="")


def steps_df(steps) -> pd.DataFrame:
    """실행 로그 탭용: 내부 도구명·JSON 인자 대신 한국어 설명."""
    return pd.DataFrame([{
        "도구": TOOL_KO.get(s.tool, s.tool),
        "내용": describe_step_ko(s.tool, s.args),
        "성공": "✅" if s.ok else "❌",
        "소요(초)": s.seconds,
        "결과 행 수": s.rows,
        "반출 게이트": " / ".join(s.gate),
        "오류": s.error,
    } for s in steps])
