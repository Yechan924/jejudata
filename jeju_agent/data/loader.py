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
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from .. import schemas
from ..config import CONFIG_DIR, RAW_DIR, SYNTHETIC_DIR

try:  # DuckDB는 선택 의존성
    import duckdb  # type: ignore
except Exception:  # noqa: BLE001
    duckdb = None

log = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "utf-8")
DATA_EXTS = (".csv", ".xlsx", ".xls", ".txt", ".tsv")
NUMERIC_TYPES = {"INT", "BIGINT", "DOUBLE", "FLOAT", "INTEGER"}
# 명세서상 STRING이지만 실제로는 숫자인 컬럼
FORCE_NUMERIC = {"latitude", "longitude", "ap_latitude", "ap_longitude", "user_count",
                 "geton_station_longitude", "geton_station_latitude",
                 "getoff_station_longitude", "getoff_station_latitude"}
DOW_KO = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
# 정보성(마스터) 테이블: 일일 누적 업데이트라 같은 ID가 여러 날짜로 반복됨 → 최신 1행만
INFO_TABLES = {"bus_route": "bus_route_id", "bus_station": "bus_station_id", "weather_station": "observatory_code", "wifi_ap": "manage_no"}
# 분석 도구가 실제로 의존하는 컬럼 (실데이터 점검용)
REQUIRED_COLUMNS = {
    "wifi_ap_usage": ["base_date", "ap_place", "ap_latitude", "ap_longitude"],   # user_id 또는 user_count 중 하나 (아래 ANY_OF)
    "bus_station_usage": ["base_date", "bus_station_id", "bus_station_name", "get_on_off_type", "user_count"],
    "bus_route_usage": ["base_date", "bus_route_name", "get_on_off_type", "user_count"],
    "bus_station": ["bus_station_id", "latitude", "longitude"],
    "bus_route": ["bus_route_id", "bus_route_length"],
    "weather_daily": ["base_date", "observatory_code", "daily_rainfall"],
    "weather_station": ["observatory_code", "latitude", "longitude"],
}
# 둘 중 하나만 있으면 되는 컬럼 묶음 (원본 4-05 는 user_id, 허브 API 집계본은 user_count)
ANY_OF_COLUMNS = {"wifi_ap_usage": [["user_id", "user_count"]]}
# 있으면 좋지만 없어도 핵심 기능이 도는 컬럼 → 없으면 해당 기능만 비활성(점검 스크립트가 안내)
OPTIONAL_COLUMNS = {"wifi_ap_usage": {"nationality": "외국인 비중", "visit_type": "관광객 비중", "user_id": "고유 이용자(중복 제거)"},
                    "bus_station_usage": {"base_hour": "시간대 프로필·피크 시간", "user_category": "교통약자 비중"},
                    "bus_route_usage": {"base_hour": "저이용 시간대 탐지", "bus_route_id": "노선 정보(거리) 조인 — 없으면 노선번호로 조인"}}


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
    alias_mapped: dict = field(default_factory=dict)   # 표준컬럼 → 원본 헤더 (별칭 규칙으로 맞춘 것)
    defaulted: dict = field(default_factory=dict)      # 표준컬럼 → 채운 기본값 (원본에 없던 것)
    unmapped: list[str] = field(default_factory=list)  # 어떤 표준컬럼에도 대응되지 않은 원본 헤더


@lru_cache(maxsize=1)
def _aliases() -> dict:
    p = CONFIG_DIR / "column_aliases.yaml"
    if not p.exists():
        return {"aliases": {}, "defaults": {}}
    with open(p, encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    return {"aliases": d.get("aliases") or {}, "defaults": d.get("defaults") or {}}


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
    FILE_ALIASES = {"wifi_ap": ["와이파이 AP 현황", "와이파이AP현황", "공공와이파이 AP 정보"],
                    "bus_station": ["버스 정류소 기본 정보"], "bus_route": ["버스 노선 기본 정보"],
                    "bus_station_usage": ["일일 정류소별 버스 이용자 정보"], "bus_route_usage": ["일일 노선별 버스 이용자 정보"],
                    "wifi_ap_usage": ["와이파이 AP그룹별 일일 사용량"]}

    @classmethod
    def _targets(cls, key: str) -> set[str]:
        d = schemas.dataset(key)
        base = d["name"].split("(")[0].strip()
        t = {_norm(key), _norm(d["name"]), _norm(base), _norm(d["code"] + d["name"]), _norm(d["code"] + "_" + d["name"]), _norm(d["code"] + base)}
        t |= {_norm(a) for a in cls.FILE_ALIASES.get(key, [])}
        # 괄호 안 꼬리표 조합: '와이파이 AP 현황(고정형)' 처럼 일부만 쓴 파일명
        import re as _re
        for part in _re.findall(r"\(([^)]*)\)", d["name"]):
            for piece in part.split(","):
                if piece.strip():
                    t.add(_norm(base + piece.strip()))
        return t

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
                        out[k] += sorted(p for ext in DATA_EXTS for p in sub.glob(f"*{ext}"))
        for f in sorted(p for ext in DATA_EXTS for p in base.glob(f"*{ext}")):
            stem = _norm(f.stem)
            best, best_len = None, 0
            for k in out:
                for t in cls._targets(k):
                    if len(t) >= 4 and stem.startswith(t) and len(t) > best_len:
                        rest = stem[len(t):]
                        # 뒤에 붙는 건 기간/버전/출처 꼬리표만 허용. 한글이 바로 이어지면 다른 데이터명의 접두어(예: '와이파이 AP 현황' vs
                        # '와이파이 AP별 이용 현황')일 수 있어 제외하되, 숫자·영문·'허브'로 시작하는 꼬리표는 허용한다.
                        if rest == "" or re.fullmatch(r"[0-9v~]*", rest) or re.match(r"^(허브|api|hub|[0-9a-z])", rest):
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
        """CSV/TSV/TXT/XLSX 를 문자열 프레임으로 읽는다 (인코딩 자동 판별)."""
        if path.suffix.lower() in (".xlsx", ".xls"):
            return pd.read_excel(path, dtype=str)
        last_err: Exception | None = None
        for enc in ENCODINGS:
            try:
                with open(path, encoding=enc) as fh:
                    head = fh.readline()
                sep = "\t" if head.count("\t") > head.count(",") else (";" if head.count(";") > head.count(",") else ",")
                return pd.read_csv(path, dtype=str, encoding=enc, low_memory=False, sep=sep)
            except UnicodeDecodeError as e:
                last_err = e
        raise last_err  # type: ignore[misc]

    @staticmethod
    def standardize(df: pd.DataFrame, key: str, info: dict | None = None) -> pd.DataFrame:
        """헤더를 명세서 영문 컬럼명으로 맞추고 타입을 캐스팅한다.

        1) 명세서 영문/한글명 정확 일치 → 2) config/column_aliases.yaml 별칭 포함 매칭 → 3) 데이터셋별 기본값.
        info(dict)를 주면 alias_mapped / defaulted / unmapped 를 채워 돌려준다.
        """
        info = info if info is not None else {}
        cols = schemas.dataset(key)["columns"]
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        by_norm: dict[str, str] = {}
        for c in cols:
            by_norm[_norm(c["name"])] = c["name"]
            by_norm[_norm(c["kor"])] = c["name"]
        rename = {raw: by_norm[_norm(raw)] for raw in df.columns if _norm(raw) in by_norm}
        df = df.rename(columns=rename)

        # 2) 별칭 매칭: 비어 있는 표준 컬럼만, 아직 쓰이지 않은 원본 헤더만
        al = _aliases()["aliases"]
        used = set(rename.values())
        free = [c for c in df.columns if c not in used and c not in by_norm.values()]
        alias_mapped: dict[str, str] = {}
        for c in cols:
            name = c["name"]
            if name in df.columns or name not in al:
                continue
            for kw in sorted(al[name], key=len, reverse=True):
                k = _norm(kw)
                hit = next((h for h in free if k and k in _norm(h)), None)
                if hit:
                    df = df.rename(columns={hit: name})
                    free.remove(hit)
                    alias_mapped[name] = hit
                    break

        # 3) 기본값
        defaults = _aliases()["defaults"].get(key, {})
        defaulted: dict[str, str] = {}
        for c in cols:
            if c["name"] not in df.columns:
                if c["name"] in defaults:
                    df[c["name"]] = defaults[c["name"]]
                    defaulted[c["name"]] = defaults[c["name"]]
                else:
                    df[c["name"]] = None
        numeric = {c["name"] for c in cols if c["type"].upper() in NUMERIC_TYPES} | (FORCE_NUMERIC & set(df.columns))
        for name in numeric:
            df[name] = pd.to_numeric(df[name].astype(str).str.replace(",", ""), errors="coerce")
        # base_date: '2025-03-01', '2025/3/1', '20250301 00:00' 등을 YYYYMMDD 로 통일
        if "base_date" in df.columns:
            s = df["base_date"].astype(str).str.strip()
            digits = s.str.replace(r"[^0-9]", "", regex=True)
            ok8 = digits.str.len() >= 8
            out = digits.str[:8].where(ok8)
            rest = ~ok8 & s.ne("") & s.ne("None") & s.ne("nan")
            if rest.any():  # '2019/3/2' 처럼 자릿수가 모자란 표기는 날짜 파서로
                parsed = pd.to_datetime(s[rest], errors="coerce", format="mixed")
                out[rest] = parsed.dt.strftime("%Y%m%d")
            df["base_date"] = out.where(out.notna(), s)
        info["alias_mapped"], info["defaulted"], info["unmapped"] = alias_mapped, defaulted, free
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
        frames, infos, errors = [], [], []
        for f in files:
            try:
                info: dict = {}
                frames.append(self.standardize(self.read_csv(f), key, info))
                infos.append(info)
            except Exception as e:  # noqa: BLE001
                log.warning("%s 읽기 실패: %s", f, e)
                errors.append(f"{f.name}: {type(e).__name__}")
        if not frames:
            p = Provenance(key, "missing", [f.name for f in files], note="읽기 실패: " + "; ".join(errors))
            self.provenance[key] = p
            return p
        df = pd.concat(frames, ignore_index=True)
        if key in INFO_TABLES:  # 정보성 테이블은 ID 기준 최신 1행만
            id_col = INFO_TABLES[key]
            if id_col in df.columns and "base_date" in df.columns and df[id_col].notna().mean() > 0.9:
                df = df.sort_values("base_date", kind="stable").drop_duplicates(subset=[id_col], keep="last").reset_index(drop=True)
        df = self._add_derived(df)
        self.frames[key] = df
        self._register(key, df)
        p = Provenance(key, source, [f.name for f in files], len(df))
        if "date" in df.columns and df["date"].notna().any():
            p.date_min, p.date_max = str(df["date"].min()), str(df["date"].max())
        p.alias_mapped = {k: v for i in infos for k, v in i.get("alias_mapped", {}).items()}
        p.defaulted = {k: v for i in infos for k, v in i.get("defaulted", {}).items()}
        p.unmapped = sorted({c for i in infos for c in i.get("unmapped", [])})
        notes = []
        if p.alias_mapped:
            notes.append("별칭 매핑 " + ", ".join(f"{k}←{v}" for k, v in p.alias_mapped.items()))
        if p.defaulted:
            notes.append("기본값 채움 " + ", ".join(f"{k}={v}" for k, v in p.defaulted.items()))
        if errors:
            notes.append("읽기 실패 " + "; ".join(errors))
        if source == "raw":
            req = REQUIRED_COLUMNS.get(key, [])
            empty = [c for c in req if c in df.columns and df[c].isna().all()]
            if empty:
                notes.append("값이 전부 비어 있는 필수 컬럼: " + ", ".join(empty))
        p.note = " / ".join(notes)
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
        self._enrich_bus_station_ids()
        self._enrich_wifi()
        self._enrich_bus_route()
        return self.provenance

    def _enrich_bus_station_ids(self) -> None:
        """허브 API 3-04 의 정류소 ID(7자리 내부ID)는 3-03 정류소 정보(9자리)와 체계가 다르다.
        ID 교집합이 거의 없으면 정류소명으로 3-03 ID를 붙여 교체하고, 원본 ID는 bus_station_id_src 에 보존한다."""
        if not (self.has("bus_station_usage") and self.has("bus_station")):
            return
        u, st = self.frames["bus_station_usage"], self.frames["bus_station"]
        ids_u, ids_s = set(u["bus_station_id"].dropna().astype(str)), set(st["bus_station_id"].dropna().astype(str))
        if not ids_u or len(ids_u & ids_s) / len(ids_u) > 0.5:
            return
        ref = st.dropna(subset=["bus_station_id", "bus_station_name"]).drop_duplicates("bus_station_name").set_index("bus_station_name")["bus_station_id"].astype(str)
        mapped = u["bus_station_name"].map(ref)
        u["bus_station_id_src"] = u["bus_station_id"]
        u["bus_station_id"] = mapped.where(mapped.notna(), "N_" + u["bus_station_name"].astype(str))
        rate = mapped.notna().mean()
        self.frames["bus_station_usage"] = u
        self._register("bus_station_usage", u)
        p = self.provenance.get("bus_station_usage")
        if p:
            p.note = (p.note + " / " if p.note else "") + f"정류소ID 체계가 3-03과 달라 정류소명으로 3-03 ID 매핑({rate:.0%}), 미일치는 이름을 ID로 사용"

    def _enrich_bus_route(self) -> None:
        """허브 API 노선 이용(3-02 대체)에는 bus_route_id 가 없다 → 노선 정보(3-01)의 노선번호로 ID를 붙이고, 없으면 노선명을 ID로 쓴다."""
        if not self.has("bus_route_usage"):
            return
        u = self.frames["bus_route_usage"]
        if u["bus_route_id"].notna().mean() > 0.5:
            return
        filled = 0
        if self.has("bus_route") and "bus_route_number" in u.columns and u["bus_route_number"].notna().any():
            r = self.frames["bus_route"].dropna(subset=["bus_route_id"]).drop_duplicates("bus_route_number")[["bus_route_number", "bus_route_id"]]
            m = u[["bus_route_number"]].merge(r, on="bus_route_number", how="left")
            m.index = u.index
            u["bus_route_id"] = u["bus_route_id"].fillna(m["bus_route_id"])
            filled = int(u["bus_route_id"].notna().sum())
        u["bus_route_id"] = u["bus_route_id"].fillna(u["bus_route_name"])
        self.frames["bus_route_usage"] = u
        self._register("bus_route_usage", u)
        p = self.provenance.get("bus_route_usage")
        if p:
            p.note = (p.note + " / " if p.note else "") + f"bus_route_id 를 3-01 노선번호로 보완({filled / max(len(u), 1):.0%}), 나머지는 노선명 사용"

    def _enrich_wifi(self) -> None:
        """허브 API 집계본(4-05 대체)은 좌표·카테고리가 없다 → 4-01(AP 현황)에서 장소명으로 채운다."""
        if not self.has("wifi_ap_usage") or not self.has("wifi_ap"):
            return
        u = self.frames["wifi_ap_usage"]
        if u["ap_latitude"].notna().mean() > 0.5:
            return
        ap = self.frames["wifi_ap"]
        if "ap_group_name" not in ap.columns:
            return
        ref = (ap.dropna(subset=["latitude", "longitude"])
                 .groupby("ap_group_name", as_index=False)
                 .agg(_lat=("latitude", "median"), _lon=("longitude", "median"), _cat=("category", "first")))
        m = u[["ap_place"]].merge(ref, left_on="ap_place", right_on="ap_group_name", how="left")
        m.index = u.index
        u["ap_latitude"] = u["ap_latitude"].fillna(m["_lat"])
        u["ap_longitude"] = u["ap_longitude"].fillna(m["_lon"])
        if "ap_category" in u.columns:
            u["ap_category"] = u["ap_category"].where(u["ap_category"].notna() & (u["ap_category"] != "미분류"), m["_cat"])
        hit_ap = u["ap_latitude"].notna().mean()
        # 2차: '버스정류소_…_[405000301]' 처럼 장소명에 정류소 ID가 있으면 3-03 정류소 정보 좌표로 보완
        hit_bus = 0.0
        if self.has("bus_station") and u["ap_latitude"].isna().any():
            sid = u["ap_place"].astype(str).str.extract(r"\[(\d{6,})\]")[0]
            st = self.frames["bus_station"].dropna(subset=["latitude", "longitude"]).drop_duplicates("bus_station_id").set_index("bus_station_id")
            st.index = st.index.astype(str)
            lat2, lon2 = sid.map(st["latitude"]), sid.map(st["longitude"])
            fill = u["ap_latitude"].isna() & lat2.notna()
            u.loc[fill, "ap_latitude"], u.loc[fill, "ap_longitude"] = lat2[fill], lon2[fill]
            if "ap_category" in u.columns:
                u.loc[fill & u["ap_category"].isin([None, "미분류"]), "ap_category"] = "버스정류소"
            hit_bus = u["ap_latitude"].notna().mean() - hit_ap
        self.frames["wifi_ap_usage"] = u
        self._register("wifi_ap_usage", u)
        p = self.provenance.get("wifi_ap_usage")
        if p:
            p.note = (p.note + " / " if p.note else "") + f"좌표·카테고리를 4-01 장소명({hit_ap:.0%})" + (f"·3-03 정류소ID({hit_bus:.0%})" if hit_bus else "") + "로 보완"

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
