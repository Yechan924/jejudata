"""분석 결과를 단일 HTML 리포트로 저장 (외부 의존성 없음, 반출 게이트 통과본만 포함)."""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from .agent import AgentResult, JejuAgent
from .charts import png_data_uri, render_png
from .config import ATTRIBUTION, OUTPUT_DIR
from .tools.base import df_to_md

CSS = """
body{font-family:-apple-system,'Malgun Gothic','Apple SD Gothic Neo',sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#1a1a1a;line-height:1.55}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:1.8rem;border-bottom:1px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse;font-size:.85rem;margin:8px 0}td,th{border:1px solid #ddd;padding:4px 8px;text-align:right}th{background:#f4f4f4}td:first-child,th:first-child{text-align:left}
.warn{background:#fff4e5;border-left:4px solid #f0a000;padding:8px 12px}.meta{color:#666;font-size:.85rem}
pre{background:#f7f7f7;padding:8px;font-size:.8rem;overflow:auto}
"""


def _md_table_to_html(md: str) -> str:
    rows = [r for r in md.splitlines() if r.startswith("|")]
    if len(rows) < 2:
        return ""
    def cells(r):
        return [c.strip() for c in r.strip("|").split("|")]
    out = ["<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cells(rows[0])) + "</tr></thead><tbody>"]
    for r in rows[2:]:
        out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells(r)) + "</tr>")
    return "".join(out) + "</tbody></table>"


def _md_to_html(md: str) -> str:
    """아주 작은 마크다운 변환기(굵게, 글머리표, 줄바꿈)."""
    import re
    lines, out, in_ul = md.splitlines(), [], False
    for ln in lines:
        ln = html.escape(ln)
        ln = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", ln)
        if ln.startswith("- "):
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{ln[2:]}</li>")
        else:
            if in_ul:
                out.append("</ul>"); in_ul = False
            out.append(f"<p>{ln}</p>" if ln.strip() else "")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def build_html(res: AgentResult) -> str:
    parts = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>제주 혼잡·이동 진단 리포트</title><style>{CSS}</style></head><body>",
             "<h1>제주 혼잡·이동 진단 Agent — 분석 리포트</h1>",
             f"<p class='meta'>생성 {datetime.now():%Y-%m-%d %H:%M} · 모드 {html.escape(res.mode)} · 도구 {len(res.steps)}회 · 소요 {res.elapsed}s</p>"]
    if res.synthetic:
        parts.append("<p class='warn'>⚠️ 이 리포트는 개발용 합성 데이터로 생성되었습니다. 실제 제주 데이터가 아닙니다.</p>")
    parts.append(f"<h2>질문</h2><p>{html.escape(res.question)}</p><h2>답변</h2>{_md_to_html(res.answer)}")
    for i, r in enumerate(res.results, 1):
        parts.append(f"<h2>근거 {i}. {html.escape(r.title)}</h2>")
        parts.append(f"<p class='meta'>도구 {r.name} · 데이터 {', '.join(r.datasets)} · 인자 {html.escape(str(r.params))}<br>방법: {html.escape(r.method)}<br>반출 게이트: {html.escape(' / '.join(r.gate_log))}</p>")
        png = render_png(r)
        if png:
            parts.append(f"<img src='{png_data_uri(png)}' style='max-width:100%;border:1px solid #eee'>")
        parts.append(_md_table_to_html(df_to_md(r.table, 60)))
        if r.caveats:
            parts.append("<p class='meta'>주의: " + " / ".join(html.escape(c) for c in r.caveats) + "</p>")
    if res.notes:
        parts.append("<h2>검증 메모</h2><ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in res.notes) + "</ul>")
    parts.append("<h2>실행 로그</h2><pre>" + html.escape("\n".join(f"{s.tool} {s.args} → {'OK' if s.ok else 'ERR ' + s.error} ({s.seconds}s, {s.rows}행)" for s in res.steps)) + "</pre>")
    parts.append(f"<p class='meta'>{html.escape(ATTRIBUTION)}<br>데이터 출처: 제주특별자치도 · 제주빅데이터센터/제주데이터허브(티머니, 농업기술원 등). 본 리포트의 모든 수치는 집계값이며 개인 식별 정보를 포함하지 않습니다. 차트는 PNG로 내장되어 있습니다.</p></body></html>")
    return "\n".join(parts)


def save_report(res: AgentResult, path: Path | None = None) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = path or OUTPUT_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.html"
    path.write_text(build_html(res), encoding="utf-8")
    return path
