"""Stats, water covers and the round-2 design: seeded data + checks + screenshot matrix.

Used by e2e.py (--only stats | design). Expected numbers are recomputed here with the
same rules as docs/DESIGN.md §6 so the UI is checked against an independent implementation.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import time
import urllib.request

from playwright.sync_api import expect

from e2e import check, new_context, shot, wait_playing, watch

DEVICE = "ios_e2eSeedDevice01"
OTHER = "mac_desk_7f3k2q9"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# seconds listened N days ago on this phone (0 = today). Missing = no listening that day.
PATTERN = {
    0: 1102.4, 1: 2250.7, 2: 845.3, 3: 1934.0, 4: 402.9, 6: 3120.5, 7: 1520.2, 10: 12384.7, 11: 2710.4,
    12: 95.6, 13: 1810.3, 14: 2480.9, 15: 1320.0, 16: 2045.5, 17: 2890.1, 18: 1760.8, 19: 2204.3, 20: 1655.0,
    22: 640.2, 24: 1450.7, 25: 2320.4, 27: 980.6, 28: 1750.3, 30: 2605.9, 31: 1210.4, 34: 3380.2, 35: 1044.8,
    36: 1870.0, 38: 720.5, 39: 2150.6, 41: 1590.3, 42: 1320.8, 44: 2440.1,
}
# the second device (a laptop) adds some listening
OTHER_PATTERN = {2: 600.0, 9: 1800.4, 26: 930.7, 33: 415.2}


def api(base, path):
    with urllib.request.urlopen(base + path) as r:
        return json.load(r)


def put(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="PUT", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def book_total(b):
    return sum(c["duration"] for c in b["chapters"] if c["include"])


def build(base):
    today = dt.date.today()
    k = lambda n: (today - dt.timedelta(days=n)).isoformat()  # noqa: E731
    walden = api(base, "/api/books/bk_walden07")
    tm = api(base, "/api/books/bk_time002")
    tw, ttm = book_total(walden), book_total(tm)
    fin = max(today - dt.timedelta(days=3), today.replace(day=1)).isoformat()
    days = {}
    for n, sec in PATTERN.items():
        books = {"bk_time002": round(sec * 0.4, 1), "bk_walden07": round(sec * 0.6, 1)} if n < 20 else {"bk_time002": sec}
        days[k(n)] = {"listen": sec, "app": round(sec * 1.18 + 95.3, 1), "books": books}
    now = int(time.time())
    local = {
        "version": 1,
        "days": days,
        "books": {
            "bk_time002": {"title": "The Time Machine", "author": "H. G. Wells", "furthest": ttm, "total": ttm, "finished_at": fin, "last_played_at": now - 3 * 86400},
            "bk_walden07": {"title": "Walden", "author": "Henry David Thoreau", "furthest": round(0.37 * tw, 1), "total": tw, "finished_at": None, "last_played_at": now - 3600},
        },
        "goal_minutes": 30,
        "updated_at": now - 100,
    }
    other = {
        "version": 1,
        "days": {k(n): {"listen": sec, "app": round(sec * 1.1, 1), "books": {"bk_walden07": sec}} for n, sec in OTHER_PATTERN.items()},
        "books": {
            "bk_walden07": {"title": "Walden", "author": "Henry David Thoreau", "furthest": round(0.2 * tw, 1), "total": tw, "finished_at": None, "last_played_at": now - 5 * 86400},
            "bk_time002": {"title": "The Time Machine", "author": "H. G. Wells", "furthest": ttm, "total": ttm, "finished_at": today.isoformat(), "last_played_at": now - 6 * 86400},
        },
        "goal_minutes": 45,
        "updated_at": now - 86400,
    }
    return local, other, fin


def merged_days(*docs):
    out = {}
    for d in docs:
        for key, v in d["days"].items():
            o = out.setdefault(key, {"listen": 0.0, "app": 0.0})
            o["listen"] += v["listen"]
            o["app"] += v["app"]
    return out


def usual(days, anchor, goal=30):
    vals = []
    for i in range(1, 61):
        v = days.get((anchor - dt.timedelta(days=i)).isoformat())
        if v and v["listen"] >= 60:
            vals.append(v["listen"])
    if len(vals) < 3:
        return goal * 60
    return statistics.median(vals)


def level(listen, u):
    if listen < 60:
        return 0
    r = listen / u
    return 1 if r < 0.5 else 2 if r < 1 else 3 if r < 1.5 else 4


def streak(days, today):
    ok = lambda d: days.get(d.isoformat(), {"listen": 0})["listen"] >= 300  # noqa: E731
    d = today if ok(today) else today - dt.timedelta(days=1)
    cur = 0
    while ok(d):
        cur += 1
        d -= dt.timedelta(days=1)
    keys = sorted(dt.date.fromisoformat(x) for x, v in days.items() if v["listen"] >= 300)
    longest = run = 0
    prev = None
    for x in keys:
        run = run + 1 if prev and x - prev == dt.timedelta(days=1) else 1
        longest = max(longest, run)
        prev = x
    return cur, max(longest, cur)


def long_js(sec):
    m = round(sec / 60)
    if m < 1:
        return "< 1 m" if sec > 0 else "0 m"
    hh, mm = divmod(m, 60)
    return f"{hh} h {mm:02d} m" if hh else f"{mm} m"


def dur_js(sec):
    m = round((sec or 0) / 60)
    return f"{m} min" if m < 60 else long_js(sec)


def seed(ctx, local):
    ctx.add_init_script(
        f"""(() => {{ try {{
          if (!localStorage.getItem('auk.stats.v1')) {{
            localStorage.setItem('auk.device', {json.dumps(json.dumps(DEVICE))});
            localStorage.setItem('auk.stats.v1', {json.dumps(json.dumps(local))});
            localStorage.setItem('auk.name', {json.dumps(json.dumps('Youssef'))});
          }}
        }} catch (e) {{}} }})();"""
    )


def fill_of(page, href):
    return page.evaluate(
        """(href) => { const el = document.querySelector(`.shelf-item[href="${href}"] .wc`); if (!el) return null;
           const w = el.querySelector('.wc-water'); const m = new DOMMatrix(getComputedStyle(w).transform);
           return {fill: el.style.getPropertyValue('--fill'), ratio: m.m42 / w.getBoundingClientRect().height, full: el.classList.contains('is-full'), empty: el.classList.contains('is-empty'), badge: getComputedStyle(el.querySelector('.wc-badge')).display}; }""",
        href,
    )


def stats_flow(browser, base):
    urllib.request.urlopen(base + "/__mock/reset").read()  # earlier flows change books and stats
    local, other, fin = build(base)
    put(base, f"/api/stats/{OTHER}", other)
    today = dt.date.today()
    days = merged_days(local, other)

    ctx = new_context(browser)
    seed(ctx, local)
    page = ctx.new_page()
    watch(page, "stats")

    # ---------- library: greeting, today strip, water covers ----------
    page.goto(f"{base}/#/library")
    page.wait_for_selector(".shelf-item .wc")
    page.wait_for_timeout(1800)  # rise animation (1.2 s) + first stats fetch
    check("greeting uses the name from Settings", page.locator(".lib-head h1").inner_text().endswith(", Youssef"), page.locator(".lib-head h1").inner_text())
    cur, longest = streak(days, today)
    left = max(0, -(-(30 * 60 - days[today.isoformat()]["listen"]) // 60))
    line = page.locator(".today-line").inner_text()
    check("today strip: minutes to goal", line == f"{int(left)} minutes to today's goal", line)
    check("today strip: streak", f"{cur} days in a row" in page.locator(".today-sub").inner_text(), page.locator(".today-sub").inner_text())
    f100 = fill_of(page, "#/book/bk_time002")
    f37 = fill_of(page, "#/book/bk_walden07")
    f0 = fill_of(page, "#/book/bk_callw08")
    check("water cover 100%: full, badge shown", f100 and f100["fill"] == "100%" and abs(f100["ratio"]) < 0.01 and f100["full"] and f100["badge"] != "none", str(f100))
    check("water cover ~37%: fill + transform", f37 and f37["fill"] == "37%" and abs(f37["ratio"] - 0.63) < 0.02, str(f37))
    check("water cover 0%: empty", f0 and f0["fill"] == "0%" and f0["empty"] and abs(f0["ratio"] - 1) < 0.01, str(f0))
    label = page.locator('.shelf-item[href="#/book/bk_frank001"] .wc-create-label').inner_text()
    check("creation ring on a book being created", page.locator('.shelf-item[href="#/book/bk_frank001"] .wc.is-creating .wc-ring').count() == 1 and label.startswith("Creating "), label)
    check("draft shows Finish setup", "Finish setup" in page.locator('.shelf-item[href*="bk_medit003"] .shelf-note').inner_text())
    check("error shows Needs attention", "Needs attention" in page.locator('.shelf-item[href="#/book/bk_moby004"] .shelf-note').inner_text())
    check("no continue hero before anything was played", page.locator(".hero-continue").count() == 0)
    shot(page, "r2-library-seeded")

    def shelf_ids():
        return page.eval_on_selector_all(".shelf .shelf-item", "els => els.map(e => e.getAttribute('href').match(/bk_[A-Za-z0-9]+/)[0])")

    page.get_by_role("tab", name="Finished").click()
    check("filter Finished", shelf_ids() == ["bk_time002"], str(shelf_ids()))
    page.get_by_role("tab", name="Listening").click()
    check("filter Listening", shelf_ids() == ["bk_walden07"], str(shelf_ids()))
    page.get_by_role("tab", name="Creating").click()
    creating = set(shelf_ids())
    check("filter Creating", creating == {"bk_frank001", "bk_war006", "bk_medit003", "bk_moby004", "bk_pride005", "bk_navig009", "bk_crime010", "bk_warpeace11"}, str(creating))
    shot(page, "r2-library-filter-creating")
    page.get_by_role("tab", name="All").click()

    # ---------- stats: current month ----------
    page.locator(".tabbar a[href='#/stats']").click()
    page.wait_for_selector(".cal-day")
    page.wait_for_timeout(600)
    check("month label", page.locator(".month-label").inner_text() == f"{MONTHS[today.month - 1]} {today.year}")
    check("next month disabled on the current month", page.locator(".month-switch button[aria-label='Next month']").is_disabled())
    u = usual(days, today)
    got = page.eval_on_selector_all(".cal-day:not(.future)", "els => els.map(e => [e.dataset.date, Number(e.dataset.level)])")
    bad = [(d, lv, level(days.get(d, {"listen": 0})["listen"], u)) for d, lv in got if lv != level(days.get(d, {"listen": 0})["listen"], u)]
    check(f"heatmap levels match the rule for all {len(got)} days", not bad, str(bad[:5]))
    spot = {n: level(days.get((today - dt.timedelta(days=n)).isoformat(), {"listen": 0})["listen"], u) for n in (0, 5, 10, 12) if (today - dt.timedelta(days=n)).month == today.month}
    check("heatmap spot check (long day = 4, zero day = 0)", spot.get(10, 4) == 4 and spot.get(5, 0) == 0, str(spot))
    cap = page.locator(".usual").inner_text()
    check("usual-day caption", cap == f"Compared with your usual day (about {max(1, round(u / 60))} min)", cap)
    check("streak count", page.locator(".streak-num b").inner_text() == str(cur) and f"Longest {longest}" in page.locator(".streak-sub").inner_text(), f"{cur}/{longest} vs {page.locator('.streak-tile').inner_text()}")
    check("today ring", page.locator(".ring-tile .gr-num").inner_text() == str(int(days[today.isoformat()]["listen"] // 60)))

    # vs last month (month to date)
    prev_m = (today.replace(day=1) - dt.timedelta(days=1))
    def month_listen(y, m, until):
        return sum(v["listen"] for key, v in days.items() if key.startswith(f"{y}-{m:02d}") and int(key[8:]) <= until)
    this = month_listen(today.year, today.month, today.day)
    last = month_listen(prev_m.year, prev_m.month, min(today.day, prev_m.day))
    pct = round((this - last) / last * 100)
    exp = f"{'+' if pct >= 0 else chr(0x2212)}{abs(pct)}% vs {MONTHS[prev_m.month - 1]}"
    check("vs last month delta", page.locator(".delta").inner_text() == exp, f"{page.locator('.delta').inner_text()} vs {exp}")
    check("month time listened", page.locator(".bt-listen .bt-value").inner_text() == dur_js(this), page.locator(".bt-listen .bt-value").inner_text())
    fin_names = page.eval_on_selector_all(".fin-row .fin-item .shelf-title", "els => els.map(e => e.textContent)")
    check("finished this month row", fin_names == ["The Time Machine"] and page.locator(".fin-row .wc.is-full").count() == 1, str(fin_names))
    check("books finished tile", page.locator(".bt-finished .bt-value").inner_text() == "1")
    check("finished_at merge keeps the earliest day", page.locator(".fin-row").count() == 1 and fin <= today.isoformat())
    shot(page, "r2-stats-current")

    # day detail
    n_long = 10 if (today - dt.timedelta(days=10)).month == today.month else 1
    d_long = today - dt.timedelta(days=n_long)
    page.locator(f".cal-day[data-date='{d_long.isoformat()}']").click()
    head = page.locator(".day-detail .dd-date").inner_text()
    exp_head = "Yesterday" if n_long == 1 else f"{DOW[d_long.weekday()]}, {d_long.day} {MONTHS[d_long.month - 1]}"
    check("day detail date", head == exp_head, f"{head} vs {exp_head}")
    listened = page.locator(".day-detail .dd-listen").inner_text()
    check("day detail time listened", listened == f"{dur_js(days[d_long.isoformat()]['listen'])} listened", listened)
    check("day detail time in the app", page.locator(".day-detail .dd-app").inner_text() == f"{dur_js(days[d_long.isoformat()]['app'])} in the app")
    titles = page.eval_on_selector_all(".day-detail .dd-book-title", "els => els.map(e => e.textContent)")
    check("day detail books", set(titles) == {"Walden", "The Time Machine"}, str(titles))
    page.evaluate("document.querySelector('.day-detail').scrollIntoView({block: 'center'})")
    page.wait_for_timeout(400)
    shot(page, "r2-stats-day-detail")
    fin_day = dt.date.fromisoformat(fin)
    page.locator(f".cal-day[data-date='{fin}']").click()
    check("day detail: finished that day", "Finished The Time Machine" in page.locator(".day-detail").inner_text())

    # goal sheet
    page.locator(".ring-tile").click()
    page.wait_for_selector(".sheet-goal")
    shot(page, "r2-stats-goal-sheet")
    page.locator(".sheet-goal .seg-btn", has_text="45").click()
    page.wait_for_timeout(500)
    check("goal sheet sets the goal", "of 45 min" in page.locator(".ring-tile").inner_text() and page.evaluate("JSON.parse(localStorage.getItem('auk.stats.v1')).goal_minutes") == 45)

    # past month
    page.get_by_role("button", name="Previous month").click()
    page.wait_for_timeout(400)
    check("month switch to last month", page.locator(".month-label").inner_text() == f"{MONTHS[prev_m.month - 1]} {prev_m.year}")
    anchor = today.replace(day=1)
    u2 = usual(days, anchor, goal=45)
    got2 = page.eval_on_selector_all(".cal-day", "els => els.map(e => [e.dataset.date, Number(e.dataset.level)])")
    bad2 = [(d, lv) for d, lv in got2 if lv != level(days.get(d, {"listen": 0})["listen"], u2)]
    check(f"past month: {len(got2)} days, levels match", len(got2) == prev_m.day and not bad2, str(bad2[:5]))
    check("past month: next enabled", not page.locator(".month-switch button[aria-label='Next month']").is_disabled())
    shot(page, "r2-stats-past-month")
    page.get_by_role("button", name="Next month").click()
    check("month switch back", page.locator(".month-label").inner_text() == f"{MONTHS[today.month - 1]} {today.year}")

    # ---------- listening accounting (mock) ----------
    page.goto(f"{base}/#/book/bk_callw08")
    page.wait_for_selector(".ch")
    doc = lambda: page.evaluate("JSON.parse(localStorage.getItem('auk.stats.v1'))")  # noqa: E731
    before = doc()["days"][today.isoformat()]["listen"]
    page.locator(".ch", has_text="Into the Primitive").click()
    wait_playing(page)
    page.wait_for_timeout(6000)
    page.locator(".mini-play").click()
    page.wait_for_timeout(400)
    after = doc()["days"][today.isoformat()]
    gained = after["listen"] - before
    check("listening time counts while playing", 4.0 <= gained <= 8.5, f"+{gained:.1f} s")
    check("per-book seconds recorded", after["books"].get("bk_callw08", 0) >= 4)
    page.wait_for_timeout(4000)
    still = doc()["days"][today.isoformat()]["listen"]
    check("no listening time while paused", abs(still - after["listen"]) < 0.01, f"{after['listen']} -> {still}")
    check("app time counted while visible", doc()["days"][today.isoformat()]["app"] > local["days"][today.isoformat()]["app"])
    page.wait_for_timeout(1200)
    devices = api(base, "/api/stats")["devices"]
    check("stats synced to the server (PUT)", DEVICE in devices and OTHER in devices, str(list(devices)))
    furthest = doc()["books"].get("bk_callw08", {}).get("furthest", 0)
    check("furthest position recorded", furthest >= 4, str(furthest))
    ctx.close()


def design_shots(browser, base):
    """Library, Stats (current + past), day detail, Book, Now Playing in dark/light at 390 and 375."""
    urllib.request.urlopen(base + "/__mock/reset").read()
    local, other, _ = build(base)
    put(base, f"/api/stats/{OTHER}", other)
    for scheme in ("dark", "light"):
        for w, hgt in ((390, 844), (375, 667)):
            tag = f"r2-{scheme}-{w}"
            ctx = new_context(browser, w, hgt, scheme)
            seed(ctx, local)
            page = ctx.new_page()
            watch(page, tag)
            page.goto(f"{base}/#/library")
            page.wait_for_selector(".shelf-item .wc")
            page.wait_for_timeout(1700)
            shot(page, f"{tag}-library")
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(500)
            shot(page, f"{tag}-library-shelf")
            page.goto(f"{base}/#/stats")
            page.wait_for_selector(".cal-day")
            page.wait_for_timeout(900)
            shot(page, f"{tag}-stats")
            page.evaluate("document.querySelector('.day-detail').scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1300)
            shot(page, f"{tag}-stats-detail")
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(500)
            shot(page, f"{tag}-stats-bottom")
            page.evaluate("window.scrollTo(0, 0)")
            page.get_by_role("button", name="Previous month").click()
            page.wait_for_timeout(500)
            shot(page, f"{tag}-stats-past")
            page.goto(f"{base}/#/book/bk_walden07")
            page.wait_for_selector(".ch")
            page.wait_for_timeout(1500)
            shot(page, f"{tag}-book")
            page.locator(".ch", has_text="Reading").click()
            wait_playing(page)
            page.wait_for_timeout(900)
            shot(page, f"{tag}-book-playing")
            page.locator(".mini-main").click()
            page.wait_for_selector(".np.open")
            page.wait_for_timeout(1600)
            shot(page, f"{tag}-now-playing")
            page.get_by_role("button", name="Close Now Playing").click()
            # with the mini player showing, the last row of every tab must scroll clear of both bars
            for route, last in (("#/library", ".shelf > :last-child"), ("#/stats", ".stats-body > :last-child"), ("#/book/bk_walden07", ".ch-list > :last-child"), ("#/settings", ".page-settings > :last-child")):
                page.goto(f"{base}/{route}")
                page.wait_for_selector(last)
                page.wait_for_timeout(700)
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_timeout(500)
                gap = page.evaluate(
                    """(sel) => { const el = document.querySelector(sel).getBoundingClientRect();
                       const bars = [...document.querySelectorAll('.tabbar, .mini')].filter(b => getComputedStyle(b).display !== 'none' && !b.hidden).map(b => b.getBoundingClientRect().top);
                       return Math.min(...bars) - el.bottom; }""",
                    last,
                )
                check(f"{tag}: {route} scrolls fully above tab bar + mini player", gap >= 0, f"{gap:.0f}px")
            shot(page, f"{tag}-settings")
            ctx.close()
