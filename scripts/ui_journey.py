"""Drive the running app through the core user journey in a real browser.

Usage:
    streamlit run streamlit_app.py --server.port 8599   # in another terminal, empty workspace
    python scripts/ui_journey.py --url http://localhost:8599 [--screenshots docs/screenshots]

Uses Playwright with the locally installed Google Chrome (`--browser chromium` uses
Playwright's bundled Chromium instead; install it with `playwright install chromium`).
Steps: upload the sample CSV through the Data Management page → import → run analysis →
open an insight and its evidence → create an opportunity → change RICE inputs → add to
roadmap → save a version → download a product brief → visit analytics.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample" / "sample_feedback.csv"


def wait_idle(page: Page, timeout: float = 240) -> None:
    """Wait until Streamlit has finished rerunning."""
    page.wait_for_timeout(600)
    deadline = time.time() + timeout
    while time.time() < deadline:
        running = page.locator('[data-testid="stStatusWidget"]').count()
        if not running:
            page.wait_for_timeout(400)
            if not page.locator('[data-testid="stStatusWidget"]').count():
                return
        page.wait_for_timeout(500)
    raise TimeoutError("Streamlit did not become idle")


def shot(page: Page, out: Path | None, name: str) -> None:
    if out:
        page.screenshot(path=str(out / f"{name}.png"), full_page=True)
        print(f"  screenshot {name}.png")


def nav(page: Page, label: str) -> None:
    page.locator('[data-testid="stSidebarNav"]').get_by_text(label, exact=True).click()
    wait_idle(page)


def step(msg: str) -> None:
    print(f"✓ {msg}" if not msg.startswith("…") else msg, flush=True)


def run(url: str, out: Path | None, browser_name: str, headless: bool, full_sample: bool = False) -> None:
    if out:
        out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        launch = {"headless": headless}
        if browser_name == "chrome":
            launch["channel"] = "chrome"
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1440, "height": 1700}, accept_downloads=True)
        page.goto(url, wait_until="networkidle")
        wait_idle(page)
        expect(page.get_by_role("button", name="Try it with sample data")).to_be_visible(timeout=60_000)
        step("Dashboard loads with empty state")
        shot(page, out, "01_empty_dashboard")
        if full_sample:
            page.get_by_role("button", name="Try it with sample data").click()
            wait_idle(page, timeout=900)
            expect(page.get_by_text("Biggest customer problems")).to_be_visible(timeout=120_000)
            step("One-click sample workspace loaded and analysed from the dashboard")
            chip = page.get_by_role("button", name="Enterprise", exact=True).first
            chip.click()
            wait_idle(page)
            expect(page.get_by_text("filtered from", exact=False)).to_be_visible(timeout=30_000)
            step("Filter chip narrows the Home page live")
            shot(page, out, "00_home_filtered")
            page.get_by_role("button", name="Enterprise", exact=True).first.click()
            wait_idle(page)
            page.wait_for_timeout(2000)  # let count-up animations finish
            mismatches = page.evaluate("""() => [...document.querySelectorAll('.pp-count')].filter(e =>
                getComputedStyle(e).getPropertyValue('--pp-n').trim() !==
                (e.getAttribute('aria-label') || '').replace(/[^0-9]/g, '')).length""")
            assert mismatches == 0, f"{mismatches} counted numbers did not end at their real value"
            step("Count-up numbers animate and settle on the real values")
            shot(page, out, "00a_home_animating")
            page.get_by_role("button", name="Replay").click()
            wait_idle(page)
            page.locator(".js-plotly-plot .updatemenu-button").first.click()
            page.wait_for_timeout(1800)
            shot(page, out, "00c_replay_playing")
            slider_text = page.locator(".js-plotly-plot .slider-group text").first.text_content() or ""
            step(f"Replay animation plays week by week ({slider_text.strip() or 'slider moving'})")
            page.get_by_role("button", name="Today").click()
            wait_idle(page)
            page.locator(".js-plotly-plot .scatterlayer .point").first.click(force=True)
            wait_idle(page)
            expect(page.get_by_text("word for word", exact=False)).to_be_visible(timeout=30_000)
            step("Clicking a bubble on the problem map opens that problem")
            page.get_by_role("button", name="Watch", exact=False).first.click()
            wait_idle(page)
            nav(page, "Home")
            expect(page.get_by_text("Watching", exact=True)).to_be_visible(timeout=30_000)
            assert (
                page.get_by_text("since you started watching", exact=False).count()
                + page.get_by_text("no change", exact=False).count()
                > 0
            ), "watched problem not shown on Home"
            step("Watched problem appears on Home with its latest numbers")
            page.get_by_role("button", name="Weekly summary").click()
            expect(page.get_by_text("Customer feedback update", exact=False)).to_be_visible(timeout=30_000)
            step("Weekly summary dialog opens with a ready-to-paste update")
            shot(page, out, "00b_weekly_summary")
            page.keyboard.press("Escape")
            wait_idle(page)
            box = page.get_by_placeholder("Search all feedback…")
            box.fill("csv export")
            box.press("Enter")
            wait_idle(page)
            expect(page.get_by_text("matching record(s)", exact=False)).to_be_visible(timeout=30_000)
            step("Sidebar quick search jumps to matching feedback")
            nav(page, "Home")

        # --- Import through the real upload widget
        nav(page, "Import & data")
        page.locator('[data-testid="stFileUploaderDropzoneInput"]').set_input_files(str(SAMPLE))
        wait_idle(page)
        expect(page.get_by_text("Map columns")).to_be_visible(timeout=30_000)
        step("Uploaded sample CSV; preview and column mapping shown")
        shot(page, out, "02_import_mapping")
        page.get_by_role("button", name="Import only").click()
        wait_idle(page)
        expect(page.get_by_text("new record(s)")).to_be_visible(timeout=30_000)
        step("Imported valid rows")
        nav(page, "Import & data")
        page.locator('[data-testid="stFileUploaderDropzoneInput"]').set_input_files(str(SAMPLE))
        wait_idle(page)
        expect(page.get_by_text("We recognise this file format", exact=False)).to_be_visible(timeout=30_000)
        step("Re-uploading the same file format reuses the remembered column mapping")
        page.get_by_role("button", name="Import only").click()
        wait_idle(page)
        page.get_by_role("button", name="Run analysis on the updated workspace").click()
        wait_idle(page, timeout=600)
        expect(page.get_by_text("Similar feedback grouped into customer problems", exact=False)).to_be_visible(
            timeout=60_000
        )
        step("Analysis completed (sentiment + clustering)")

        # --- Insight with evidence
        expect(page.get_by_text("word for word", exact=False)).to_be_visible(timeout=30_000)
        quotes = page.locator(".pp-quote")
        assert quotes.count() >= 1, "no quotes rendered"
        step(f"Insight detail shows {quotes.count()} verbatim quote(s) with source IDs")
        shot(page, out, "03_insight_detail")
        page.get_by_role("button", name="Open record").first.click()
        wait_idle(page)
        expect(page.get_by_text("Original feedback (verbatim)", exact=True)).to_be_visible(timeout=30_000)
        step("Evidence link opens the source record in Feedback Explorer")
        shot(page, out, "04_feedback_explorer")

        nav(page, "Problem themes")
        page.get_by_role("tab", name="Similar requests", exact=True).click()
        wait_idle(page)
        shot(page, out, "04b_duplicates")
        page.get_by_role("tab", name="Themes", exact=True).click()
        page.get_by_role("button", name="Create opportunity").first.click()
        wait_idle(page)
        expect(page.get_by_text("RICE inputs", exact=True)).to_be_visible(timeout=30_000)
        step("Opportunity created from theme")
        effort = page.get_by_label("Effort (person-months, > 0)")
        effort.fill("2")
        effort.press("Enter")
        wait_idle(page)
        expect(page.get_by_text("unsaved changes")).to_be_visible(timeout=15_000)
        page.get_by_role("button", name="Save changes").click()
        wait_idle(page)
        step("RICE recalculated live and saved after changing effort")
        shot(page, out, "05_prioritization_edit")
        page.get_by_role("button", name="Ranking", exact=True).click()
        wait_idle(page)
        shot(page, out, "06_prioritization_ranking")
        page.get_by_role("button", name="Scenarios", exact=True).click()
        wait_idle(page)
        expect(page.get_by_text("Current vs edited ranking")).to_be_visible(timeout=30_000)
        step("Scenario workspace renders")

        # --- Roadmap
        nav(page, "Roadmap")
        page.get_by_text("Choose opportunities…").click()
        page.locator('[role="option"]').first.click()
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Add to roadmap").click()
        wait_idle(page)
        step("Opportunity added to roadmap")
        page.get_by_role("button", name="Versions").click()
        wait_idle(page)
        page.get_by_label("Version name").fill("Demo plan v1")
        page.get_by_label("Version name").press("Enter")
        wait_idle(page)
        page.get_by_role("button", name="Save version").click()
        wait_idle(page)
        step("Roadmap version saved")
        page.get_by_role("button", name="Board").click()
        wait_idle(page)
        frame = page.frame_locator("iframe[title*='sortable']")
        card = frame.locator(".sortable-item").first
        expect(card).to_be_visible(timeout=30_000)
        now_body = frame.locator(".sortable-container").first.locator(".sortable-container-body")
        src, dst = card.bounding_box(), now_body.bounding_box()
        page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
        page.mouse.down()
        for i in range(1, 21):  # move in small steps so the drag library registers a drag
            page.mouse.move(
                src["x"] + (dst["x"] + 40 - src["x"]) * i / 20, src["y"] + (dst["y"] + 30 - src["y"]) * i / 20
            )
            page.wait_for_timeout(25)
        page.mouse.up()
        wait_idle(page)
        frame = page.frame_locator("iframe[title*='sortable']")
        expect(frame.locator(".sortable-container").first.locator(".sortable-item")).to_have_count(1, timeout=30_000)
        nav(page, "Home")
        expect(page.get_by_text("Building now")).to_be_visible(timeout=30_000)
        assert page.get_by_text("Nothing in Now yet").count() == 0, "dragged item did not persist to Now"
        nav(page, "Roadmap")
        step("Drag-and-drop moved the initiative to Now and saved it")
        page.get_by_role("button", name="Board").click()
        wait_idle(page)
        shot(page, out, "07_roadmap_board")
        page.get_by_role("button", name="Export & brief").click()
        wait_idle(page)
        with page.expect_download() as dl:
            page.get_by_role("button", name="Download product brief (Markdown)").click()
        path = dl.value.path()
        text = Path(path).read_text(encoding="utf-8")
        assert "## 3. Supporting evidence" in text and "RICE =" in text, "brief content missing"
        step(f"Product brief downloaded ({len(text):,} chars)")
        shot(page, out, "08_roadmap_brief")

        nav(page, "Trends")
        expect(page.get_by_text("Feedback in view")).to_be_visible(timeout=30_000)
        step("Analytics page renders")
        shot(page, out, "09_analytics")
        nav(page, "Home")
        expect(page.get_by_text("Biggest customer problems")).to_be_visible(timeout=30_000)
        step("Dashboard reflects analysed data, opportunity and roadmap")
        shot(page, out, "10_dashboard")
        nav(page, "How it works")
        shot(page, out, "11_methodology")
        errors = page.locator('[data-testid="stException"]').count()
        assert errors == 0, "a Streamlit exception is visible"
        browser.close()
    print("UI journey passed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8599")
    ap.add_argument("--screenshots", type=Path, default=None)
    ap.add_argument("--browser", choices=["chrome", "chromium"], default="chrome")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--full-sample", action="store_true", help="first load the full sample from the dashboard")
    a = ap.parse_args()
    try:
        run(a.url, a.screenshots, a.browser, not a.headed, a.full_sample)
    except Exception as exc:
        print(f"✗ UI journey failed: {exc}", file=sys.stderr)
        raise
