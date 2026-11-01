"""CSV → 분석 엔진 적재.

규칙
- data/raw/ 아래의 CSV를 명세서 데이터명·키·코드로 퍼지 매칭해 자동 인식한다.
  (예: '와이파이 AP별 이용 현황.csv', '4-05 와이파이AP별이용현황_2025.csv', 'wifi_ap_usage/*.csv' 모두 인식)
- 헤더가 영문(컬럼명(영문))이든 한글(컬럼명(한글))이든 명세서 기준 영문명으로 표준화한다.
- 인코딩은 utf-8-sig → cp949 → euc-kr 순으로 시도한다.
- raw에 없는 데이터셋은 data/synthetic/ 의 개발용 합성 데이터로 대체하고 provenance에 기록한다.
- SQL 엔진은 DuckDB를 우선 사용하고, 없으면 sqlite3(in-memory)로 폴백한다.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .. import schemas
from ..config import RAW_DIR, SYNTHETIC_DIR

try:  # DuckDB는 선택 의존성
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001
    duckdb = None

log = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "utf-8")
NUMERIC_TYPES = {"INT", "BIGINT", "DOUBLE", "FLOAT", "INTEGER"}
# 명세서상 STRING이지만 실제로는 숫자인 컬럼
FORCE_NUMERIC = {"latitude", "longitude", "ap_latitude", "ap_longitude", "user_count",
                 "geton_station_longitude", "geton_station_latitude",
                 "getoff_station_longitude", "getoff_station_latitude"}
DOW_KO = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-()（）,./·]", "", str(s)).lower()


@dataclass
class Provenance:
    key: str
    source: str            # "raw" | "synthetic" | "missing"
    files: list[str] = field(default_factory=list)
    rows: int = 0
    note: str = ""
    date_min: str = ""
    date_max: str = ""


class JejuDB:
    """데이터프레임 저장소 + SQL 엔진(DuckDB 또는 sqlite)."""

    def __init__(self):
        self.frames: dict[str, pd.DataFrame] = {}
        self.provenance: dict[str, Provenance] = {}
        if duckdb is not None:
            self.engine = "duckdb"
            self.con = duckdb.connect(":memory:")
        else:
            self.engine = "sqlite"
            self.con = sqlite3.connect(":memory:", check_same_thread=False)

    # ---------- 파일 탐색 ----------
    @staticmethod
    def _targets(key: str) -> set[str]:
        d = schemas.dataset(key)
        return {_norm(key), _norm(d["name"]), _norm(d["code"] + d["name"]), _norm(d["code"] + "_" + d["name"])}

    @classmethod
    def assign_files(cls, base: Path) -> dict[str, list[Path]]:
        """base 아래 CSV를 '가장 길게 일치하는' 데이터셋에 배정한다 (wifi_ap vs wifi_ap_usage 충돌 방지)."""
        out: dict[str, list[Path]] = {k: [] for k in schemas.catalog()}
        if not base.exists():
            return out
        for sub in base.iterdir():
            if sub.is_dir():
                n = _norm(sub.name)
                for k in out:
                    if n in cls._targets(k):
                        out[k] += sorted(sub.glob("*.csv"))
        for f in base.glob("*.csv"):
            stem = _norm(f.stem)
            best, best_len = None, 0
            for k in out:
                for t in cls._targets(k):
                    if len(t) >= 4 and stem.startswith(t) and len(t) > best_len:
                        rest = stem[len(t):]
                        # 뒤에 붙는 건 기간/버전/번호 정도만 허용 (다른 데이터명의 접두어로 오인 방지)
                        if rest == "" or re.fullmatch(r"[0-9v~]*", rest):
                            best, best_len = k, len(t)
            if best:
                out[best].append(f)
        return {k: sorted(set(v)) for k, v in out.items()}

    def find_files(self, key: str, base: Path) -> list[Path]:
        cache_name = f"_assign_{_norm(str(base))}"
        if not hasattr(self, cache_name):
            setattr(self, cache_name, self.assign_files(base))
        return getattr(self, cache_name).get(key, [])

    # ---------- CSV 읽기 ----------
    @staticmethod
    def read_csv(path: Path) -> pd.DataFrame:
        last_err: Exception | None = None
        for enc in ENCODINGS:
            try:
                return pd.read_csv(path, dtype=str, encoding=enc, low_memory=False)
            except UnicodeDecodeError as e:
                last_err = e
        raise last_err  # type: ignore[misc]

    @staticmethod
    def standardize(df: pd.DataFrame, key: str) -> pd.DataFrame:
        """헤더를 명세서 영문 컬럼명으로 맞추고 타입을 캐스팅한다."""
        cols = schemas.dataset(key)["columns"]
        by_norm: dict[str, str] = {}
        for c in cols:
            by_norm[_norm(c["name"])] = c["name"]
            by_norm[_norm(c["kor"])] = c["name"]
        rename = {raw: by_norm[_norm(raw)] for raw in df.columns if _norm(raw) in by_norm}
        df = df.rename(columns=rename)
        for c in cols:
            if c["name"] not in df.columns:
                df[c["name"]] = None
        for c in cols:
            name = c["name"]
            if c["type"].upper() in NUMERIC_TYPES or name in FORCE_NUMERIC:
                df[name] = pd.to_numeric(df[name].astype(str).str.replace(",", ""), errors="coerce")
        return df

    @staticmethod
    def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
        """분석에 반복적으로 쓰이는 파생 컬럼."""
        if "base_date" in df.columns:
            dt = pd.to_datetime(df["base_date"].astype(str).str[:8], format="%Y%m%d", errors="coerce")
            df["date"] = dt.dt.strftime("%Y-%m-%d")
            df["ym"] = dt.dt.strftime("%Y-%m")
            df["weekday"] = dt.dt.weekday
            df["dow"] = dt.dt.weekday.map(DOW_KO)
            df["is_weekend"] = (dt.dt.weekday >= 5).astype("Int64")
        if "base_hour" in df.columns:
            df["hour"] = pd.to_numeric(df["base_hour"], errors="coerce")
        return df

    # ---------- 적재 ----------
    def load_dataset(self, key: str) -> Provenance:
        files = self.find_files(key, RAW_DIR)
        source = "raw"
        if not files:
            files = self.find_files(key, SYNTHETIC_DIR)
            source = "synthetic" if files else "missing"
        if not files:
            p = Provenance(key, "missing", note="data/raw, data/synthetic 어디에도 파일 없음")
            self.provenance[key] = p
            return p
        frames = []
        for f in files:
            try:
                frames.append(self.standardize(self.read_csv(f), key))
            except Exception as e:  # noqa: BLE001
                log.warning("%s 읽기 실패: %s", f, e)
        if not frames:
            p = Provenance(key, "missing", [f.name for f in files], note="읽기 실패")
            self.provenance[key] = p
            return p
        df = self._add_derived(pd.concat(frames, ignore_index=True))
        self.frames[key] = df
        self._register(key, df)
        p = Provenance(key, source, [f.name for f in files], len(df))
        if "date" in df.columns and df["date"].notna().any():
            p.date_min, p.date_max = str(df["date"].min()), str(df["date"].max())
        self.provenance[key] = p
        return p

    def _register(self, key: str, df: pd.DataFrame) -> None:
        if self.engine == "duckdb":
            self.con.register(f"_{key}", df)
            self.con.execute(f"CREATE OR REPLACE TABLE {key} AS SELECT * FROM _{key}")
            self.con.unregister(f"_{key}")
        else:
            df.to_sql(key, self.con, if_exists="replace", index=False)

    def load_all(self, keys: list[str] | None = None) -> dict[str, Provenance]:
        for k in keys or schemas.CORE_DATASETS:
            self.load_dataset(k)
        return self.provenance

    # ---------- 조회 ----------
    def table(self, key: str) -> pd.DataFrame:
        if key not in self.frames:
            raise KeyError(f"데이터셋 '{key}'이(가) 적재되지 않았습니다.")
        return self.frames[key]

    def has(self, key: str) -> bool:
        return key in self.frames

    def query(self, sql: str) -> pd.DataFrame:
        if self.engine == "duckdb":
            return self.con.execute(sql).df()
        return pd.read_sql_query(sql, self.con)

    def summary(self) -> pd.DataFrame:
        rows = []
        for k, p in self.provenance.items():
            d = schemas.dataset(k)
            rows.append({"key": k, "code": d["code"], "name": d["name"], "source": p.source, "rows": p.rows,
                         "files": ", ".join(p.files[:3]) + (" …" if len(p.files) > 3 else ""),
                         "period": f"{p.date_min}~{p.date_max}" if p.date_min else "", "note": p.note})
        return pd.DataFrame(rows)

    def uses_synthetic(self) -> bool:
        return any(p.source == "synthetic" for p in self.provenance.values())
