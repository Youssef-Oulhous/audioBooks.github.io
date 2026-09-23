"""End-to-end run of the AuK Audiobooks PWA in headless Chromium (iPhone viewports).

Usage:  ../server/.venv/bin/python e2e.py [--base http://127.0.0.1:8799] [--auth-base http://127.0.0.1:8798]
Needs mock_server.py running on --base (and optionally a second one started with
--token secret on --auth-base). Screenshots go to app-tests/screenshots/.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pymupdf
from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "screenshots"
FIX = HERE / "fixtures"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"

PROSE = (
    "The lamp had burned every night for forty years, and every night the keeper climbed the iron stairs to trim the wick. "
    "Below him the sea turned over in its sleep, grey and patient, and the gulls settled along the rail like punctuation. "
    "He kept a ledger of ships and weather, and in the margins he wrote small notes to his daughter, who lived far inland "
    "and had never seen a wave taller than a hedge. "
)

results: list[tuple[str, bool, str]] = []
errors: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def make_pdfs() -> tuple[Path, Path]:
    FIX.mkdir(exist_ok=True)
    book = FIX / "lighthouse-keeper.pdf"
    doc = pymupdf.open()
    titles = ["Contents", "Chapter 1: The Lamp", "Chapter 2: The Ledger", "Chapter 3: A Letter Inland", "Chapter 4: The Storm", "Chapter 5: Morning", "Chapter 6: The Relief Boat"]
    toc = []
    for i, t in enumerate(titles):
        page = doc.new_page(width=420, height=640)
        body = "\n".join(titles[1:]) if i == 0 else (PROSE * (3 + i % 3))
        page.insert_text((40, 70), t, fontsize=18, fontname="tiro")
        page.insert_textbox(pymupdf.Rect(40, 90, 380, 610), body, fontsize=10.5, fontname="tiro")
        toc.append([1, t, i + 1])
    doc.set_toc(toc)
    doc.set_metadata({"title": "The Lighthouse Keeper", "author": "E. M. Hart"})
    doc.save(book)
    scanned = FIX / "scanned-book.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=640)
    page.draw_rect(pymupdf.Rect(40, 40, 380, 600), color=(0.2, 0.2, 0.2), fill=(0.9, 0.88, 0.84))
    doc.save(scanned)
    return book, scanned


def new_context(browser, width=390, height=844, scheme="dark", scale=3):
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=scale,
        is_mobile=True,
        has_touch=True,
        user_agent=UA,
        color_scheme=scheme,
    )
    return ctx


netfails: list = []


def watch(page, tag: str) -> None:
    page.on("console", lambda m: errors.append(f"[{tag}] console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[{tag}] pageerror: {e}"))
    page.on("requestfailed", lambda r: netfails.append(f"[{tag}] {r.method} {r.url.split('?')[0]} {r.failure}"))


T0 = time.time()


def shot(page, name: str, full: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(350)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print(f"  [{time.time() - T0:6.1f}s] {name}", flush=True)
    sw, cw = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    check(f"no horizontal overflow: {name}", sw <= cw, f"scrollWidth {sw} vs {cw}")


def audio_time(page) -> float:
    return page.evaluate("document.querySelector('audio').currentTime")


def wait_playing(page, timeout=15000) -> None:
    page.wait_for_function("() => { const a = document.querySelector('audio'); return a && !a.paused && a.currentTime > 0.3 }", timeout=timeout)


def main_flow(browser, base: str, book_pdf: Path, scanned_pdf: Path) -> None:
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "main")

    # ---------- library ----------
    page.goto(f"{base}/#/library")
    page.wait_for_selector(".shelf-item .wc")
    check("library lists books", page.locator(".shelf-item").count() >= 8)
    check("no status chip when the engine is ready", page.locator(".status-chip").count() == 0)
    shot(page, "01-library-dark")

    page.goto(f"{base}/__mock/engine?state=loading&demo=1")
    page.goto(f"{base}/#/library")
    page.wait_for_selector(".callout-demo")
    check("demo banner visible", "placeholder tones" in page.locator(".callout-demo").inner_text())
    check("status chip shows Loading voices", "Loading voices" in page.locator(".status-chip").inner_text())
    shot(page, "01b-library-demo-loading")
    page.goto(f"{base}/__mock/engine?state=ready&demo=0")

    # ---------- upload ----------
    page.goto(f"{base}/#/library")
    page.locator(".lib-head a[href='#/new']").click()
    page.wait_for_selector(".drop")
    shot(page, "02-upload")
    page.set_input_files("input[type=file]", str(scanned_pdf))
    page.wait_for_selector(".upload-card")
    page.wait_for_selector(".callout-error", timeout=15000)
    check("scanned PDF error shown", "scanned" in page.locator(".callout-error").inner_text())
    shot(page, "03-upload-scanned-error")

    page.set_input_files("input[type=file]", str(book_pdf))
    page.wait_for_selector(".up-phase strong", timeout=10000)
    shot(page, "03b-upload-reading")
    page.wait_for_url("**/chapters", timeout=20000)
    page.wait_for_selector(".sch")
    check("chapters detected", page.locator(".sch").count() == 7, str(page.locator(".sch").count()))
    check("Contents unticked by default", not page.locator(".sch .switch").first.is_checked())
    check("detection note shown", "bookmarks" in page.locator(".callout-info").inner_text())
    shot(page, "04-chapters")

    # rename chapter 2 inline, untick chapter 7
    page.locator(".sch-title").nth(1).click()
    page.locator(".input-inline").fill("Chapter 1: The Great Lamp")
    page.locator(".input-inline").press("Enter")
    check("inline rename applied", "The Great Lamp" in page.locator(".sch-title-text").nth(1).inner_text())
    page.locator(".sch .switch-wrap").nth(6).click()
    check("totals update", page.locator(".totals-text strong").inner_text().startswith("5 of 7"), page.locator(".totals-text strong").inner_text())
    page.locator(".input-author").fill("Eleanor M. Hart")
    shot(page, "05-chapters-edited")
    shot(page, "05b-chapters-full", full=True)

    # ---------- voice ----------
    page.get_by_role("button", name="Choose a voice").click()
    page.wait_for_url("**/voice")
    page.wait_for_selector(".vc")
    en_count = page.locator(".vc:not(.vc-design)").count()
    shot(page, "06-voices")
    oliver = page.locator(".vc", has_text="Oliver")
    oliver.locator(".vc-play").click()
    expect(oliver.locator(".vc-status")).to_have_text("Creating sample…", timeout=3000)
    shot(page, "07-voice-generating")
    expect(oliver.locator(".vc-play.playing")).to_have_count(1, timeout=15000)
    check("generated sample plays", True)
    shot(page, "07b-voice-playing")
    page.locator(".filter-row .switch-wrap").click()
    all_count = page.locator(".vc:not(.vc-design)").count()
    check("show all voices adds Chinese voices", all_count > en_count, f"{en_count} -> {all_count}")

    page.locator(".vc-design").click()
    page.wait_for_selector(".designer")
    shot(page, "08-design-voice")
    page.locator(".designer input").first.fill("Grandpa Joe")
    page.locator(".example-chip").first.click()
    page.get_by_role("button", name="Create voice").click()
    joe = page.locator(".vc", has_text="Grandpa Joe")
    expect(joe).to_have_count(1, timeout=5000)
    expect(joe.locator(".vc-status")).to_have_text("Creating sample…", timeout=3000)
    expect(joe.locator(".vc-play.playing")).to_have_count(1, timeout=15000)
    check("custom voice created and sampled", True)
    check("custom voice has delete button", joe.locator(".vc-del").count() == 1)
    joe.locator(".vc-retake").click()
    expect(joe.locator(".vc-status")).to_have_text("Creating sample…", timeout=3000)
    expect(joe.locator(".vc-play.playing")).to_have_count(1, timeout=15000)
    check("another take re-rolls the sample", True)
    shot(page, "08b-custom-voice")
    joe.locator(".vc-del").click()
    page.get_by_role("button", name="Delete voice", exact=True).click()
    expect(page.locator(".vc", has_text="Grandpa Joe")).to_have_count(0, timeout=5000)
    check("custom voice can be deleted", True)

    page.locator(".vc", has_text="James").locator(".vc-main").click()
    page.get_by_role("radio", name="Brisk").click()
    page.locator(".flow-footer .btn").click()
    page.wait_for_selector(".confirm-sheet")
    summary = page.locator(".summary").inner_text()
    check("confirm summary", "James · Brisk pace" in summary and "5 of 7" in summary and "whole book ready" in summary.lower() and "start listening" in summary.lower(), summary.replace("\n", " | "))
    shot(page, "09-confirm")
    page.get_by_role("button", name="Create audiobook").click()
    page.wait_for_url("**/#/book/**")
    page.wait_for_selector(".render-card")
    page.wait_for_timeout(3500)  # one poll, so the queue position is known
    eta = page.locator(".rc-eta").inner_text()
    check("queued book shows its place in line", "ahead of this one" in eta or "Starting soon" in eta, eta)
    shot(page, "10-book-queued")

    # ---------- playback ----------
    page.goto(f"{base}/#/book/bk_frank001")
    page.wait_for_selector(".ch")
    page.locator(".ch", has_text="Letter 2").click()
    wait_playing(page)
    check("chapter tap starts playback", True)
    check("mini player visible", page.locator(".mini").is_visible())
    check("playing row highlighted", "Letter 2" in page.locator(".ch.is-current").inner_text())
    page.wait_for_timeout(1500)
    shot(page, "11-book-playing")

    page.locator(".mini-main").click()
    page.wait_for_selector(".np.open")
    page.wait_for_timeout(500)
    shot(page, "12-now-playing")
    pos = page.locator(".np-pos").inner_text()
    check("book position line", pos.startswith("Chapter 2 of 11") and "left in book" in pos, pos)

    page.get_by_role("button", name="Next chapter").click()
    expect(page.locator(".np-chapter")).to_have_text("Letter 3", timeout=5000)
    wait_playing(page)
    check("next chapter", True)

    t0 = audio_time(page)
    page.get_by_role("button", name="Forward 30 seconds").last.click()
    page.wait_for_timeout(300)
    t1 = audio_time(page)
    page.get_by_role("button", name="Back 15 seconds").click()
    page.wait_for_timeout(300)
    t2 = audio_time(page)
    check("skip +30 / -15", t1 - t0 > 25 and 10 < t1 - t2 < 20, f"{t0:.1f} -> {t1:.1f} -> {t2:.1f}")

    page.locator(".np-x", has_text="Speed").click()
    rate = page.evaluate("document.querySelector('audio').playbackRate")
    check("speed cycles to 1.25×", abs(rate - 1.25) < 1e-6 and "1.25×" in page.locator(".np-x", has_text="Speed").inner_text(), str(rate))

    page.locator(".np-x", has_text="Sleep").click()
    page.wait_for_selector(".actions")
    shot(page, "13-sleep-sheet")
    page.get_by_role("button", name="15 minutes").click()
    page.wait_for_timeout(1500)
    label = page.locator(".np-x", has_text="Sleep").inner_text()
    check("sleep timer counts down", "14:5" in label or "15:00" in label, label.replace("\n", " "))
    shot(page, "14-now-playing-sleep-speed")

    # auto-advance on `ended`
    page.evaluate("const a = document.querySelector('audio'); a.currentTime = a.duration - 1.2")
    expect(page.locator(".np-chapter")).to_have_text("Letter 4", timeout=12000)
    wait_playing(page)
    check("auto-continues to next chapter on ended", True)

    page.evaluate("document.querySelector('audio').currentTime = 40")
    page.wait_for_timeout(300)
    page.get_by_role("button", name="Previous chapter").click()  # > 5 s in: restart this chapter
    page.wait_for_timeout(400)
    check("previous restarts the chapter when > 5 s in", page.locator(".np-chapter").inner_text() == "Letter 4" and audio_time(page) < 3, f"{audio_time(page):.1f}")
    page.get_by_role("button", name="Previous chapter").click()  # at the start: previous chapter
    expect(page.locator(".np-chapter")).to_have_text("Letter 3", timeout=5000)
    check("previous chapter", True)

    # sleep: end of chapter
    page.locator(".np-x", has_text="Sleep").click()
    page.get_by_role("button", name="End of chapter").click()
    page.evaluate("const a = document.querySelector('audio'); a.currentTime = a.duration - 1.0")
    page.wait_for_function("() => document.querySelector('audio').paused", timeout=12000)
    page.wait_for_timeout(800)
    check("end-of-chapter sleep stops instead of advancing", page.locator(".np-chapter").inner_text() == "Letter 3")

    page.locator(".np-x", has_text="Chapters").click()
    page.wait_for_selector(".np:not(.open)", state="attached")
    page.wait_for_timeout(900)
    cur = page.locator(".ch.is-current")
    box = cur.bounding_box()
    check("Chapters button scrolls to current chapter", box is not None and 0 < box["y"] < 844, str(box))
    shot(page, "15-book-chapter-list")
    check("listened chapters marked", page.locator(".ch.listened").count() >= 1, str(page.locator(".ch.listened").count()))

    # position persisted
    saved = page.evaluate("JSON.parse(localStorage.getItem('auk.pos:bk_frank001'))")
    check("position saved per book", saved and saved.get("i") == 3, str(saved))

    # scrubber
    page.locator(".mini-main").click()
    page.wait_for_selector(".np.open")
    page.evaluate("""(() => { const r = document.querySelector('.scrub'); r.value = '500'; r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    page.wait_for_timeout(300)
    frac = page.evaluate("(() => { const a = document.querySelector('audio'); return a.currentTime / a.duration })()")
    check("scrubber seeks within the chapter", 0.45 < frac < 0.55, f"{frac:.2f}")
    page.get_by_role("button", name="Close Now Playing").click()

    # pause / resume creation
    page.get_by_role("button", name="Pause creation").click()
    page.wait_for_selector(".render-card.paused", timeout=5000)
    check("pause creation", "Creation paused" in page.locator(".render-card").inner_text())
    shot(page, "15b-book-paused")
    page.get_by_role("button", name="Resume creation").click()
    page.wait_for_selector(".render-card:not(.paused) .rc-label", timeout=5000)
    check("resume creation", page.locator(".render-card.paused").count() == 0)

    # delete a book
    page.goto(f"{base}/#/book/bk_pride005")
    page.wait_for_selector(".ch")
    page.get_by_role("button", name="Book actions").click()
    page.get_by_role("button", name="Delete book").click()
    page.wait_for_selector(".confirm")
    shot(page, "15c-delete-confirm")
    page.locator(".confirm .btn-danger").click()
    page.wait_for_url("**/#/library")
    page.wait_for_selector(".shelf-item .wc")
    page.wait_for_timeout(500)
    check("delete book removes it from the library", page.locator(".shelf-item", has_text="Pride and Prejudice").count() == 0)

    # ---------- offline download ----------
    page.goto(f"{base}/#/book/bk_time002")
    page.wait_for_selector(".ch")
    page.get_by_role("button", name="Book actions").click()
    page.wait_for_selector(".actions")
    shot(page, "16-actions")
    check("m4b link offered", page.locator("a.action", has_text=".m4b").count() == 1)
    page.get_by_role("button", name="Download for offline").click()
    page.wait_for_selector(".dl-row", timeout=5000)
    shot(page, "17-downloading")
    page.wait_for_selector(".badge-ok", timeout=60000)
    check("Available offline badge", "Available offline" in page.locator(".badge-ok").inner_text())
    shot(page, "17b-available-offline")
    n_cached = page.evaluate("caches.open('audio-v1').then(c => c.keys()).then(k => k.map(r => r.url.split('/').pop()))")
    check("finished book saved as one stream file + cover", len(n_cached) == 2 and any(u.startswith("stream.m4a") for u in n_cached), str(n_cached))

    # ---------- settings ----------
    page.goto(f"{base}/#/settings")
    page.wait_for_selector("#set-server")
    shot(page, "18-settings")
    page.get_by_role("button", name="Test connection").click()
    page.wait_for_selector(".kv", timeout=8000)
    check("test connection shows engine", "AuK-Flash" in page.locator(".kv").inner_text())
    shot(page, "19-settings-tested")

    # ---------- offline mode ----------
    page.goto(f"{base}/#/library")
    page.wait_for_selector(".shelf-item .wc")
    controlled = page.evaluate("!!navigator.serviceWorker.controller")
    check("service worker controls the page", controlled)
    ctx.set_offline(True)
    page.reload()
    page.wait_for_selector(".callout-offline", timeout=15000)
    rows = page.locator(".shelf-item").count()
    check("offline: library shows only downloaded books", rows == 1, str(rows))
    shot(page, "20-offline-library")
    page.locator(".shelf-item").first.click()
    page.wait_for_selector(".ch")
    page.locator(".ch", has_text="II. The Machine").click()
    wait_playing(page, timeout=15000)
    t0 = audio_time(page)
    page.wait_for_timeout(1500)
    t1 = audio_time(page)
    check("offline: downloaded chapter plays from cache", t1 > t0, f"{t0:.2f} -> {t1:.2f}")
    page.evaluate("const a = document.querySelector('audio'); a.currentTime = 200")
    page.wait_for_timeout(1200)
    t3 = audio_time(page)
    check("offline: seeking works (Range from cache)", t3 > 199, f"{t3:.2f}")
    shot(page, "21-offline-book-playing")
    ctx.set_offline(False)
    ctx.close()


def light_and_small(browser, base: str) -> None:
    for scheme in ("light", "dark"):
        for (w, hgt, tag) in ((390, 844, "390"), (375, 667, "375")):
            if scheme == "dark" and w == 390:
                continue
            ctx = new_context(browser, w, hgt, scheme)
            page = ctx.new_page()
            watch(page, f"{scheme}-{tag}")
            p = f"{scheme}-{tag}"
            page.goto(f"{base}/#/library")
            page.wait_for_selector(".shelf-item .wc")
            shot(page, f"{p}-library")
            page.goto(f"{base}/#/book/bk_frank001")
            page.wait_for_selector(".ch")
            page.locator(".ch", has_text="Letter 1").click()
            wait_playing(page)
            page.wait_for_timeout(1200)
            shot(page, f"{p}-book")
            page.locator(".mini-main").click()
            page.wait_for_selector(".np.open")
            page.wait_for_timeout(500)
            shot(page, f"{p}-now-playing")
            page.get_by_role("button", name="Close Now Playing").click()
            page.goto(f"{base}/#/new/bk_medit003/chapters")
            page.wait_for_selector(".sch")
            shot(page, f"{p}-chapters")
            page.goto(f"{base}/#/new/bk_medit003/voice")
            page.wait_for_selector(".vc")
            shot(page, f"{p}-voices")
            page.goto(f"{base}/#/new")
            page.wait_for_selector(".drop")
            shot(page, f"{p}-upload")
            page.goto(f"{base}/#/settings")
            page.wait_for_selector("#set-server")
            shot(page, f"{p}-settings")
            page.goto(f"{base}/#/book/bk_moby004")
            page.wait_for_selector(".render-card")
            shot(page, f"{p}-book-error")
            ctx.close()
    # empty library state
    ctx = new_context(browser, 390, 844, "dark")
    page = ctx.new_page()
    watch(page, "empty")
    ctx.route("**/api/books", lambda route: route.fulfill(status=200, content_type="application/json", body='{"books": []}') if route.request.method == "GET" else route.continue_())
    page.goto(f"{base}/#/library")
    page.wait_for_selector(".empty")
    shot(page, "22-library-empty")
    ctx.close()


def auth_flow(browser, auth_base: str) -> None:
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "auth")
    page.goto(f"{auth_base}/#/library")
    page.wait_for_url("**/#/settings", timeout=10000)
    check("401 routes to Settings", True)
    check("friendly access-code message", "Access code needed" in page.locator(".page-settings").inner_text())
    shot(page, "23-settings-needs-code")
    page.fill("#set-token", "secret")
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector(".callout-ok", timeout=8000)
    page.goto(f"{auth_base}/#/library")
    page.wait_for_selector(".shelf-item .wc", timeout=10000)
    check("library loads after entering code", True)
    cover_ok = page.evaluate("[...document.querySelectorAll('.cover img')].some(i => i.src.includes('token=secret'))")
    check("media URLs carry ?token=", cover_ok)
    ctx.close()
    # pairing link on a fresh context
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "pair")
    page.goto(f"{auth_base}/?pair=secret#/library")
    page.wait_for_selector(".shelf-item .wc", timeout=10000)
    check("?pair= stores the code and strips the query", "pair=" not in page.url and page.evaluate("localStorage.getItem('auk.token')") == '"secret"', page.url)
    ctx.close()
    # app hosted elsewhere (served from the auth server's origin) pointing at another server address
    ctx.close()


def cross_origin(browser, app_base: str, api_base: str) -> None:
    """App served from one origin (like GitHub Pages), API on another."""
    ctx = new_context(browser)
    page = ctx.new_page()
    watch(page, "cross")
    page.goto(f"{app_base}/?server={api_base}#/library")
    page.wait_for_selector(".shelf-item .wc", timeout=15000)
    saved = page.evaluate("localStorage.getItem('auk.server')")
    check("cross-origin: ?server= saved and stripped", saved == f'"{api_base}"' and "server=" not in page.url, f"{saved} {page.url}")
    page.wait_for_timeout(800)
    check("cross-origin: covers load", page.evaluate("[...document.querySelectorAll('.cover img')].some(i => i.complete && i.naturalWidth > 0)"))
    shot(page, "24-cross-origin-library")
    page.goto(f"{app_base}/#/book/bk_time002")
    page.wait_for_selector(".ch")
    page.locator(".ch", has_text="I. Introduction").click()
    wait_playing(page)
    check("cross-origin: chapter audio plays", True)
    meta = page.evaluate("(() => { const m = navigator.mediaSession && navigator.mediaSession.metadata; return m ? [m.title, m.artist, m.album, m.artwork.length && m.artwork[0].src] : null })()")
    check("media session metadata", bool(meta) and meta[0] == "I. Introduction" and meta[1] == "H. G. Wells" and meta[2] == "The Time Machine" and "cover.jpg" in (meta[3] or ""), str(meta))
    page.get_by_role("button", name="Book actions").click()
    page.get_by_role("button", name="Download for offline").click()
    page.wait_for_selector(".badge-ok", timeout=60000)
    check("cross-origin: download for offline", True)
    ctx.set_offline(True)
    page.goto(f"{app_base}/#/library")
    page.reload()
    page.wait_for_selector(".callout-offline", timeout=15000)
    page.locator(".shelf-item").first.click()
    page.locator(".ch", has_text="III. The Time Traveller Returns").click()
    wait_playing(page)
    t0 = audio_time(page)
    page.wait_for_timeout(1200)
    check("cross-origin: offline playback of cross-origin audio from cache", audio_time(page) > t0, f"{t0:.2f} -> {audio_time(page):.2f}")
    ctx.set_offline(False)
    ctx.close()


MS_SPY = """
(() => {
  window.__ms = { handlers: {}, pos: null };
  const ms = navigator.mediaSession;
  if (!ms) return;
  const orig = ms.setActionHandler.bind(ms);
  ms.setActionHandler = (a, fn) => { window.__ms.handlers[a] = fn; try { orig(a, fn); } catch (e) {} };
  if (ms.setPositionState) {
    const o2 = ms.setPositionState.bind(ms);
    ms.setPositionState = (st) => { window.__ms.pos = st; try { o2(st); } catch (e) {} };
  }
})();
"""


def stream_flow(browser, base: str) -> None:
    """Finished books play as ONE file; chapters are seeks to Chapter.offset."""
    import json
    import urllib.request

    def api(path):
        with urllib.request.urlopen(base + path) as r:
            return json.load(r)

    ctx = new_context(browser)
    ctx.add_init_script(MS_SPY)
    page = ctx.new_page()
    watch(page, "stream")
    tm = api("/api/books/bk_time002")
    off = {c["index"]: c["offset"] for c in tm["chapters"]}
    near = lambda a, b, tol=1.0: abs(a - b) <= tol  # noqa: E731
    cur_t = lambda: page.evaluate("document.querySelector('audio').currentTime")  # noqa: E731
    src = lambda: page.evaluate("document.querySelector('audio').src")  # noqa: E731

    page.goto(f"{base}/#/book/bk_time002")
    page.wait_for_selector(".ch")
    check("stream: downloads strip on finished book", page.locator(".file-tile").count() == 3)
    hrefs = page.eval_on_selector_all(".file-tile", "els => els.map(e => e.getAttribute('href'))")
    check("stream: MP3 / zip / m4b links", any("book.mp3" in h for h in hrefs) and any("chapters-mp3.zip" in h for h in hrefs) and any("book.m4b" in h for h in hrefs), str(hrefs))
    check("stream: per-chapter MP3 links", page.locator(".ch-dl:not([hidden])").count() == 12)
    disp = page.evaluate("(u) => fetch(u).then(r => r.headers.get('content-disposition'))", page.locator(".ch-dl").nth(2).get_attribute("href"))
    check("stream: chapter MP3 is an attachment", disp and "attachment" in disp and ".mp3" in disp, str(disp))
    shot(page, "stream-01-book-ready")

    page.locator(".ch", has_text="III. The Time Traveller Returns").click()
    wait_playing(page)
    s0 = src()
    check("stream: plays the single stream file", "stream.m4a" in s0, s0)
    check("stream: tapping a chapter seeks to its offset", near(cur_t(), off[2], 2.0), f"{cur_t():.1f} vs {off[2]}")
    check("stream: tapped row highlighted", "III." in page.locator(".ch.is-current").inner_text())

    # chapter boundary while playing: highlight + metadata switch, no file switch
    page.evaluate(f"document.querySelector('audio').currentTime = {off[3] - 1.5}")
    expect(page.locator(".ch.is-current")).to_contain_text("IV. Time Travelling", timeout=6000)
    check("stream: highlight moves at the chapter boundary", True)
    check("stream: no file switch at the boundary", src() == s0)
    meta = page.evaluate("navigator.mediaSession.metadata && navigator.mediaSession.metadata.title")
    check("stream: Media Session title follows the chapter", meta == "IV. Time Travelling", str(meta))
    done = page.evaluate("JSON.parse(localStorage.getItem('auk.pos:bk_time002')).done")
    check("stream: finished chapter marked listened", 2 in done, str(done))
    pos = page.evaluate("window.__ms.pos")
    check("stream: positionState is per chapter", pos and near(pos["duration"], off[4] - off[3], 0.5) and pos["position"] < 10, str(pos))

    # Media Session prev/next (lock screen, AirPods)
    page.evaluate("window.__ms.handlers.nexttrack()")
    page.wait_for_timeout(400)
    check("stream: Media Session nexttrack seeks to next offset", near(cur_t(), off[4], 0.6) and "V. In the Golden Age" in page.locator(".ch.is-current").inner_text(), f"{cur_t():.1f} vs {off[4]}")
    page.evaluate("window.__ms.handlers.previoustrack()")
    page.wait_for_timeout(400)
    check("stream: previoustrack at chapter start -> previous chapter", near(cur_t(), off[3], 0.6), f"{cur_t():.1f} vs {off[3]}")
    page.evaluate(f"document.querySelector('audio').currentTime = {off[3] + 20}")
    page.wait_for_timeout(400)
    page.evaluate("window.__ms.handlers.previoustrack()")
    page.wait_for_timeout(400)
    check("stream: previoustrack >3 s in -> restart chapter", near(cur_t(), off[3], 0.6) and "IV." in page.locator(".ch.is-current").inner_text(), f"{cur_t():.1f}")
    page.evaluate(f"document.querySelector('audio').currentTime = {off[3] + 5}")
    page.wait_for_timeout(300)
    page.evaluate("window.__ms.handlers.seekbackward({})")
    page.wait_for_timeout(400)
    check("stream: back 15 s crosses into the previous chapter", near(cur_t(), off[3] - 10, 0.8) and "III." in page.locator(".ch.is-current").inner_text(), f"{cur_t():.1f}")
    page.evaluate("window.__ms.handlers.seekto({seekTime: 60})")
    page.wait_for_timeout(300)
    check("stream: lock-screen seekto is chapter-relative", near(cur_t(), off[2] + 60, 0.8), f"{cur_t():.1f}")

    # Now Playing sheet in stream mode
    page.locator(".mini-main").click()
    page.wait_for_selector(".np.open")
    check("stream: Now Playing offers the chapter MP3", page.locator(".np-dl:not([hidden])").count() == 1 and "/audio/2.mp3" in page.locator(".np-dl").get_attribute("href"))
    page.get_by_role("button", name="Next chapter").click()
    expect(page.locator(".np-chapter")).to_have_text("IV. Time Travelling", timeout=3000)
    check("stream: Next chapter button seeks within the stream", near(cur_t(), off[3], 0.6) and src() == s0, f"{cur_t():.1f}")
    page.evaluate("""(() => { const r = document.querySelector('.scrub'); r.value = '500'; r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); })()""")
    page.wait_for_timeout(300)
    check("stream: scrubber spans offset..offset+duration", near(cur_t(), off[3] + (off[4] - off[3]) / 2, 1.5), f"{cur_t():.1f}")
    pos_line = page.locator(".np-pos").inner_text()
    check("stream: Chapter k of N", pos_line.startswith("Chapter 4 of 12"), pos_line)
    shot(page, "stream-02-now-playing")

    # sleep at end of chapter inside one file
    page.locator(".np-x", has_text="Sleep").click()
    page.get_by_role("button", name="End of chapter").click()
    page.evaluate(f"document.querySelector('audio').currentTime = {off[4] - 1.2}")
    page.wait_for_function("() => document.querySelector('audio').paused", timeout=8000)
    page.wait_for_timeout(300)
    check("stream: end-of-chapter sleep pauses at the boundary", near(cur_t(), off[4], 0.6) and page.locator(".np-chapter").inner_text().startswith("V."), f"{cur_t():.1f} vs {off[4]}")
    page.get_by_role("button", name="Close Now Playing").click()

    saved = page.evaluate("JSON.parse(localStorage.getItem('auk.pos:bk_time002'))")
    check("stream: position saved as stream time + chapter", saved["mode"] == "stream" and near(saved["time"], cur_t(), 1.0) and saved["i"] == 4, str({k: saved[k] for k in ("mode", "time", "i", "t")}))

    # restore after relaunch
    page.reload()
    page.wait_for_selector(".mini:not([hidden])")
    page.locator(".mini-play").click()
    wait_playing(page)
    check("stream: resumes at the saved stream position", near(cur_t(), saved["time"], 1.5) and "stream.m4a" in src(), f"{cur_t():.1f} vs {saved['time']}")
    page.locator(".mini-play").click()

    # a book finishing while listening in chapter mode switches at the next chapter change
    page.goto(f"{base}/#/book/bk_frank001")
    page.wait_for_selector(".ch")
    page.get_by_role("button", name="Book actions").click()
    page.wait_for_selector(".actions")
    dis = page.locator("button.action", has_text="Whole book as MP3")
    check("stream: whole-book downloads disabled while rendering", dis.count() == 1 and dis.is_disabled() and "finished" in dis.inner_text())
    shot(page, "stream-03-actions-rendering")
    page.get_by_role("button", name="Cancel").click()
    check("stream: chapter MP3 links while rendering", page.locator(".ch-dl:not([hidden])").count() >= 5)
    shot(page, "stream-04-book-rendering")
    page.locator(".ch", has_text="Letter 2").click()
    wait_playing(page)
    chapter_src = src()
    check("stream: rendering book plays chapter files", "/audio/2.m4a" in chapter_src, chapter_src)
    page.request.get(f"{base}/__mock/finish?id=bk_frank001")
    page.wait_for_timeout(4200)  # one book-screen poll
    check("stream: finishing the book doesn't interrupt playback", src() == chapter_src and not page.evaluate("document.querySelector('audio').paused"))
    fr = api("/api/books/bk_frank001")
    off3 = next(c["offset"] for c in fr["chapters"] if c["index"] == 3)
    page.evaluate("const a = document.querySelector('audio'); a.currentTime = a.duration - 1.0")
    page.wait_for_function("() => document.querySelector('audio').src.includes('stream.m4a')", timeout=10000)
    wait_playing(page)
    check("stream: switches to the stream at the next chapter", near(cur_t(), off3, 1.5) and "Letter 3" in page.locator(".ch.is-current").inner_text(), f"{cur_t():.1f} vs {off3}")
    page.get_by_role("button", name="Book actions").click()
    page.wait_for_selector(".actions")
    shot(page, "stream-05-actions-ready")
    page.get_by_role("button", name="Cancel").click()
    ctx.close()


def main() -> int:
    sys.modules.setdefault("e2e", sys.modules[__name__])  # e2e_stats shares these results/errors
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8799")
    ap.add_argument("--auth-base", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--app-base", default="", help="static copy of app/ on another origin, for the cross-origin test")
    args = ap.parse_args()
    book_pdf, scanned_pdf = make_pdfs()
    t = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--autoplay-policy=user-gesture-required"])
        if not args.only or args.only == "main":
            main_flow(browser, args.base, book_pdf, scanned_pdf)
        if not args.only or args.only == "themes":
            light_and_small(browser, args.base)
        if args.auth_base and (not args.only or args.only == "auth"):
            auth_flow(browser, args.auth_base)
        if not args.only or args.only == "stream":
            stream_flow(browser, args.base)
        if not args.only or args.only == "stats":
            import e2e_stats
            e2e_stats.stats_flow(browser, args.base)
        if args.only == "design":
            import e2e_stats
            e2e_stats.design_shots(browser, args.base)
        if args.app_base and (not args.only or args.only == "cross"):
            cross_origin(browser, args.app_base, args.base)
        if not args.only or args.only == "parts":
            import e2e_parts
            e2e_parts.run_all(browser, args.base)
        if not args.only or args.only == "nav":
            import e2e_nav
            e2e_nav.run_all(browser, args.base)
        if not args.only or args.only == "setup":
            import e2e_setup
            e2e_setup.run_all(browser, args.base)
        browser.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - t:.0f}s")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)
    print("console errors:" if errors else "no console errors")
    for e in errors:
        print("  ", e)
    if netfails:
        print("failed/aborted requests (info):")
        for n in netfails:
            print("  ", n)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
