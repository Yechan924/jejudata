"""제주 혼잡·이동 진단 Agent — Streamlit UI

실행: streamlit run app.py
"""
from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import format as F
from jeju_agent.agent import JejuAgent
from jeju_agent.charts import render_png
from jeju_agent.config import ATTRIBUTION, RAW_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.llm import LLM
from jeju_agent.report import build_html

try:
    import plotly.express as px
except Exception:  # noqa: BLE001
    px = None
try:
    import pydeck as pdk
except Exception:  # noqa: BLE001
    pdk = None

st.set_page_config(page_title="제주 혼잡·이동 진단 Agent", page_icon="🍊", layout="wide")

EXAMPLES = [
    "눈에 띄는 게 있어?",
    "주말에 가장 붐비는 관광지는 어디야?",
    "외국인 관광객이 많이 가는 곳은?",
    "성산일출봉은 언제 가장 붐비나?",
    "해변 중에 주말에 가장 붐비는 곳은?",
    "비 오면 어디가 한산해지나?",
    "효율이 낮은 버스 노선은?",
    "251번 노선 시간대별 이용 현황 알려줘",
    "어르신이 많이 타는 정류소는?",
    "성산일출봉 가는 버스 정류소는 얼마나 붐비나?",
    "9월 전반과 후반에 어디가 늘고 줄었어?",
    "평일과 주말 차이가 큰 정류소는?",
    "이번 주말 어디가 붐빌까?",
    "9월 월간 브리핑 보여줘",
]


@st.cache_resource(show_spinner="데이터를 읽는 중…")
def get_db(_version: int) -> JejuDB:
    from jeju_agent.config import SYNTHETIC_DIR
    if not list(RAW_DIR.glob("**/*.csv")) and not (SYNTHETIC_DIR / "wifi_ap_usage.csv").exists():
        from datetime import date
        from jeju_agent.data.synthetic import generate
        generate(date(2025, 1, 1), date(2025, 6, 30), scale=0.3)   # 첫 실행(공개 데모 포함) 시 합성 데이터 자동 생성
    db = JejuDB()
    db.load_all()
    return db


def get_agent(offline: bool) -> JejuAgent:
    db = get_db(st.session_state.get("data_version", 0))
    llm = LLM("offline") if offline else LLM()
    return JejuAgent(db, llm)


# ---------------- 사이드바 ----------------
with st.sidebar:
    st.title("🍊 제주 혼잡·이동 진단")
    st.caption("공공 와이파이 = 혼잡 센서 · 티머니 버스 = 이동 수요 · 날씨")
    offline = st.toggle("오프라인 모드(LLM 없이 규칙 기반)", value=False, help="외부망이 없거나 API 키가 없을 때 켜세요. 자동 폴백도 됩니다.")
    agent = get_agent(offline)
    db = agent.db
    st.markdown(f"**분석 엔진** `{db.engine}` · **LLM** `{agent.llm.provider}`" + (f" `{agent.llm.model}`" if agent.llm.available else ""))
    if agent.llm.available:
        _ex = os.getenv("LLM_EXTRAS", "followups")
        c1, c2 = st.columns(2)
        fu = c1.checkbox("후속 질문 LLM 제안", value="followups" in _ex, help="답변 뒤 LLM이 결과를 보고 다음 질문을 제안(호출 1회 추가)")
        rv = c2.checkbox("답변 자기 점검", value="review" in _ex, help="LLM이 자기 답변을 근거표와 대조해 과장·인과 표현을 지적(호출 1회 추가)")
        os.environ["LLM_EXTRAS"] = ",".join([k for k, on in (("followups", fu), ("review", rv)) if on]) or "none"
    if db.uses_synthetic():
        st.warning("⚠️ 개발용 합성 데이터가 포함되어 있습니다. data/raw 에 실제 CSV를 넣고 '다시 읽기'를 누르세요.", icon="⚠️")
    with st.expander("적재 데이터", expanded=False):
        summ = db.summary()
        st.dataframe(summ[["code", "name", "source", "rows", "period"]], hide_index=True, use_container_width=True)
        notes = summ[summ["note"].astype(str).str.len() > 0]
        if len(notes):
            st.caption("헤더 매핑·기본값 채움 기록 (실데이터 점검용)")
            for r in notes.itertuples():
                st.caption(f"· {r.code}: {r.note}")
        st.caption(f"원본 CSV 폴더: `{RAW_DIR}` · 점검: `python scripts/check_data.py`")
        up = st.file_uploader("CSV 추가 (제주데이터허브에서 받은 파일)", type=["csv"], accept_multiple_files=True)
        if up:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            for f in up:
                (RAW_DIR / f.name).write_bytes(f.getbuffer())
            st.success(f"{len(up)}개 파일 저장. 아래 '다시 읽기'를 누르세요.")
        if st.button("올린 자료 모두 삭제", help="data/raw 의 CSV를 삭제합니다(센터 PC 퇴실 전 사용)"):
            n = 0
            for f in RAW_DIR.glob("**/*.csv"):
                f.unlink(); n += 1
            st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1
            st.cache_resource.clear()
            st.success(f"{n}개 파일 삭제"); st.rerun()
        if st.button("데이터 다시 읽기"):
            st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1
            st.cache_resource.clear()
            st.rerun()
    st.markdown("**예시 질문**")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            st.session_state["pending_q"] = ex
    if st.button("대화 지우기"):
        st.session_state["turns"] = []
        st.rerun()


# ---------------- 지도 ----------------
def render_map(points: list, height: int | None = None, caption: str = "원 크기 = 지표 값. 실제 위치는 와이파이 AP·정류소 좌표(명세서 기준)."):
    pts = [p for p in points if p is not None and not p.empty]
    if pts and pdk is not None:
        mp = pd.concat(pts, ignore_index=True)
        mp["lat"] = pd.to_numeric(mp["lat"], errors="coerce"); mp["lon"] = pd.to_numeric(mp["lon"], errors="coerce")
        mp["value"] = pd.to_numeric(mp["value"], errors="coerce").fillna(0)
        mp = mp.dropna(subset=["lat", "lon"])
        if mp.empty:
            st.info("이 결과에는 지도에 표시할 좌표가 없습니다."); return
        vmax = max(abs(mp["value"]).max(), 1e-9)
        mp["radius"] = 300 + 1500 * (abs(mp["value"]) / vmax)
        mp["color_r"] = (120 + 135 * (mp["value"].clip(lower=0) / vmax)).astype(int)
        layer = pdk.Layer("ScatterplotLayer", data=mp, get_position="[lon, lat]", get_radius="radius",
                          get_fill_color="[color_r, 90, 60, 160]", pickable=True)
        text = pdk.Layer("TextLayer", data=mp, get_position="[lon, lat]", get_text="label", get_size=12, get_color=[30, 30, 30], get_alignment_baseline="'bottom'")
        view = pdk.ViewState(latitude=float(mp["lat"].mean()), longitude=float(mp["lon"].mean()), zoom=9.3)
        deck = pdk.Deck(layers=[layer, text], initial_view_state=view, map_style=None, tooltip={"text": "{label}\n값: {value}"})
        try:
            if height:   # 주의: 삼항식(bare IfExp)으로 쓰면 streamlit magic이 st.write(DeltaGenerator)=st.help 표를 그려 버린다
                st.pydeck_chart(deck, height=height)
            else:
                st.pydeck_chart(deck)
        except TypeError:   # 구버전 streamlit: height 인자 없음
            st.pydeck_chart(deck)
        if caption:
            st.caption(caption)
    elif pts:
        mp = pd.concat(pts, ignore_index=True).rename(columns={"lat": "latitude", "lon": "longitude"})
        st.map(mp.dropna(subset=["latitude", "longitude"]))
    else:
        st.info("이 결과에는 지도에 표시할 좌표가 없습니다.")


# ---------------- 결과 렌더링 ----------------
def render_starters(key_prefix: str):
    """6개 시작 질문 버튼. 첫 화면과 빈 결과·오류 화면에서 같이 쓴다."""
    cols = st.columns(3)
    for j, q_ in enumerate(F.STARTERS):
        if cols[j % 3].button(q_, key=f"{key_prefix}_q_{j}", use_container_width=True):
            st.session_state["pending_q"] = q_; st.rerun()


def render_result(res, key_prefix: str):
    st.markdown(res.answer)
    for n in res.notes:
        st.info(n, icon="🔎")
    with st.expander(F.EVIDENCE_TITLE, expanded=False):
        if res.plan:
            st.markdown("**분석 과정** — Agent가 어떤 순서로 보았나")
            for i, p in enumerate(res.plan, 1):
                st.markdown(f"{i}. {F.korean_enums(p)}")
        if res.steps:
            st.markdown("**사용한 도구**")
            for s_ in res.steps:
                st.caption(("✅ " if s_.ok else "❌ ") + F.describe_step_ko(s_.tool, s_.args)
                           + f" · {s_.seconds}초 · {s_.rows:,}행" + (f" · 오류: {s_.error}" if s_.error else ""))
        used = sorted({d for r in res.results for d in r.datasets})
        if used:
            st.markdown("**사용한 데이터와 기간**")
            for k in used:
                st.caption("· " + F.dataset_line(db, k))
        if res.limitations:
            st.markdown("**알아 둘 한계** — 코드가 자동으로 확인한 내용")
            for x in res.limitations:
                st.markdown(f"- {x}")
    cols = st.columns(4)
    cols[0].metric("응답 방식", F.mode_ko(res.mode))
    cols[1].metric("도구 호출", f"{len(res.steps)}회")
    cols[2].metric("LLM 호출", f"{res.llm_calls}회")
    cols[3].metric("소요 시간", f"{res.elapsed:.1f}초")
    if res.followups:
        st.caption("이어서 물어보기")
        fcols = st.columns(len(res.followups))
        for j, f in enumerate(res.followups):
            if fcols[j].button(f, key=f"{key_prefix}_fu_{j}", use_container_width=True):
                st.session_state["pending_q"] = f
                st.rerun()
    if not res.results:
        st.info(F.no_result_text(db), icon="💡")
        render_starters(f"{key_prefix}_retry")
        return
    tabs = st.tabs(["🗺️ 지도", "📊 차트", "📋 근거 표", "🔍 방법·한계", "🧾 실행 로그", "📡 LLM 전송 내용"])
    with tabs[0]:
        render_map([r.map_points for r in res.results], height=None)
    with tabs[1]:
        for i, r in enumerate(res.results):
            if not r.chart or px is None:
                continue
            c, df = r.chart, r.table.copy()
            ys = c["y"] if isinstance(c["y"], list) else [c["y"]]
            for y in ys:
                df[y] = pd.to_numeric(df[y], errors="coerce")
            if c["x"] not in df.columns or any(y not in df.columns for y in ys):
                continue
            df = df.rename(columns={k: F.COL_KO.get(str(k), str(k)) for k in df.columns})   # 표시용 한국어 컬럼명
            x_ko, ys_ko = F.COL_KO.get(str(c["x"]), str(c["x"])), [F.COL_KO.get(str(y), str(y)) for y in ys]
            try:
                fig = px.bar(df, x=x_ko, y=ys_ko, title=c.get("title")) if c["type"] == "bar" else px.line(df, x=x_ko, y=ys_ko, title=c.get("title"), markers=True)
                fig.update_layout(height=380, margin=dict(l=10, r=10, t=40, b=10), xaxis_title=None,
                                  yaxis_title=ys_ko[0] if len(ys_ko) == 1 else None)
                st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart_{i}")
                st.caption(F.unit_period_caption(r, db))
            except Exception as e:  # noqa: BLE001
                st.caption(f"차트 생략: {e}")
            extra = getattr(r, "extra", None)
            if extra and px is not None and "by_dow" in extra:
                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(px.bar(extra["by_dow"], x="dow", y="avg_users", title="요일별 일평균 이용자(명)"), use_container_width=True, key=f"{key_prefix}_dow_{i}")
                with c2:
                    st.plotly_chart(px.line(extra["by_month"], x="ym", y="avg_users", title="월별 일평균 이용자(명)", markers=True), use_container_width=True, key=f"{key_prefix}_mon_{i}")
    with tabs[2]:
        for r in res.results:
            st.markdown(f"**{r.title}** · {len(r.table):,}행")
            st.caption("데이터: " + " · ".join(F.dataset_ko(d) for d in r.datasets) + " / " + F.unit_period_caption(r, db))
            st.dataframe(F.styled(r.table), hide_index=True, use_container_width=True)
    with tabs[3]:
        if res.limitations:
            st.markdown("**자동 점검** — 코드가 데이터 출처·기간·게이트·표본을 확인해 적은 한계")
            for x in res.limitations:
                st.markdown(f"- {x}")
            st.divider()
        for r in res.results:
            st.markdown(f"**{r.title}**\n- 방법: {r.method}\n- 인자: `{json.dumps(r.params, ensure_ascii=False)}`\n- 반출 게이트: {' / '.join(r.gate_log)}")
            for cv in r.caveats:
                st.markdown(f"  - ⚠️ {cv}")
    with tabs[4]:
        st.dataframe(F.steps_df(res.steps), hide_index=True, use_container_width=True)
        if res.tokens:
            st.caption(f"토큰: {res.tokens}")
        if res.verification:
            st.caption(f"답변 숫자 검증: {res.verification.get('checked', 0)}개 확인, 미확인 {len(res.verification.get('unsupported', []))}개")
    with tabs[5]:
        if not res.llm_transcript:
            st.info("이 답변은 LLM 호출 없이(오프라인 모드) 생성되어 외부로 전송된 내용이 없습니다.")
        else:
            st.caption("외부 LLM에 실제로 전송된 내용 전부입니다. 원본 행·식별자는 포함되지 않으며, 반출 게이트를 통과한 집계표(최대 40행)만 갑니다.")
            for m in res.llm_transcript:
                with st.expander(f"{m['direction']} · {m['kind']} ({len(m['content'])}자)", expanded=m["kind"] == "answer"):
                    st.code(m["content"][:6000] + ("…" if len(m["content"]) > 6000 else ""), language="markdown")
    c1, c2 = st.columns([1, 3])
    c1.download_button("📄 HTML 리포트(PNG 차트 내장)", build_html(res), file_name="jeju_report.html", mime="text/html", key=f"{key_prefix}_dl")
    pngs = [(r, render_png(r)) for r in res.results]
    pngs = [(r, p) for r, p in pngs if p]
    if pngs:
        c2.download_button("🖼️ 차트 PNG (반출용)", pngs[0][1], file_name=f"{pngs[0][0].name}.png", mime="image/png", key=f"{key_prefix}_png")


# ---------------- 메인 ----------------
st.markdown("### 제주 혼잡·이동 진단 Agent")
st.markdown(F.SERVICE_ONELINER)
st.caption(F.coverage_caption(db))
with st.expander("이 서비스는 무엇인가요?", expanded=False):
    st.markdown(F.ABOUT_MD)
turns = st.session_state.setdefault("turns", [])


@st.cache_resource(show_spinner="Agent가 데이터를 먼저 둘러보는 중…")
def get_home(_version: int):
    """첫 화면용 자율 탐색 — LLM 없이(빠르고 결정적) 혼잡 상위 5곳 + 오늘의 발견 3건."""
    return JejuAgent(get_db(_version), LLM("offline")).discover(top_n=3)


def render_home():
    """질문 전 첫 화면: 비전문가가 뭘 물어야 할지 몰라도 바로 쓸 수 있게 Agent가 먼저 보여 준다."""
    try:
        home = get_home(st.session_state.get("data_version", 0))
    except Exception as e:  # noqa: BLE001
        st.info(f"첫 화면 탐색을 건너뜁니다({type(e).__name__}). 아래 질문으로 시작하세요."); home = None
    rank = next((r for r in (home.results if home else []) if r.name == "congestion_rank"), None)
    disc = next((r for r in (home.results if home else []) if r.name == "explore_anomalies"), None)
    dmin, dmax = agent._data_range()
    st.markdown("#### 지금 제주는")
    st.caption(f"{dmin} ~ {dmax} 데이터를 Agent가 질문 없이 먼저 살펴본 결과예요.")
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("**🗺️ 지금 가장 붐비는 곳 — 상위 5곳**")
        st.caption("공공 와이파이 일평균 이용자(명) 기준. 원이 클수록 붐빕니다.")
        if rank is not None and len(rank.table):
            render_map([rank.map_points], height=330, caption="")
            chips = st.columns(len(rank.table))
            for j, row in enumerate(rank.table.itertuples()):
                if chips[j].button(f"{row.rank}. {row.ap_place}", key=f"home_rank_{j}", help=f"일평균 {row.avg_daily_users:,.0f}명", use_container_width=True):
                    st.session_state["pending_q"] = f"{row.ap_place} 언제 가장 붐비나?"; st.rerun()
        else:
            st.caption("혼잡 순위를 만들 데이터가 부족합니다.")
    with right:
        st.markdown("**🔎 오늘의 발견 — 평소와 다른 변화**")
        st.caption("평소 = 같은 장소·정류소의 평일/주말별 중앙값이에요.")
        if disc is not None and len(disc.table):
            qs = (getattr(disc, "extra", None) or {}).get("questions") or []
            icons = {"급증": "📈", "급감": "📉", "최근 붐빔": "🔥", "최근 한산": "🧊"}
            for j, row in enumerate(disc.table.itertuples()):
                with st.container(border=True):
                    st.markdown(f"{icons.get(row.유형, '•')} **{row.구분} · {row.대상}** — "
                                f"{F.ANOMALY_PHRASE.get(row.유형, row.유형)} (평소 대비 {row.배율}배)")
                    st.caption(row.설명)
                    if j < len(qs) and st.button("자세히 보기 →", key=f"home_disc_{j}"):
                        st.session_state["pending_q"] = qs[j]; st.rerun()
            st.caption("원인(행사·날씨·수집 공백)은 데이터에 없으므로 현장 확인이 필요해요. 더 보려면 '눈에 띄는 게 있어?'라고 물어보세요.")
        else:
            st.caption("기준에 걸리는 변화가 없습니다.")
    st.markdown("**💬 이렇게 물어보세요**")
    render_starters("home")
    if home is not None:
        with st.expander(F.EVIDENCE_TITLE + " (첫 화면)", expanded=False):
            st.markdown(home.answer)


if not turns and not st.session_state.get("pending_q"):
    render_home()
for i, t in enumerate(turns):
    with st.chat_message("user"):
        st.write(t["q"])
    with st.chat_message("assistant"):
        render_result(t["res"], f"t{i}")

q = st.chat_input("예: 주말에 가장 붐비는 관광지는?") or st.session_state.pop("pending_q", None)
if q:
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        res, err = None, ""
        with st.status("분석 중 — 질문 해석 → 데이터 조회 → 결과 정리", expanded=True) as box:
            st.write("질문을 해석하고 필요한 데이터를 조회하는 중…")
            history = []
            for t in turns[-3:]:   # 최근 3턴을 맥락으로 전달 ("거기 가는 버스는?" 같은 후속 질문 해석용)
                history += [{"role": "user", "content": t["q"]}, {"role": "assistant", "content": t["res"].answer[:1500]}]
            try:
                res = agent.run(q, history=history)
                for s_ in res.steps:
                    st.write(("✅ " if s_.ok else "⚠️ ") + F.describe_step_ko(s_.tool, s_.args))
                st.write("결과 정리 완료")
                box.update(label=f"분석 완료 ({res.elapsed:.1f}초)", state="complete", expanded=False)
            except Exception:  # noqa: BLE001
                err = traceback.format_exc()
                box.update(label="분석에 실패했습니다", state="error", expanded=False)
        if res is None:
            st.warning(F.no_result_text(db), icon="🙏")
            with st.expander(F.TECH_DETAIL_TITLE, expanded=False):
                st.code(err)
            render_starters("err")
        else:
            render_result(res, f"t{len(turns)}")
            turns.append({"q": q, "res": res})

st.divider()
st.caption(ATTRIBUTION + " · 데이터: 제주데이터허브(티머니, 농업기술원 등)")
