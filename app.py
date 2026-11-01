"""제주 혼잡·이동 진단 Agent — Streamlit UI

실행: streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

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
    "주말에 가장 붐비는 관광지는 어디야?",
    "외국인 관광객이 많이 가는 곳은?",
    "성산일출봉은 언제 가장 붐비나?",
    "비 오면 어디가 한산해지나?",
    "효율이 낮은 버스 노선은?",
    "251번 노선 시간대별 이용 현황 알려줘",
    "어르신이 많이 타는 정류소는?",
    "성산일출봉 가는 버스 정류소는 얼마나 붐비나?",
    "이번 주말 어디가 붐빌까?",
    "5월 월간 브리핑 보여줘",
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
    if db.uses_synthetic():
        st.warning("⚠️ 개발용 합성 데이터가 포함되어 있습니다. data/raw 에 실제 CSV를 넣고 '다시 읽기'를 누르세요.", icon="⚠️")
    with st.expander("적재 데이터", expanded=False):
        st.dataframe(db.summary()[["code", "name", "source", "rows", "period"]], hide_index=True, use_container_width=True)
        st.caption(f"원본 CSV 폴더: `{RAW_DIR}`")
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


# ---------------- 결과 렌더링 ----------------
def render_result(res, key_prefix: str):
    st.markdown(res.answer)
    cols = st.columns(4)
    cols[0].metric("모드", res.mode.split("/")[0])
    cols[1].metric("도구 호출", len(res.steps))
    cols[2].metric("LLM 호출", res.llm_calls)
    cols[3].metric("소요(초)", res.elapsed)
    if not res.results:
        return
    if res.notes:
        for n in res.notes:
            st.info(n, icon="🔎")
    tabs = st.tabs(["🗺️ 지도", "📊 차트", "📋 근거 표", "🔍 방법·한계", "🧾 실행 로그", "📡 LLM 전송 내용"])
    with tabs[0]:
        pts = [r.map_points for r in res.results if r.map_points is not None and not r.map_points.empty]
        if pts and pdk is not None:
            mp = pd.concat(pts, ignore_index=True)
            mp["lat"] = pd.to_numeric(mp["lat"], errors="coerce"); mp["lon"] = pd.to_numeric(mp["lon"], errors="coerce")
            mp["value"] = pd.to_numeric(mp["value"], errors="coerce").fillna(0)
            mp = mp.dropna(subset=["lat", "lon"])
            vmax = max(abs(mp["value"]).max(), 1e-9)
            mp["radius"] = 300 + 1500 * (abs(mp["value"]) / vmax)
            mp["color_r"] = (120 + 135 * (mp["value"].clip(lower=0) / vmax)).astype(int)
            layer = pdk.Layer("ScatterplotLayer", data=mp, get_position="[lon, lat]", get_radius="radius",
                              get_fill_color="[color_r, 90, 60, 160]", pickable=True)
            text = pdk.Layer("TextLayer", data=mp, get_position="[lon, lat]", get_text="label", get_size=12, get_color=[30, 30, 30], get_alignment_baseline="'bottom'")
            view = pdk.ViewState(latitude=float(mp["lat"].mean()), longitude=float(mp["lon"].mean()), zoom=9.3)
            st.pydeck_chart(pdk.Deck(layers=[layer, text], initial_view_state=view, map_style=None, tooltip={"text": "{label}\n값: {value}"}))
            st.caption("원 크기 = 지표 값. 실제 위치는 와이파이 AP·정류소 좌표(명세서 기준).")
        elif pts:
            mp = pd.concat(pts, ignore_index=True).rename(columns={"lat": "latitude", "lon": "longitude"})
            st.map(mp.dropna(subset=["latitude", "longitude"]))
        else:
            st.info("이 결과에는 지도에 표시할 좌표가 없습니다.")
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
            try:
                fig = px.bar(df, x=c["x"], y=ys, title=c.get("title")) if c["type"] == "bar" else px.line(df, x=c["x"], y=ys, title=c.get("title"), markers=True)
                fig.update_layout(height=380, margin=dict(l=10, r=10, t=40, b=10), xaxis_title=None)
                st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_chart_{i}")
            except Exception as e:  # noqa: BLE001
                st.caption(f"차트 생략: {e}")
            extra = getattr(r, "extra", None)
            if extra and px is not None:
                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(px.bar(extra["by_dow"], x="dow", y="avg_users", title="요일별 일평균"), use_container_width=True, key=f"{key_prefix}_dow_{i}")
                with c2:
                    st.plotly_chart(px.line(extra["by_month"], x="ym", y="avg_users", title="월별 일평균", markers=True), use_container_width=True, key=f"{key_prefix}_mon_{i}")
    with tabs[2]:
        for r in res.results:
            st.markdown(f"**{r.title}** · 데이터: `{'`, `'.join(r.datasets)}` · {len(r.table)}행")
            st.dataframe(r.table, hide_index=True, use_container_width=True)
    with tabs[3]:
        for r in res.results:
            st.markdown(f"**{r.title}**\n- 방법: {r.method}\n- 인자: `{json.dumps(r.params, ensure_ascii=False)}`\n- 반출 게이트: {' / '.join(r.gate_log)}")
            for cv in r.caveats:
                st.markdown(f"  - ⚠️ {cv}")
    with tabs[4]:
        st.dataframe(pd.DataFrame([s.__dict__ for s in res.steps]), hide_index=True, use_container_width=True)
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
st.caption("질문 한 줄 → 집계 → 지도·차트 → 쉬운 해설. 모든 결과는 집계값이며 개인정보를 포함하지 않습니다. 질문에 개인정보를 적지 마세요.")
turns = st.session_state.setdefault("turns", [])
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
        with st.spinner("분석 중…"):
            res = agent.run(q)
        render_result(res, f"t{len(turns)}")
    turns.append({"q": q, "res": res})

st.divider()
st.caption(ATTRIBUTION + " · 데이터: 제주데이터허브(티머니, 농업기술원 등)")
