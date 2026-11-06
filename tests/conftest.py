"""테스트는 data/raw 의 실데이터와 무관하게 합성 데이터만으로 돈다 (실데이터 유무·기간에 따라 결과가 달라지지 않도록)."""
import os

import pytest

os.environ.setdefault("LLM_EXTRAS", "none")   # 기존 LLM 경로 테스트는 호출 횟수를 세므로 부가 단계는 개별 테스트에서만 켠다


@pytest.fixture(autouse=True, scope="session")
def _isolate_raw_dir(tmp_path_factory):
    import jeju_agent.data.loader as L
    empty = tmp_path_factory.mktemp("raw_empty")
    original, orig_demo = L.RAW_DIR, L.DEMO_DIR
    L.RAW_DIR = empty
    L.DEMO_DIR = tmp_path_factory.mktemp("demo_empty")
    yield
    L.RAW_DIR, L.DEMO_DIR = original, orig_demo
