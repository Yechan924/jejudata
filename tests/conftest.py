"""테스트는 data/raw 의 실데이터와 무관하게 합성 데이터만으로 돈다 (실데이터 유무·기간에 따라 결과가 달라지지 않도록)."""
import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_raw_dir(tmp_path_factory):
    import jeju_agent.data.loader as L
    empty = tmp_path_factory.mktemp("raw_empty")
    original = L.RAW_DIR
    L.RAW_DIR = empty
    yield
    L.RAW_DIR = original
