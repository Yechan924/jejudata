"""골든 회귀 — 동봉 축약본(data/demo)으로 18문항을 오프라인 실행한 지문이 tests/golden/demo_golden.json 과 같아야 한다.

코드를 바꿔 결과가 의도적으로 달라졌다면: python scripts/reproduce.py --update-golden
(다른 테스트와 달리 합성 데이터가 아니라 실데이터 축약본을 쓴다. data/demo 가 없으면 건너뛴다.)
"""
import pytest

import jeju_agent.data.loader as L
from jeju_agent import golden
from jeju_agent.agent import JejuAgent
from jeju_agent.config import DEMO_DIR
from jeju_agent.data.loader import JejuDB
from jeju_agent.llm import LLM


@pytest.fixture(scope="module")
def demo_agent():
    if not (DEMO_DIR / "wifi_ap_usage.csv.gz").exists():
        pytest.skip("data/demo 없음")
    prev = L.DEMO_DIR
    L.DEMO_DIR = DEMO_DIR          # conftest 가 비워 둔 DEMO_DIR 를 이 모듈에서만 복원 (RAW_DIR 은 비운 채로 → 항상 축약본 기준)
    try:
        db = JejuDB(); db.load_all()
        assert not db.uses_synthetic(), "골든 테스트는 실데이터 축약본으로만 돈다"
        yield JejuAgent(db, LLM("offline"))
    finally:
        L.DEMO_DIR = prev


@pytest.fixture(scope="module")
def expected():
    exp = golden.load()
    if exp is None:
        pytest.skip("tests/golden/demo_golden.json 없음 — python scripts/reproduce.py 로 생성")
    return exp


@pytest.mark.parametrize("q", golden.load_questions())
def test_golden(demo_agent, expected, q):
    assert q in expected, f"골든에 없는 문항: {q} (reproduce.py --update-golden)"
    res = demo_agent.discover(top_n=3) if q == "__discover__" else demo_agent.run_offline(q)
    fp = golden.fingerprint(res)
    assert fp["ok"], [s.error for s in res.steps if not s.ok]
    d = golden.diff(expected[q], fp)
    assert not d, "\n".join(d)


def test_fingerprint_diff_detects_changes():
    a = {"tools": ["x"], "ok": True, "results": [{"tool": "x", "rows": 3, "first": {"ap_place": "A", "v": 1.5}, "fact": "f"}]}
    b = {"tools": ["x"], "ok": True, "results": [{"tool": "x", "rows": 3, "first": {"ap_place": "A", "v": 1.5}, "fact": "f"}]}
    assert golden.diff(a, b) == []
    b["results"][0]["first"]["v"] = 1.6
    b["results"][0]["rows"] = 4
    out = golden.diff(a, b)
    assert len(out) == 2 and any("행 수" in m for m in out) and any("v: 1.5 → 1.6" in m for m in out)
