"""Drive the PWA against the REAL server (demo engine) in headless Chromium.

Start the server first, e.g. (separate data dir, own port):
  cd ../server && AUDIOBOOK_DATA=/tmp/x AUDIOBOOK_DEMO_DELAY=0.3 .venv/bin/python -m audiobook --engine demo --host 127.0.0.1 --port 8797
Then:  ../server/.venv/bin/python e2e_real.py --base http://127.0.0.1:8797
"""

from __future__ import annotations

import argparse
import sys
import time

from playwright.sync_api import expect, sync_playwright

import e2e
from e2e import check, errors, results, shot, wait_playing, watch, audio_time


def run(base: str) -> None:
    book_pdf, _ = e2e.make_pdfs()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = e2e.new_context(browser)
        page = ctx.new_page()
        watch(page, "real")

        page.goto(f"{base}/#/library")
        page.wait_for_selector(".page-library")
        page.wait_for_timeout(1200)
        check("real: demo banner", page.locator(".callout-demo").count() == 1)
        shot(page, "real-01-library")

        page.locator(".lib-head a[href='#/new']").click()
        page.set_input_files("input[type=file]", str(book_pdf))
        page.wait_for_url("**/chapters", timeout=60000)
        page.wait_for_selector(".sch")
        n = page.locator(".sch").count()
        check("real: chapters detected by the server", n >= 6, str(n))
        shot(page, "real-02-chapters")
        page.get_by_role("button", name="Choose a voice").click()
        page.wait_for_url("**/voice")
        page.wait_for_selector(".vc")
        shot(page, "real-03-voices")
        james = page.locator(".vc", has_text="James")
        james.locator(".vc-play").click()
        expect(james.locator(".vc-play.playing")).to_have_count(1, timeout=120000)
        check("real: voice preview generated and playing", True)
        shot(page, "real-04-voice-playing")

        page.locator(".flow-footer .btn").click()
        page.wait_for_selector(".confirm-sheet")
        shot(page, "real-05-confirm")
        page.get_by_role("button", name="Create audiobook").click()
        page.wait_for_url("**/#/book/**")
        page.wait_for_selector(".ch:not([disabled])", timeout=180000)
        check("real: first chapter becomes playable while rendering", True)
        shot(page, "real-06-book-rendering")
        page.locator(".ch:not([disabled])").first.click()
        wait_playing(page, timeout=20000)
        t0 = audio_time(page)
        page.wait_for_timeout(1500)
        check("real: AAC chapter plays", audio_time(page) > t0, f"{t0:.2f} -> {audio_time(page):.2f}")
        shot(page, "real-07-book-playing")

        # wait for the whole book
        deadline = time.time() + 300
        while time.time() < deadline and page.locator(".render-card").count():
            page.wait_for_timeout(2000)
        check("real: book finishes rendering (demo engine)", page.locator(".render-card").count() == 0)
        page.wait_for_timeout(600)

        # finished book: downloads + single-file stream mode
        book_id = page.url.rsplit("/", 1)[-1]
        bk = page.evaluate("(id) => fetch('/api/books/' + id).then(r => r.json())", book_id)
        check("real: stream_url + offsets when finished", bool(bk.get("stream_url")) and all(isinstance(c.get("offset"), (int, float)) for c in bk["chapters"] if c["include"]), str(bk.get("stream_url")))
        check("real: downloads strip (MP3, zip, m4b)", page.locator(".file-tile").count() == 3)
        for sel in (".file-tile", ".ch-dl:not([hidden])"):
            href = page.locator(sel).first.get_attribute("href")
            info = page.evaluate("(u) => fetch(u).then(r => r.status + ' ' + (r.headers.get('content-type') || '') + ' ' + (r.headers.get('content-disposition') || ''))", href)
            check(f"real: {sel} download is an attachment", info.startswith("200") and "attachment" in info, info)
        zip_href = page.locator(".file-tile", has_text="zip").get_attribute("href")
        zinfo = page.evaluate("(u) => fetch(u).then(r => r.status + ' ' + r.headers.get('content-type'))", zip_href)
        check("real: chapters zip downloads", zinfo.startswith("200") and "zip" in zinfo, zinfo)
        shot(page, "real-07b-book-ready")
        inc = [c for c in sorted(bk["chapters"], key=lambda c: c["index"]) if c["include"]]
        target = inc[2]
        page.locator(".ch", has_text=target["title"]).click()
        wait_playing(page)
        src = page.evaluate("document.querySelector('audio').src")
        check("real: finished book plays the stream file", "stream.m4a" in src, src)
        check("real: chapter tap seeks to offset", abs(audio_time(page) - target["offset"]) < 2.5, f"{audio_time(page):.1f} vs {target['offset']}")
        nxt = inc[3]
        page.evaluate(f"document.querySelector('audio').currentTime = {nxt['offset'] - 1.2}")
        expect(page.locator(".ch.is-current")).to_contain_text(nxt["title"], timeout=8000)
        check("real: highlight switches at the chapter boundary (no file switch)", page.evaluate("document.querySelector('audio').src") == src)

        # listening-time accounting (stream mode on the real server)
        today = page.evaluate("(() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; })()")
        listen = lambda: page.evaluate("(k) => { const d = JSON.parse(localStorage.getItem('auk.stats.v1') || '{}'); return ((d.days || {})[k] || {}).listen || 0; }", today)  # noqa: E731
        page.wait_for_timeout(1200)  # let the seek settle and one save happen
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")  # flush to localStorage
        t0 = listen()
        page.wait_for_timeout(6000)
        page.locator(".mini-play").click()  # pause (also saves)
        page.wait_for_timeout(500)
        t1 = listen()
        check("real: listening time counts while playing", 4.0 <= t1 - t0 <= 8.5, f"+{t1 - t0:.1f} s")
        page.wait_for_timeout(4000)
        check("real: no listening time while paused", abs(listen() - t1) < 0.01, f"{t1:.1f} -> {listen():.1f}")
        dev = page.evaluate("JSON.parse(localStorage.getItem('auk.device'))")
        devices = page.evaluate("fetch('/api/stats').then(r => r.json()).then(d => Object.keys(d.devices))")
        check("real: stats document synced (PUT /api/stats)", dev in devices, str(devices))
        page.goto(f"{base}/#/stats")
        page.wait_for_selector(".cal-day")
        page.wait_for_timeout(600)
        check("real: Stats screen renders today's listening", "listened" in page.locator(".day-detail").inner_text() and page.locator(".cal-day.today").count() == 1)
        shot(page, "real-07c-stats")
        page.go_back()
        page.wait_for_selector(".ch")
        page.get_by_role("button", name="Book actions").click()
        page.wait_for_selector(".actions")
        check("real: m4b link offered when ready", page.locator("a.action", has_text=".m4b").count() == 1)
        href = page.locator("a.action", has_text=".m4b").get_attribute("href")
        status = page.evaluate("(u) => fetch(u, {method: 'GET'}).then(r => r.status + ' ' + r.headers.get('content-type'))", href)
        check("real: m4b downloads", status.startswith("200"), status)
        page.get_by_role("button", name="Download for offline").click()
        page.wait_for_selector(".badge-ok", timeout=60000)
        keys = page.evaluate("caches.open('audio-v1').then(c => c.keys()).then(k => k.map(r => r.url.split('/').pop()))")
        check("real: available offline (stream file cached)", any(k.startswith("stream.m4a") for k in keys), str(keys))
        shot(page, "real-08-offline-ready")

        page.locator(".mini-main").click()
        page.wait_for_selector(".np.open")
        before = page.locator(".np-chapter").inner_text()
        page.get_by_role("button", name="Next chapter").click()
        page.wait_for_timeout(400)
        check("real: next chapter (paused stays paused)", page.locator(".np-chapter").inner_text() != before)
        page.locator(".np-play").click()
        wait_playing(page)
        check("real: next chapter plays", True)
        shot(page, "real-09-now-playing")
        page.get_by_role("button", name="Close Now Playing").click()

        page.goto(f"{base}/#/library")
        page.wait_for_selector(".shelf-item .wc")
        ctx.set_offline(True)
        page.reload()
        page.wait_for_selector(".callout-offline", timeout=15000)
        check("real: offline library from snapshot", page.locator(".shelf-item").count() == 1)
        page.locator(".shelf-item").first.click()
        page.wait_for_selector(".offline-slot .badge-ok")  # fully downloaded: no "only downloaded chapters" note
        page.wait_for_selector(".ch")
        page.locator(".ch:not([disabled])").nth(1).click()
        wait_playing(page)
        t0 = audio_time(page)
        page.wait_for_timeout(1200)
        check("real: offline AAC playback from cache", audio_time(page) > t0, f"{t0:.2f} -> {audio_time(page):.2f}")
        check("real: offline playback uses the cached stream", "stream.m4a" in page.evaluate("document.querySelector('audio').src"))
        shot(page, "real-10-offline")
        ctx.set_offline(False)
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8797")
    args = ap.parse_args()
    t = time.time()
    run(args.base)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - t:.0f}s")
    for name, _, detail in failed:
        print("  FAILED:", name, detail)
    print("console errors:" if errors else "no console errors")
    for e in errors:
        print("  ", e)
    return 1 if failed or errors else 0


if __name__ == "__main__":
    sys.exit(main())
