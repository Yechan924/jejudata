"""Agent 오케스트레이터.

흐름: 질문 → (LLM 플래너 | 규칙 플래너) → 도구 호출 → 반출 게이트 → (LLM 해설 | 템플릿 해설) → AgentResult
LLM은 원본 행을 절대 보지 않는다. 도구가 만든 집계표(게이트 통과본)만 텍스트로 전달된다.
"""
from __future__ import annotations

import json
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import schemas
from .config import ATTRIBUTION, LLM_MAX_TOOL_CALLS
from .data.loader import INFO_TABLES, JejuDB
from .gate import apply_gate
from .insight import auto_limitations, describe_step, suggest_followups
from .llm import LLM
from .planner_rules import parse_period, plan as rule_plan, template_answer
from .tools import ToolResult, call_tool, tool_specs
from .verify import verify_numbers

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


@dataclass
class StepLog:
    tool: str
    args: dict
    ok: bool
    seconds: float
    rows: int = 0
    gate: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class AgentResult:
    question: str
    answer: str
    results: list[ToolResult]
    steps: list[StepLog]
    mode: str                      # "llm:<provider>/<model>" | "offline"
    llm_calls: int = 0
    tokens: dict = field(default_factory=dict)
    elapsed: float = 0.0
    synthetic: bool = False
    llm_transcript: list[dict] = field(default_factory=list)   # LLM에 실제로 보낸/받은 내용 (미리보기용)
    verification: dict = field(default_factory=dict)             # 답변 숫자 검증 결과
    notes: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)                # Agent가 세운 실행 계획(사람이 읽는 문장)
    limitations: list[str] = field(default_factory=list)         # 코드가 자동으로 모은 데이터·방법 한계
    followups: list[str] = field(default_factory=list)           # 다음 질문 제안


class JejuAgent:
    def __init__(self, db: JejuDB | None = None, llm: LLM | None = None):
        self.db = db or JejuDB()
        if not self.db.frames:
            self.db.load_all()
        self.llm = llm or LLM()

    # ---------- 공통 ----------
    def _system_prompt(self) -> str:
        prov = self.db.summary()
        prov_txt = "\n".join(f"- {r.key}: {r.source}, {r.rows:,}행, {r.period}" + (f" ({r.note})" if r.note else "") for r in prov.itertuples())
        if self.db.uses_synthetic():
            prov_txt += "\n⚠️ 'synthetic'은 명세서 구조만 따른 개발용 가짜 데이터입니다. 답변 '한계'에 이 사실을 반드시 적으세요."
        compact = self.llm.provider == "ollama" or os.getenv("LLM_COMPACT_PROMPT", "0") == "1"   # 로컬 소형 모델: 컨텍스트 절약
        return PROMPT_PATH.read_text(encoding="utf-8").format(datasets=schemas.describe_for_llm(compact=compact), provenance=prov_txt)

    def _run_tool(self, name: str, args: dict, steps: list[StepLog]) -> ToolResult | None:
        t0 = time.time()
        try:
            r = call_tool(self.db, name, args)
            g = apply_gate(r)
            r.table, r.gate_log = g.table, g.log
            steps.append(StepLog(name, args, True, round(time.time() - t0, 2), len(r.table), g.log))
            return r
        except Exception as e:  # noqa: BLE001
            steps.append(StepLog(name, args, False, round(time.time() - t0, 2), error=f"{type(e).__name__}: {e}"))
            return None

    def _names(self):
        places = sorted(self.db.table("wifi_ap_usage")["ap_place"].dropna().unique().tolist()) if self.db.has("wifi_ap_usage") else []
        stations = sorted(self.db.table("bus_station_usage")["bus_station_name"].dropna().unique().tolist()) if self.db.has("bus_station_usage") else []
        routes = sorted(self.db.table("bus_route_usage")["bus_route_name"].dropna().unique().tolist()) if self.db.has("bus_route_usage") else []
        return places, stations, routes

    def _caps(self) -> dict:
        """적재 데이터로 가능한 분석 능력 (집계본이면 국적·시간대 없음)."""
        caps = {"nationality": True, "hour": True, "weather": self.db.has("weather_daily")}
        if self.db.has("wifi_ap_usage"):
            u = self.db.table("wifi_ap_usage")
            caps["nationality"] = bool(u["nationality"].notna().any() and u["nationality"].nunique() > 1)
        if self.db.has("bus_station_usage"):
            caps["hour"] = bool(self.db.table("bus_station_usage")["hour"].notna().any())
        return caps

    def _data_range(self) -> tuple[str, str]:
        ps = [p for k, p in self.db.provenance.items() if p.date_min and k not in INFO_TABLES]
        return (min(p.date_min for p in ps), max(p.date_max for p in ps)) if ps else ("", "")

    # ---------- 오프라인(규칙) 모드 ----------
    def run_offline(self, question: str, history: list[dict] | None = None) -> AgentResult:
        t0 = time.time()
        places, stations, routes = self._names()
        dmin, dmax = self._data_range()
        steps: list[StepLog] = []
        results: list[ToolResult] = []
        plan_txt: list[str] = []
        q_eff = question
        if history and any(k in question.replace(" ", "") for k in ("거기", "그곳", "그장소", "그노선", "그정류소", "그정류장", "여기")):
            # 간단한 지시어 해석: 직전 대화에 나온 장소·정류소·노선명을 질문에 보탠다
            from .planner_rules import fuzzy_match_any
            prev = " ".join(m.get("content", "") for m in history[-2:]).replace(" ", "")
            ref = fuzzy_match_any(prev, places) or fuzzy_match_any(prev, stations) or fuzzy_match_any(prev, routes)
            if ref:
                q_eff = f"{ref} {question}"
                plan_txt.append(f"지시어를 직전 대화의 '{ref}'(으)로 해석")
        caps = self._caps()
        planned = rule_plan(q_eff, places, stations, routes, dmin, dmax, caps)
        qn = question.replace(" ", "")
        if not caps["nationality"] and any(k in qn for k in ("외국인", "중국인", "해외")):
            plan_txt.append("적재 데이터에 국적 정보가 없어(허브 집계본) 전체 이용자 기준 순위로 대체")
        if not caps["hour"] and any(k in qn for k in ("몇시", "시간대", "출근", "퇴근")):
            plan_txt.append("적재 데이터에 시간대가 없어(허브 집계본) 일 단위 승차 순위로 대체")
        if not caps["weather"] and any(n == "weather_effect" for n, _ in planned):
            note = self.db.provenance["weather_daily"].note if "weather_daily" in self.db.provenance else ""
            plan_txt.append("날씨 데이터(5-02)가 적재되지 않아 강우 영향 분석을 실행하지 않음")
            ans = ("**한 줄 결론** — 날씨 데이터가 없어 강우 영향을 계산할 수 없습니다.\n\n**근거**\n- " + (note or "weather_daily 미적재") +
                   "\n\n**해석과 제안**\n- `python scripts/fetch_weather.py --kma-csv <기상청 CSV>` 또는 `--open-meteo --from 2025-09-01 --to 2025-09-30` 으로 날씨를 받아 넣은 뒤 '다시 읽기' 하면 이 질문이 동작합니다.\n"
                   "- 날씨 없이 볼 수 있는 질문: '주말에 가장 붐비는 관광지는?', '효율이 낮은 버스 노선은?'\n\n**한계**\n- 강우일/비강우일 비교는 일강수량(관측소 4곳) 기준의 단순 비교입니다.\n\n" + ATTRIBUTION)
            res = AgentResult(question, ans, [], steps, "offline", elapsed=round(time.time() - t0, 2), synthetic=self.db.uses_synthetic())
            res.plan, res.limitations, res.followups = plan_txt, ["날씨 데이터 미적재"], ["주말에 가장 붐비는 관광지는?", "효율이 낮은 버스 노선은?", "어떤 데이터가 있어?"]
            return res
        plan_txt.append("규칙 플래너: 질문의 키워드로 도구를 골랐습니다 → " + " → ".join(describe_step(n, a) for n, a in planned))
        for name, args in planned:
            if args.get("place") == "__TOP1__":
                if not results or results[0].table.empty:
                    continue
                args = {**args, "place": str(results[0].table.iloc[0].get("ap_place", ""))}
                plan_txt.append(f"1위 장소 '{args['place']}'를 다음 단계 입력으로 사용")
            r = self._run_tool(name, args, steps)
            if r is not None:
                results.append(r)
        lim = auto_limitations(self.db, results, None, parse_period(question, dmin, dmax))
        ans = template_answer(question, results, self.db.uses_synthetic(), lim) + "\n\n" + ATTRIBUTION
        res = AgentResult(question, ans, results, steps, "offline", elapsed=round(time.time() - t0, 2), synthetic=self.db.uses_synthetic())
        res.plan, res.limitations, res.followups = plan_txt, lim, suggest_followups(question, results, self.db)
        return res

    # ---------- LLM 모드 ----------
    def run(self, question: str, history: list[dict] | None = None) -> AgentResult:
        if not self.llm.available:
            return self.run_offline(question, history)
        t0 = time.time()
        steps: list[StepLog] = []
        results: list[ToolResult] = []
        messages = list(history or []) + [{"role": "user", "content": question}]
        system = self._system_prompt()
        tools = tool_specs()
        calls, usage = 0, {"input": 0, "output": 0}
        transcript: list[dict] = [{"direction": "→LLM", "kind": "system", "content": system},
                                  {"direction": "→LLM", "kind": "user", "content": question}]
        plan_txt: list[str] = []
        try:
            for _ in range(LLM_MAX_TOOL_CALLS + 1):
                rep = self.llm.chat(system, messages, tools)
                calls += 1
                u = rep.usage or {}
                usage["input"] += u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
                usage["output"] += u.get("output_tokens", u.get("completion_tokens", 0)) or 0
                if not rep.tool_calls:
                    transcript.append({"direction": "←LLM", "kind": "answer", "content": rep.text})
                    return self._finish(question, rep.text, results, steps, calls, usage, t0, transcript, plan_txt)
                if rep.text and rep.text.strip():
                    plan_txt.append(rep.text.strip())
                plan_txt.append("도구 호출: " + " · ".join(describe_step(tc.name, tc.args) for tc in rep.tool_calls))
                transcript.append({"direction": "←LLM", "kind": "tool_calls", "content": json.dumps([{"name": tc.name, "args": tc.args} for tc in rep.tool_calls], ensure_ascii=False)})
                messages.append({"role": "assistant", "content": rep.text, "tool_calls": [{"id": tc.id, "name": tc.name, "args": tc.args} for tc in rep.tool_calls]})
                for tc in rep.tool_calls:
                    r = self._run_tool(tc.name, tc.args, steps)
                    if r is not None:
                        results.append(r)
                        content = r.to_llm_text()
                    else:
                        content = f"도구 오류: {steps[-1].error}. 인자를 수정해 다시 시도하거나 다른 도구를 쓰세요."
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
                    transcript.append({"direction": "→LLM", "kind": f"tool_result:{tc.name}", "content": content})
            # 도구 호출 한도 초과 → 지금까지 결과로 마무리
            messages.append({"role": "user", "content": "도구 호출 한도에 도달했습니다. 지금까지의 결과만으로 형식에 맞춰 최종 답변을 작성하세요."})
            rep = self.llm.chat(system, messages, None)
            calls += 1
            transcript.append({"direction": "←LLM", "kind": "answer", "content": rep.text})
            plan_txt.append("도구 호출 한도 도달 → 지금까지 결과로 마무리")
            return self._finish(question, rep.text, results, steps, calls, usage, t0, transcript, plan_txt)
        except Exception as e:  # noqa: BLE001  LLM 장애 → 오프라인 폴백
            steps.append(StepLog("llm", {}, False, 0, error=f"{type(e).__name__}: {e}"))
            off = self.run_offline(question, history)
            off.steps = steps + off.steps
            off.mode = f"offline(fallback from {self.llm.provider}: {type(e).__name__})"
            off.llm_calls = calls
            off.plan = plan_txt + [f"LLM 장애({type(e).__name__}) → 규칙 플래너로 대체"] + off.plan
            return off

    # ---------- LLM 부가 단계 (후속 질문·자기 점검) ----------
    FOLLOWUP_SYS = ("당신은 제주 관광·교통 데이터 분석 도우미입니다. 사용자의 질문과 방금 나온 분석 결과를 보고, 사용자가 '다음으로' 물어볼 만한 "
                    "짧은 한국어 질문 3개를 제안합니다. 결과에 나온 장소·정류소·노선 이름을 그대로 써서 구체적으로. 아래 분석 종류 안에서만: "
                    "장소 혼잡 순위/특정 장소 추이/장소 주변 정류소/정류소 승차 순위(교통약자)/노선 효율/강우 영향/주말 예측/기간·평일주말 비교/월간 브리핑. "
                    "JSON 문자열 배열만 출력하세요. 예: [\"성산일출봉은 언제 가장 붐비나?\", \"...\", \"...\"]")
    REVIEW_SYS = ("당신은 데이터 분석 결과를 검토하는 감수자입니다. '근거 사실'과 '답변'을 대조해 (1) 사실과 어긋나거나 과장된 문장, (2) 상관을 인과로 말한 표현, "
                  "(3) 표본·기간·방법 때문에 독자가 오해할 수 있는 점을 찾습니다. 문제가 없으면 빈 배열. 각 항목은 60자 이내 한국어 한 문장. "
                  "JSON 객체만 출력: {\"issues\": [\"...\"]}")

    @staticmethod
    def _extras() -> set[str]:
        return {e.strip() for e in os.getenv("LLM_EXTRAS", "followups").split(",") if e.strip()}

    @staticmethod
    def _json_in(text: str):
        m = re.search(r"[\[{].*[\]}]", text or "", re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    def _llm_followups(self, question: str, results: list[ToolResult], transcript: list[dict]) -> list[str] | None:
        facts = "\n".join(f"- {f}" for r in results for f in r.facts)[:3000]
        user = f"질문: {question}\n분석 결과 사실:\n{facts}\n\n다음 질문 3개를 JSON 배열로."
        rep = self.llm.chat(self.FOLLOWUP_SYS, [{"role": "user", "content": user}], None, max_tokens=300)
        transcript.append({"direction": "←LLM", "kind": "followups", "content": rep.text})
        arr = self._json_in(rep.text)
        if isinstance(arr, list):
            out = [str(x).strip() for x in arr if isinstance(x, str) and 4 <= len(str(x).strip()) <= 80]
            if len(out) >= 2:
                return out[:4]
        return None

    def _llm_review(self, question: str, answer: str, results: list[ToolResult], transcript: list[dict]) -> list[str]:
        facts = "\n".join(f"- {f}" for r in results for f in r.facts + r.caveats)[:3000]
        user = f"질문: {question}\n\n근거 사실:\n{facts}\n\n답변:\n{answer[:2500]}\n\n검토 결과를 JSON 으로."
        rep = self.llm.chat(self.REVIEW_SYS, [{"role": "user", "content": user}], None, max_tokens=400)
        transcript.append({"direction": "←LLM", "kind": "review", "content": rep.text})
        obj = self._json_in(rep.text)
        if isinstance(obj, dict) and isinstance(obj.get("issues"), list):
            return [str(x).strip() for x in obj["issues"] if isinstance(x, str) and x.strip()][:3]
        return []

    def _finish(self, question, text, results, steps, calls, usage, t0, transcript, plan_txt=None) -> AgentResult:
        """LLM 답변 마무리: 숫자 검증 → 실패 시 템플릿 대체 → (LLM 자기 점검·후속 질문) → 자동 점검·출처 문구 부착."""
        notes: list[str] = []
        answer = (text or "").strip()
        v = verify_numbers(answer, results) if answer else None
        verification = {"checked": v.checked, "unsupported": v.unsupported} if v else {}
        dmin, dmax = self._data_range()
        lim = auto_limitations(self.db, results, verification, parse_period(question, dmin, dmax) if dmin else None)
        extras = self._extras() if results else set()
        plan_txt = list(plan_txt or [])
        if not answer or (v and not v.ok):
            if v and not v.ok:
                notes.append(f"LLM 해설의 숫자 {', '.join(v.unsupported[:5])} 이(가) 근거표에 없어 템플릿 해설로 대체했습니다.")
            lim = auto_limitations(self.db, results, None, parse_period(question, dmin, dmax) if dmin else None)  # 폐기된 해설의 숫자는 제외
            answer = template_answer(question, results, self.db.uses_synthetic(), lim)
        else:
            if v and v.unsupported:
                notes.append(f"근거표에서 확인되지 않은 숫자: {', '.join(v.unsupported[:5])} (검토 필요)")
            if "review" in extras:
                try:
                    issues = self._llm_review(question, answer, results, transcript)
                    calls += 1
                    if issues:
                        lim = lim + [f"LLM 자기 점검: {x}" for x in issues]
                        plan_txt.append(f"자기 점검: 답변과 근거를 대조해 {len(issues)}건 지적")
                    else:
                        plan_txt.append("자기 점검: 답변이 근거 사실과 어긋나지 않음")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"LLM 자기 점검 실패({type(e).__name__}) — 규칙 점검만 적용")
            if lim:
                answer += "\n\n**자동 점검** (코드가 확인한 데이터·방법 한계)\n" + "\n".join(f"- {x}" for x in lim[:8])
        answer += "\n\n" + ATTRIBUTION
        followups = suggest_followups(question, results, self.db)
        if "followups" in extras:
            try:
                fu = self._llm_followups(question, results, transcript)
                calls += 1
                if fu:
                    followups = fu + [f for f in followups if f not in fu][:1]
                    plan_txt.append("후속 질문: LLM이 결과를 보고 제안")
            except Exception as e:  # noqa: BLE001
                notes.append(f"LLM 후속 질문 생성 실패({type(e).__name__}) — 규칙 제안 사용")
        res = AgentResult(question, answer, results, steps, f"llm:{self.llm.provider}/{self.llm.model}", calls, usage,
                          round(time.time() - t0, 2), self.db.uses_synthetic())
        res.llm_transcript, res.notes, res.verification = transcript, notes, verification
        res.plan, res.limitations, res.followups = plan_txt, lim, followups
        return res

    # ---------- 직렬화 ----------
    @staticmethod
    def to_dict(res: AgentResult) -> dict:
        return {"question": res.question, "answer": res.answer, "mode": res.mode, "llm_calls": res.llm_calls, "tokens": res.tokens,
                "elapsed": res.elapsed, "synthetic": res.synthetic, "verification": res.verification, "notes": res.notes,
                "plan": res.plan, "limitations": res.limitations, "followups": res.followups,
                "llm_transcript": res.llm_transcript,
                "steps": [s.__dict__ for s in res.steps],
                "results": [{"tool": r.name, "title": r.title, "params": r.params, "datasets": r.datasets, "method": r.method,
                             "facts": r.facts, "caveats": r.caveats, "gate": r.gate_log, "rows": len(r.table),
                             "table": json.loads(r.table.head(200).to_json(orient="records", force_ascii=False))} for r in res.results]}
