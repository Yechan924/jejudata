"""LLM 해설의 숫자가 근거(도구 결과 표·facts)에 있는지 검증한다.

원칙: 답변 속 숫자는 도구 결과에서 나와야 한다. 표에 없는 숫자가 있으면 그 사실을 기록하고,
비율이 높으면 해설을 템플릿으로 대체한다(환각 방지의 구조적 보장).
느슨한 규칙: 100 이하 정수(순위·개수·시각), 연도, 날짜 조각은 검사 제외. 단위 변환(천·만·%)을 허용한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .tools.base import ToolResult

_NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")


@dataclass
class VerifyResult:
    checked: int = 0
    unsupported: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.checked == 0 or len(self.unsupported) <= max(1, int(self.checked * 0.2))


def _pool(results: list[ToolResult]) -> set[float]:
    vals: set[float] = set()
    for r in results:
        for c in r.table.columns:
            s = pd.to_numeric(r.table[c], errors="coerce").dropna()
            for v in s.tolist():
                vals.add(round(float(v), 2))
        for f in r.facts:
            for m in _NUM.finditer(f):
                vals.add(round(float(m.group(0).replace(",", "")), 2))
    # 단위 변환 허용: ×100(비율→%), ÷1000, ÷10000
    extra = set()
    for v in list(vals):
        extra.update({round(v * 100, 2), round(v / 1000, 2), round(v / 10000, 2), round(v, 0), round(v, 1)})
    return vals | extra


def verify_numbers(answer: str, results: list[ToolResult]) -> VerifyResult:
    pool = _pool(results)
    out = VerifyResult()
    text = re.sub(r"\d{4}-\d{2}(-\d{2})?", " ", answer)  # 날짜 제거
    for m in _NUM.finditer(text):
        raw = m.group(0).replace(",", "")
        x = float(raw)
        if x <= 100 and x.is_integer():
            continue
        if re.fullmatch(r"(19|20)\d\d", raw):
            continue
        out.checked += 1
        cands = {round(x, 2), round(x, 1), round(x, 0)}
        if not any(abs(c - p) < 1e-6 or (p and abs(c - p) / max(abs(p), 1e-9) < 0.02) for c in cands for p in pool):
            out.unsupported.append(m.group(0))
    return out
