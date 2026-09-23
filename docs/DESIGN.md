# AuK Audiobooks: design system and screen spec

Built with the **taste-skill** pack (`~/Claude-folder/.claude/skills/`):
`redesign-existing-projects` (primary: upgrade the existing vanilla-CSS app, don't rewrite),
`design-taste-frontend` (anti-slop rules and pre-flight check), and the readability,
navigation and colour rules of `imagegen-frontend-mobile`.

**Design read:** a personal listening-habit app for one motivated reader, with a colourful,
warm-but-premium language, built on the app's existing vanilla CSS, self-hosted type and a
vendored icon family.

**Dials:** DESIGN_VARIANCE 5 (mobile, single column, a few deliberate asymmetric moments) ·
MOTION_INTENSITY 6 (water, rings, staggered entries, all motivated) · VISUAL_DENSITY 4.

**Audit of v1, what changes:** the near-black + brass/amber palette (the skill's banned default
"premium" palette) → Lagoon palette. System serif titles → one sans family (Outfit). Uppercase
tracked labels above every section → sentence-case headings, eyebrows removed. Em-dashes in copy
→ none. Coloured dots before every status → icons or plain text. Library list → bookshelf with
water-fill covers. Hand-drawn icons → vendored Phosphor.

Constraints that stay: vanilla ES modules, no build step, no CDN at runtime, works offline,
iOS PWA safe areas, one persistent `<audio>` element (see player notes in the code).

---

## 1. Tokens

### Colour: "Lagoon" (one accent, plus one semantic warm colour for streaks only)

| Token | Dark (default when the system is dark) | Light |
| --- | --- | --- |
| `--bg` | `#0B1519` | `#F2F7F6` |
| `--surface` | `#111E23` | `#FBFDFD` |
| `--surface-2` | `#17282F` | `#E6EFEE` |
| `--line` | `rgb(234 245 243 / .08)` | `rgb(15 35 38 / .09)` |
| `--text` | `#EAF4F2` | `#0E1F23` |
| `--text-2` (secondary, ≥ 4.5:1) | `#A3B6B5` | `#4E6366` |
| `--accent` (buttons, active tab, links) | `#46D3C3`, with text `#062320` on it | `#0C8579`, with text `#FBFDFD` on it |
| `--accent-bright` (water, rings, heat top) | `#46D3C3` | `#2BC2B2` |
| `--ember` (streak flame only) | `#FF9466` | `#E4682F` |
| `--danger` | `#FF7A7A` | `#C93C3C` |

Heatmap ramp, level 0 → 4 ("more" = more intense; in light mode that means darker, in dark mode brighter):
dark `#17282F #164A4A #1B6F69 #2A9E93 #46D3C3` · light `#E3ECEB #BDE8E2 #7DD6CB #2FB3A5 #0C7A6F`.

Water: vertical gradient, top `color-mix(in srgb, var(--accent-bright) 50%, transparent)` → bottom
`color-mix(in srgb, #0C6F66 82%, transparent)`. Shadows are tinted with the background hue, never
pure black. No pure `#000`/`#fff`. Theme follows `prefers-color-scheme` for the whole app.

A fixed, `pointer-events:none` grain overlay at opacity .03 over the whole app is allowed
(never on scrolling containers). One soft radial lagoon glow behind the top of the Library is
allowed (static, not animated).

### Shape (one documented rule)
Interactive controls = full pill. Covers and cards = 14 px. Sheets = 24 px top corners.
Inputs = 12 px. Floating tab bar = pill.

### Type
One family: **Outfit** (variable, OFL), self-hosted woff2 in `app/fonts/` (download once from
`https://cdn.jsdelivr.net/npm/@fontsource-variable/outfit/files/outfit-latin-wght-normal.woff2`,
include `OFL.txt`). `font-display: swap`; fallback `-apple-system, system-ui, sans-serif`.
Numbers use `font-variant-numeric: tabular-nums`. Minimum text size 13 px; body 16 px.

| Role | Size / line | Weight | Tracking |
| --- | --- | --- | --- |
| Display (screen titles, big stats) | 30 / 34 | 650 | -0.02em |
| Title | 22 / 28 | 600 | -0.01em |
| Headline (section headings, sentence case) | 17 / 24 | 600 | 0 |
| Body | 16 / 24 | 400 | 0 |
| Label (buttons, tabs, meta) | 14 / 20 | 500 | 0 |
| Caption | 13 / 18 | 500 | 0 |

`text-wrap: balance` on headings, `pretty` on paragraphs.

### Icons
**Phosphor** (MIT), vendored: copy the needed SVGs' paths from
`https://cdn.jsdelivr.net/npm/@phosphor-icons/core@2/assets/{regular,fill}/<name>.svg` into
`js/icons.js` (keep the MIT notice). Regular weight for UI, fill for play/pause/flame/check.
One family, one stroke look. Replace all hand-drawn icon paths.

### Motion
UI transitions 240 ms `cubic-bezier(.16,1,.3,1)`; sheets 420 ms `cubic-bezier(.32,.72,0,1)`.
Press feedback `transform: scale(.97)`. Animate only `transform` and `opacity`.
Entry: list/grid items fade-up 16 px with a 40 ms stagger (first screen load only, not on every poll).
Everything collapses to instant/static under `prefers-reduced-motion: reduce`.

---

## 2. Navigation

Floating **tab bar** (fixed, glass: `backdrop-filter` allowed because it's fixed) with three tabs:
**Library**, **Stats**, **Settings** (Phosphor `books`, `calendar-dots` or `chart-bar`, `gear-six`,
labels under icons, active tab in `--accent`). The **mini player** floats just above the tab bar
(cover thumbnail with the book's water level, chapter title, play/pause) and expands into the
Now Playing sheet. New-book flow and book screen are pushed screens (tab bar hidden on the
new-book flow, visible on the book screen). Routes stay as they are, plus `#/stats`.

---

## 3. The water cover (signature component)

Used on: library shelf, continue-listening hero, book screen cover, mini player thumbnail
(simplified: no waves), Now Playing, finished-books row in Stats.

- Container: the cover image (or the generated typographic cover) at 2:3, radius 14, `overflow:hidden`,
  `isolation:isolate`, inner 1 px highlight border.
- Water body: absolutely positioned, full height, moved with
  `transform: translateY(calc(100% - var(--fill)))` so only `transform` animates. `--fill` =
  listened percentage (0-100%). On first appearance it rises from 0 to the value over 1.2 s
  (`cubic-bezier(.16,1,.3,1)`); later updates ease over 600 ms.
- Wave crest: two tileable SVG wave paths (200% wide) on the water's top edge, translating
  `-50%` in opposite directions (7 s and 11 s, linear infinite loops), opacities .9 and .45.
  A thin light caustic line along the crest. Static under reduced motion.
- Label: percentage inside the water at the bottom-left (`62%`, tabular, 13-15 px, on a
  readable scrim if needed). Hidden at 0%.
- 0% = no water. 100% = completely full, waves calm down (slower, lower amplitude), a check badge
  (Phosphor `check-circle` fill) in the top-right, and a one-time gentle shimmer the first time
  it's seen full.
- Percentage source: `furthest / total` from merged stats (see §6), clamped 0-100, rounded.
- While a book is still being created (not ready), the cover shows a creation state instead:
  dimmed cover with a circular progress ring + "Creating 43%". Listening progress (water) and
  creation progress (ring) are never shown with the same visual.

---

## 4. Screens

### Library ("your bibliothèque")
1. Greeting line ("Good evening" or "Good evening, Youssef" if a name is set in Settings).
2. **Today strip** (no card-in-card): today's goal ring (listened / goal minutes) with the minutes
   in the centre, the streak flame + day count, and one plain line of encouragement
   ("12 minutes to today's goal", "Goal reached. 4 days in a row."). Tap → Stats.
3. **Continue listening** hero: last played book, large water cover on the left, title, "Chapter 3 of 12",
   time left, a big pill play button. Hidden when nothing has been played.
4. **Shelf**: a segmented control (All, Listening, Finished, Creating) then a 2-column grid
   (3 columns ≥ 600 px) of water covers with title (max 2 lines) and author (1 line) below.
   Drafts show "Finish setup"; errors show a small warning icon + "Needs attention".
   The last cell is an **Add a book** tile (dashed outline, plus icon); a `+` icon button also
   sits in the header. No list dividers, no status dots.
5. Empty state: large Phosphor `books` icon in a soft accent circle, the three steps in one short
   paragraph, and the Add a book button.

### Stats
1. Title "Stats" + month switcher (caret buttons, "September 2026"). Cannot go past the current month.
2. **Hero row** (asymmetric, not equal cards): streak (ember flame, "12 days", "Longest 21 days")
   and today's goal ring. Tapping the ring opens a small sheet to set the daily goal
   (15, 30, 45, 60, 90 minutes).
3. **Month calendar heatmap**: weekday initials (Monday first), 7-column grid of rounded squares
   (radius 8) with the day number inside; colour = level (§6); today has an accent ring; future
   days are faint outlines; the selected day has a thicker ring. Legend underneath:
   "Less" + five swatches + "More", and a caption "Compared with your usual day (about 38 min)".
4. **Day detail** under the calendar (defaults to today, changes on tap): the date, time listened,
   time in the app, and each book listened that day (small water cover, title, minutes). If a
   book was finished that day: "Finished Frankenstein".
5. **This month** bento (uneven: one wide tile + smaller ones): time listened (with the change
   vs last month, "+18% vs August"), books finished (count + overlapping covers), time in the app,
   active days ("18 of 30").
6. **Finished this month**: horizontal scroll of full water covers with check badges.
7. **Milestones** (small, quiet row): first book finished, 7-day streak, 30-day streak, 10 hours,
   50 hours. Earned = accent icon; not yet = outline with what's left ("3 more days").
Empty month: a composed empty state ("Your first listening day will light up this calendar").

### Book screen
Keep the structure and all behaviour. Water cover at the top. Sentence-case section headings
("Chapters", "Download") instead of uppercase eyebrows. Download options as one wide primary
tile (Whole book MP3) + two smaller tiles (MP3 chapters zip, .m4b for Apple Books). Current
chapter row tinted with the accent at low opacity + equalizer glyph. Chapter rows: one sparse
hairline between rows, not boxed.

### Now Playing sheet
Large water cover, chapter title (title style), book title, scrubber in `--accent`, big pill
play/pause with the icon perfectly optically centred, ±15/30, prev/next chapter, speed, sleep
timer, chapter list button, "Download this chapter (MP3)".

### New book flow and Settings
Restyle with the same tokens and components; no behaviour changes. Settings gains
"Your name" (optional, for the greeting) and the daily goal.

---

## 5. Copy rules

- Zero em-dashes and en-dashes as separators anywhere visible. Use a comma, a period or a colon.
- At most one middle dot per line.
- Sentence case for headings and buttons. Short button labels (1-3 words).
- Encouraging but plain, no exclamation marks in system text, no filler ("Elevate", "Unlock").
- Examples: "12 minutes to today's goal", "Goal reached", "4 days in a row", "You listened 18% more
  than in August", "Finished Frankenstein".

---

## 6. Listening data (client). Contract: docs/API.md, "Listening stats"

- `js/stats.js` owns a local StatsDoc (`localStorage` key `auk.stats.v1`, wrapped in try/catch)
  and a device id (`auk.device`, random, generated once).
- **Listening seconds** from media-time progress, which stays correct when iOS throttles events
  while locked: on each `timeupdate` while playing, `d = currentTime - lastMediaTime`; if
  `0 < d < 30` and no seek happened, add `d / playbackRate` to today's `listen` and to that
  book's per-day seconds. Reset the baseline on `seeking`, `pause`, `ended`, `waiting`, source change.
- **Furthest** = max book-time position (stream mode: `currentTime`; chapter mode: sum of the
  durations of earlier included chapters + `currentTime`). **Total** = sum of included chapter
  durations when all are ready, else `est_seconds / pace`. `finished_at` = today the first time
  furthest ≥ 98% of total on a ready book. Store `title`/`author` too.
- **App time**: while `document.visibilityState === 'visible'`, add wall-clock deltas (capped at
  10 s per tick) every 5 s and on `visibilitychange`.
- Save locally every ~5 s; `PUT /api/stats/{device}` at most once a minute when changed and on
  `visibilitychange` → hidden (`fetch(..., {keepalive:true})`). `GET /api/stats` on launch and
  when Stats opens; cache other devices' docs locally for offline. Merge per API.md.
- **Usual day** = median `listen` of active days (≥ 60 s) in the previous 60 days; if fewer than 3
  such days, use the daily goal. **Level**: 0 if < 60 s; else ratio r = listen / usual:
  1 if r < 0.5, 2 if r < 1, 3 if r < 1.5, 4 otherwise.
- **Streak**: consecutive days with ≥ 5 minutes, ending today (or yesterday if today isn't active yet).

---

## 7. Pre-flight (adapted from design-taste-frontend §14)

- [ ] Zero `—`/`–` in visible text (grep the app).
- [ ] One accent colour (lagoon) everywhere; ember only on streak visuals.
- [ ] Shape rule followed (pills / 14 / 24 / 12).
- [ ] Every button and label passes WCAG AA in both themes; no text under 13 px.
- [ ] No uppercase tracked eyebrows; no decorative status dots.
- [ ] Loading (skeletons shaped like the final layout), empty and error states on Library and Stats.
- [ ] Motion only on transform/opacity, reduced-motion honoured, waves pause when off-screen
      (IntersectionObserver) to save battery.
- [ ] Dark and light screenshots at 390 px and 375 px, no horizontal overflow.
- [ ] Playback, offline, downloads and locked-screen stream mode behave exactly as before
      (existing e2e suites still pass).
