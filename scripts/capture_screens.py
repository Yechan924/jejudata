"""UI 스크린샷 자동 캡처 (README·기획서·상호평가용).

준비(한 번):  pip install playwright && playwright install chromium
실행:         python scripts/capture_screens.py                # Streamlit을 띄우고 docs/screens/*.png 저장
              python scripts/capture_screens.py --url http://localhost:8501   # 이미 떠 있는 앱 사용
              python scripts/capture_screens.py --offline      # LLM 키가 있어도 오프라인 모드로 캡처

저장되는 파일 (docs/screens/)
  01_home.png        첫 화면(혼잡 상위 5곳 지도 · 오늘의 발견 · 질문 버튼)
  02_answer.png      답변 + '근거 보기' 패널 + 이어서 물어보기
  03_map.png         지도 탭
  04_chart.png       차트 탭
  05_evidence.png    근거 표 탭
  06_llm_transfer.png LLM 전송 내용 탭
  07_followup.png    후속 질문 클릭 결과
  08_discover.png    첫 화면 '오늘의 발견' 카드 → 자세히 보기 결과
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screens"
QUESTION = "주말에 가장 붐비는 관광지는 어디야?"


def scroll_top(page):
    """chat_input 때문에 화면이 아래로 내려가 있으면 맨 위(제목·지도)로 올려서 찍는다."""
    page.evaluate("document.querySelectorAll('[data-testid=stAppScrollToBottomContainer], section.stMain, section.main, "
                  "[data-testid=stMain], [data-testid=stAppViewContainer]').forEach(el => el.scrollTo(0, 0))")
    page.wait_for_timeout(500)


def wait_ready(page, timeout=90):
    """Streamlit 'Running…' 표시가 사라질 때까지 대기."""
    page.wait_for_timeout(1500)
    for _ in range(timeout * 2):
        if page.locator("[data-testid='stStatusWidget']").count() == 0:
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(800)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium  을 먼저 실행하세요.")
        return 1

    proc = None
    url = a.url
    if not url:
        env = dict(os.environ)
        if a.offline:
            env["LLM_PROVIDER"] = "offline"
        proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(a.port), "--server.headless", "true"],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://localhost:{a.port}"
        time.sleep(6)

    OUT.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
            # networkidle 은 지도 타일 스트리밍 때문에 끝나지 않을 수 있어 load + wait_ready 로 대기
            page.goto(url, wait_until="load", timeout=120_000)
            wait_ready(page, 120)   # 첫 실행은 데이터 적재(합성 생성 시 최대 1분)
            scroll_top(page)
            page.screenshot(path=str(OUT / "01_home.png"))
            print("01_home")

            disc = page.locator("button", has_text="자세히 보기").first   # 첫 화면 '오늘의 발견' 카드 → 세부 분석
            if disc.count():
                disc.click()
                wait_ready(page, 120)
                page.screenshot(path=str(OUT / "08_discover.png"))
                print("08_discover")
                page.goto(url, wait_until="load", timeout=120_000)
                wait_ready(page, 120)

            page.get_by_role("button", name=QUESTION).first.click()
            wait_ready(page, 120)
            page.locator("summary", has_text="근거 보기").first.click()   # 답변 아래 '근거 보기' 펼침
            page.wait_for_timeout(500)
            page.screenshot(path=str(OUT / "02_answer.png"), full_page=False)
            print("02_answer")

            tabs = {"03_map.png": "지도", "04_chart.png": "차트", "05_evidence.png": "근거 표", "06_llm_transfer.png": "LLM 전송 내용"}
            for fn, label in tabs.items():
                t = page.get_by_role("tab", name=label, exact=False).last
                t.scroll_into_view_if_needed()
                t.click()
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / fn))
                print(fn[:-4])

            fu = page.locator("button", has_text="언제 가장 붐비나").first
            if fu.count():
                fu.scroll_into_view_if_needed()
                fu.click()
                wait_ready(page, 120)
                page.screenshot(path=str(OUT / "07_followup.png"))
                print("07_followup")
            b.close()
    finally:
        if proc:
            proc.terminate()
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
