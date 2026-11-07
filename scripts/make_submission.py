"""제출물 생성: README 스크린샷 삽입 → 기획서 PDF → ZIP → 점검표.

  python scripts/make_submission.py            # 전부
  python scripts/make_submission.py --no-pdf   # PDF 생략(브라우저 없는 환경)

산출물 (저장소 밖 dist/ 에 생성; dist/ 는 .gitignore)
  dist/제출_재주도좋다_제주혼잡이동진단Agent.zip
  docs/AI_Agent_기획서_제주혼잡이동진단.pdf  (docs/proposal.html → Chrome headless 또는 wkhtmltopdf)
ZIP 제외: .git, .venv, __pycache__, .env, data/synthetic/*.csv(실행 시 자동 생성), outputs/(samples 제외), dist
ZIP 포함: data/raw/ 의 허브 공개 API 수집본(집계 데이터, 개인정보 없음) — 심사위원이 실데이터로 바로 재현할 수 있게. 제외하려면 --no-raw.
  (GitHub 저장소에는 .gitignore 로 올라가지 않음)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
ZIP_NAME = "제출_재주도좋다_제주혼잡이동진단Agent.zip"
PDF = ROOT / "docs" / "AI_Agent_기획서_제주혼잡이동진단.pdf"
EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "node_modules", ".claude"}
EXCLUDE_FILES = {".env"}
# 대회 제공 자료 중 외부 배포가 금지된 파일(DAKER 외 배포 금지) — ZIP에 절대 포함하지 않는다
EXCLUDE_NAME_PARTS = ("데이터목록", "데이터 목록")
SCREENS = ["01_home.png", "02_answer.png", "03_map.png", "06_llm_transfer.png"]
INCLUDE_RAW = True


def insert_screens() -> None:
    readme = ROOT / "README.md"
    s = readme.read_text(encoding="utf-8")
    have = [f for f in SCREENS if (ROOT / "docs" / "screens" / f).exists()]
    if not have or "<!-- screens:start -->" not in s:
        print("스크린샷 없음 — README 그대로 둠 (docs/DEMO_SCRIPT.md §3 참고)")
        return
    caps = {"01_home.png": "첫 화면", "02_answer.png": "답변·실행 계획·이어서 물어보기", "03_map.png": "지도", "06_llm_transfer.png": "LLM 전송 내용"}
    body = "\n".join(f"![{caps[f]}](docs/screens/{f})" for f in have)
    s = re.sub(r"<!-- screens:start -->.*?<!-- screens:end -->", f"<!-- screens:start -->\n{body}\n<!-- screens:end -->", s, flags=re.S)
    readme.write_text(s, encoding="utf-8")
    print(f"README 스크린샷 {len(have)}장 삽입")


def make_pdf() -> bool:
    src = ROOT / "docs" / "proposal.html"
    chrome = next((c for c in ("google-chrome", "chromium", "chromium-browser", "chrome",
                               r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                               "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") if shutil.which(c) or Path(c).exists()), None)
    if chrome:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer", f"--print-to-pdf={PDF}", src.as_uri()]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if PDF.exists() and PDF.stat().st_size > 10_000:
            print(f"PDF 생성(Chrome): {PDF.name} {PDF.stat().st_size / 1e6:.2f}MB")
            return True
        print("Chrome PDF 실패:", r.stderr[-300:])
    if shutil.which("wkhtmltopdf"):
        r = subprocess.run(["wkhtmltopdf", "--enable-local-file-access", "-q", str(src), str(PDF)], capture_output=True, text=True, timeout=120)
        if PDF.exists():
            print(f"PDF 생성(wkhtmltopdf): {PDF.name} {PDF.stat().st_size / 1e6:.2f}MB")
            return True
    print("PDF 도구 없음 — docs/proposal.html 을 브라우저에서 열어 '인쇄 → PDF 저장'(여백 기본, 배경 그래픽 켬)")
    return False


def pdf_pages() -> int | None:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(PDF)).pages)
    except Exception:  # noqa: BLE001
        try:
            import re as _re
            return len(_re.findall(rb"/Type\s*/Page[^s]", PDF.read_bytes()))
        except Exception:  # noqa: BLE001
            return None


def should_skip(rel: Path) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIRS for p in parts) or rel.name in EXCLUDE_FILES or rel.suffix in (".pyc", ".duckdb"):
        return True
    if any(k in rel.name for k in EXCLUDE_NAME_PARTS):
        return True
    if parts[0] == "data" and len(parts) > 1 and parts[1] == "raw":
        return (not INCLUDE_RAW) or rel.name.startswith(".")
    if parts[0] == "data" and len(parts) > 1 and parts[1] == "synthetic" and rel.suffix == ".csv":
        return True
    if parts[0] == "outputs" and not (len(parts) > 1 and parts[1] == "samples"):
        return True
    return False


def make_zip() -> Path:
    DIST.mkdir(exist_ok=True)
    out = DIST / ZIP_NAME
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            if should_skip(rel):
                continue
            z.write(p, str(rel))
            n += 1
    print(f"ZIP 생성: {out} ({out.stat().st_size / 1e6:.2f}MB, {n}파일)")
    return out


def verify_zip(out: Path) -> None:
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    bad = [n for n in names if n.endswith("/.env") or n == ".env"]
    raw = [n for n in names if n.startswith("data/raw/")]
    print("점검:", "✗ .env 포함!" if bad else "✓ .env 미포함", f"· 실데이터 {len(raw)}파일 {'포함' if raw else '미포함'}", "·", f"{'✓' if 'README.md' in names else '✗'} README",
          f"{'✓' if any(n.startswith('docs/USER_MANUAL') for n in names) else '✗'} 매뉴얼",
          f"{'✓' if any(n.startswith('docs/DATA_SPEC') for n in names) else '✗'} 데이터 명세",
          f"{'✓' if any(n.startswith('docs/RESULT_REPORT') for n in names) else '✗'} 결과 설명",
          f"{'✓' if any(n.startswith('outputs/samples/') for n in names) else '✗'} 샘플 결과")
    if PDF.exists():
        pg = pdf_pages()
        print(f"PDF: {PDF.stat().st_size / 1e6:.2f}MB{'' if pg is None else f', {pg}쪽'} — {'✓' if (pg is None or pg <= 15) and PDF.stat().st_size < 20e6 else '✗ 15쪽/20MB 초과'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-raw", action="store_true", help="data/raw 실데이터를 ZIP에서 제외")
    a = ap.parse_args()
    global INCLUDE_RAW
    INCLUDE_RAW = not a.no_raw
    os.chdir(ROOT)
    insert_screens()
    if not a.no_pdf:
        make_pdf()
    out = make_zip()
    verify_zip(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
