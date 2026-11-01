"""도구 결과 → PNG 차트 (matplotlib, 외부 서비스 없음).

센터 반출 정책(2026.09.01)은 시각화를 PNG/JPG 그림파일로만 반출하도록 하므로, 리포트에는 PNG를 내장하고
outputs/charts/ 에도 저장한다. 화면의 Plotly 인터랙티브 차트는 센터 안에서만 보는 용도.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pandas as pd

from .tools.base import ToolResult

_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]
_PALETTE = ["#2f6f8f", "#e07b39", "#6aa36f", "#8a8a8a"]


def _setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in _FONT_CANDIDATES:
        if Path(f).exists():
            fp = fm.FontProperties(fname=f)
            plt.rcParams["font.family"] = fp.get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def render_png(r: ToolResult, max_items: int = 15) -> bytes | None:
    """ToolResult.chart 스펙으로 PNG 바이트를 만든다. 그릴 수 없으면 None."""
    if not r.chart:
        return None
    try:
        plt = _setup()
        c, df = r.chart, r.table.copy()
        ys = c["y"] if isinstance(c["y"], list) else [c["y"]]
        if c["x"] not in df.columns or any(y not in df.columns for y in ys):
            return None
        for y in ys:
            df[y] = pd.to_numeric(df[y], errors="coerce")
        df = df.dropna(subset=ys, how="all")
        fig, ax = plt.subplots(figsize=(7.5, 3.4))
        if c["type"] == "bar":
            d = df.head(max_items).iloc[::-1]
            labels = d[c["x"]].astype(str).str.slice(0, 18)
            ax.barh(labels, d[ys[0]], color=_PALETTE[0])
            ax.set_xlabel(ys[0])
        else:
            for i, y in enumerate(ys):
                ax.plot(df[c["x"]], df[y], marker="o", ms=3, color=_PALETTE[i % 4], label=y)
            if len(ys) > 1:
                ax.legend(fontsize=8)
            ax.set_xlabel(c["x"])
            if len(df) > 20:
                ax.tick_params(axis="x", labelrotation=45, labelsize=7)
        ax.set_title(c.get("title", r.title), fontsize=11)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140)
        plt.close(fig)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def png_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def save_png(png: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path
