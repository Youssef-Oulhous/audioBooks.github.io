# AuK Audiobooks

Turn any PDF book into a narrated audiobook on your iPhone, voiced by
[Tencent Hunyuan **AuK**](https://github.com/Tencent-Hunyuan/AuK), an open-source
speech model you run yourself. No paid voice APIs.

1. **Add a book**: pick a PDF on your iPhone.
2. **Check the chapters**: they're detected automatically. Rename them, or untick
   "Contents", "Index" and similar.
3. **Pick a voice**: nine narrators with audio previews, or describe your own
   ("an old storyteller with a warm, raspy voice…").
4. **Listen**: chapters appear as they finish. Jump between chapters, set the
   speed and a sleep timer, and keep listening with the phone locked, using the
   lock-screen and AirPods controls.
5. **Download** it as **MP3**: the whole book as one file, every chapter as its
   own MP3 (one at a time or all as a zip), or one `.m4b` with chapter markers for
   Apple Books. You can also keep a book in the app for offline listening.
6. **Build the habit**: your library is a bookshelf where every cover fills with
   water up to how much you've listened, completely full when you finish. The Stats
   tab has a monthly calendar that glows more intensely on days you listened more than
   usual. It also shows your streak, a daily goal ring, time listened, time in the
   app and books finished each month.

## The app, hosted

The iPhone app is published from this repository to GitHub Pages:

**https://youssef-oulhous.github.io/audioBooks.github.io/**

Open it in Safari and tap `Share > Add to Home Screen`. The icon then always opens, even with no
server running, and plays the books you downloaded for offline listening. Every push to `main`
that touches `app/` republishes it (`.github/workflows/pages.yml`).

It can also be hosted anywhere else that serves static files. On **Vercel**, import this
repository and press Deploy: `vercel.json` tells it to publish the `app/` folder with no build
step (or set "Root Directory" to `app` in the import screen and leave the build settings empty).

The app needs a server to make books, and a page served over https can only talk to an https
server, so the server needs a secure address:

```bash
bash deploy/run_local.sh --tunnel     # your PC, free https address (Cloudflare quick tunnel)
```

It prints `https://….trycloudflare.com/?pair=<code>`. Open that once on your phone and the app
remembers the address and code (or type them into Settings). The address changes each run; the
app's own address never does. For a book in minutes rather than hours, run the Colab notebook
below instead and use the link it prints.

## How it fits together

```
 iPhone (web app on your Home Screen)                GPU server (yours)
 ┌─────────────────────────────┐   HTTPS   ┌────────────────────────────────────────┐
 │ library · chapters · voices │ ───────▶  │ PDF → text → chapters → ~15 s chunks   │
 │ player with chapter list    │ ◀───────  │ AuK voices each chunk → chapter audio  │
 │ MP3 / M4B downloads         │  audio    │ → whole book .m4b + .mp3 with chapters │
 └─────────────────────────────┘           └────────────────────────────────────────┘
```

**Why a server?** AuK is a 1.5-billion-parameter model plus a 3-billion-parameter
encoder (Qwen2.5-Omni-3B). It needs about 17–25 GB of GPU memory, far more than
an iPhone or a normal laptop has. The phone app is the remote control and player;
the voices are made on a machine with a big NVIDIA GPU. That can be one you own
(RTX 3090/4090) or one you rent by the hour.

**Why a web app, not an App Store app?** Native iOS apps must be built on a Mac with
Xcode and signed with an Apple developer account. A Progressive Web App installs
straight from Safari ("Add to Home Screen"). It opens full-screen like a normal
app, works offline for downloaded books, and plays in the background with
lock-screen controls.

### How the narration keeps one voice for the whole book

- A voice is a written description (AuK's *Instruct TTS*). The first time you
  pick a voice, AuK invents it and reads one sample sentence. That sample is your
  preview. Don't like it? Tap "Another take" for a new sample.
- Every chunk of the book is then spoken by copying the voice in that sample
  (AuK's *zero-shot TTS*), so the narrator stays the same from page 1 to the
  end. The sample is frozen into the book, so re-rolling a voice later can't
  change a book halfway through.
- AuK fits the reference sample and the new speech into one 30-second window.
  So the text is cut into chunks of about 10–18 seconds at sentence boundaries.
  Each chunk gets a length that matches the chosen narrator's natural pace,
  using AuK's own seconds-per-byte calibration. Chunks are then trimmed,
  levelled to the same loudness and joined with natural pauses: short between
  sentences, longer after paragraphs, scene breaks and headings.
- Numbers, dates, money and times are written out the way a narrator reads them
  ("$1,250.50" → "one thousand two hundred fifty dollars and fifty cents",
  "1984" → "nineteen eighty-four"). Chapter titles are announced ("Chapter twelve.").

### Listening with the phone locked

Once a book is finished, the player streams it as **one continuous audio file**.
Chapters are simply positions in that file. iOS keeps a web app's audio going
while the phone is locked, but it can stop the app from switching to a new file
at the end of a track. With one continuous file there is no switch, so playback
runs from chapter to chapter until the book ends or the sleep timer stops it.
The lock screen and AirPods skip buttons jump between chapters.

While a book is still being created, chapters play as separate files, one after
another. That also continues on a locked phone in normal use.

The whole-book files (continuous stream, `.m4b` and `.mp3`) are encoded once from
lossless chapter masters, so chapter positions are exact to the sample: no drift
at the seams, however long the book.

### Parts, chapters, sections and page numbers

A full book is read the way it is printed. The PDF's bookmarks give parts (Part I, Part II),
the chapters inside them, and any deeper headings, which become sections you can jump to.
Page numbers are the ones **printed in the book**, taken from its page labels or from the numbers
in its margins, so "go to page 59" means the page the book itself calls 59. Pages that start in
the middle of a paragraph are timed inside their chunk, so playback starts within about a second
of that page's first words.

### Listen while it's being made, and start from any page

Nobody should wait for a whole book. The narration is made in ~30-second parts, and each
part can be played as soon as it's ready. Since the voice is made faster than you listen,
it stays ahead of you. With Kokoro on a laptop, listening starts in about a minute. Finished
chapters, and then the finished book, take over at exactly the same second.

**Go to page:** type a page number of your PDF. If that page is already narrated it jumps
straight there. If not, that page is narrated next and starts playing when ready, and the
rest keeps being made from there. You can also pick a start page when creating the
audiobook. The player shows which page you're on. Pages that fall in skipped chapters
(contents, notes) move to the next narrated page, with a note. This works for books made
before this feature too, with page positions estimated from the text.

### Stats and progress

The app measures your listening itself: real playback time, not book time, and it
stays accurate with the screen locked. It also counts the time the app is open,
your furthest point in each book, and the day you finish each one. Everything is
kept on the phone and backed up to your server, one record per device, so stats
survive reinstalling the app and combine if you listen on more than one device. A
calendar day's shade compares that day with your usual day (the median of your
recent listening days). Your streak counts days with at least 5 minutes of listening.

### How chapters are found

In order of reliability:

1. **PDF bookmarks** (the outline most published PDFs have).
2. **Printed headings** such as "Chapter 12", "CHAPTER XII", "Letter 3", "Prologue",
   "Part One" or "第三章". Contents pages and running headers are ignored.
3. **Large-type headings**: short lines set noticeably bigger than the body text.
4. Nothing found: the book is split into ~20-minute **sections**.

It also removes page numbers and running headers/footers, re-joins words
hyphenated across lines and sentences split across pages, repairs decorative
drop caps ("W e were" → "We were") and drops footnote markers.

Detection is audited against each book's own table of contents by
`server/tests/audit_structure.py` (add `--no-outline` to hide the bookmarks and force the
printed-heading path). On full books it finds every chapter, part and section, keeps the
reading order, and loses no text:

| Book | Pages | Found (bookmarks) | Found (bookmarks removed) | Words kept |
| --- | --- | --- | --- | --- |
| War and Peace | 2,299 | 355 chapters in 17 books | 355 chapters | 99.9% |
| Crime and Punishment | 767 | 42 chapters in 7 parts | 41 chapters in 6 parts | 99.9% |
| Pride and Prejudice | 479 | 61 chapters | 61 chapters | 99.9% |
| Think Python | 244 | 22 chapters, 218 sections | 22 chapters | 99.8% |
| Frankenstein / Alice / Gatsby | 111-277 | 28 / 12 / 9 chapters | same | 98.8-99.9% |

The words not kept are title pages, copyright and contents, which are skipped on purpose;
nothing falls between chapters. Two traps this covers: novels restart chapter numbering in
every part ("Chapter I" exists seven times in Crime and Punishment), and running headers repeat
a chapter's title on every page, sometimes punctuated differently from the heading itself.
The first chapter of a part announces it ("Part two. Chapter one.").

## Real voices on your PC, no GPU (Kokoro via Pipecat)

The server can also narrate with **Kokoro**, through the same TTS service that
[Pipecat](https://github.com/pipecat-ai/pipecat) voice agents use (`KokoroTTSService`).
It runs on an ordinary CPU, needs no API key and costs nothing:

```bash
bash deploy/run_local.sh              # then open http://localhost:8000
bash deploy/run_local.sh --tunnel     # plus a free https address, for the hosted app and for use away from home
```

- **Voices:** two, to keep it simple: Heart (American, female) and George (British, male).
  Kokoro has more, including French, Spanish, Italian, Portuguese, Mandarin, Hindi and
  Japanese speakers. Each one is a single line in `server/audiobook/voices.py`.
- **Speed:** about 1.2-1.5× real time on a 2018 laptop CPU (i5-8350U), measured while
  developing. A 1-hour book takes about 45 minutes and a 10-hour novel about 7 hours.
  It runs in the background and resumes after a restart.
- **Difference from AuK:** Kokoro's voices are fixed speakers, so there's no "describe
  your own voice" and no "another take". The app hides those options automatically.
  AuK on a GPU sounds more expressive; Kokoro is the free, run-anywhere option.
- The model (~330 MB) downloads on first start into `~/.cache/pipecat/kokoro-onnx`.
  Kokoro needs Python 3.11+ for Pipecat 1.x (the script sets that up).

## Free GPU: a whole book in minutes (Google Colab)

Kokoro on a laptop CPU runs at about real time, so a full book takes hours. The same model on a
GPU is 10 to 50 times faster, and Google Colab gives a free one. `deploy/colab_audiobook.ipynb`
runs this server there, keeps your library in your Google Drive, and prints a link for your phone:

1. `bash deploy/make_colab_zip.sh` and upload the zip to Google Drive, into `MyDrive/AuKAudiobooks/`
   (or push this project to your own GitHub and set `GIT_URL` in the notebook instead).
2. Open `deploy/colab_audiobook.ipynb` in [Colab](https://colab.research.google.com/),
   choose `Runtime > Change runtime type > T4 GPU`, then `Runtime > Run all`.
3. The last cell prints `https://….trycloudflare.com/?pair=<code>`. Open it on your iPhone in
   Safari and tap `Share > Add to Home Screen`.
4. Add a book. A full novel takes roughly 10 to 20 minutes; listening starts after about a minute.
5. Before closing Colab, tap **Download for offline** (or **Download MP3**) on finished books.

Your books, audio and stats live in `MyDrive/AuKAudiobooks/data`, so the next session has them
all. The access code stays the same; only the link changes. Free Colab sessions end after a few
hours and a GPU is not guaranteed at busy times. Kaggle notebooks (30 GPU hours a week) work the
same way if Colab is busy: same steps, without the Drive folder.

## Try it now without a GPU (demo mode)

The demo engine makes soft placeholder tones instead of speech. Everything else is
real: chapters, chunking, pauses, audio files, the `.m4b` and the whole app. It's
for trying the flow, not for listening.

```bash
bash deploy/try_demo.sh
```

Open http://localhost:8000. On your iPhone (same Wi-Fi) use the address the
script prints. An orange banner in the app reminds you it's the demo engine.

## Run it for real on a GPU

You need a Linux machine with an NVIDIA GPU with **24 GB or more** (RTX 3090/4090,
A5000, L4 at 24 GB with offload, A40/L40S/A100 at 48 GB+) and about 40 GB of free disk.

### Easiest: rent one on RunPod

1. Create a pod with a PyTorch template, a 24 GB+ GPU, 50 GB of disk, and
   **HTTP port 8000** exposed.
2. Open the pod's terminal and get this project onto it: `git clone` your copy,
   or upload the folder.
3. Run:
   ```bash
   cd auk-audiobook
   bash deploy/setup_gpu.sh      # installs AuK + server, downloads ~19 GB of weights (once)
   bash deploy/start.sh          # prints your iPhone link: https://<pod>-8000.proxy.runpod.net/?pair=<code>
   ```
4. Open that link on your iPhone in **Safari**. Tap **Share → Add to Home Screen**.

The pair link saves the server address and access code in the app. On 24 GB cards
the setup script turns on AuK's CPU offload automatically.

### Your own PC with a GPU

Same two scripts. For the HTTPS address the iPhone needs (to install the app and
use offline mode), start with a free Cloudflare tunnel:

```bash
bash deploy/start.sh --tunnel     # prints https://….trycloudflare.com/?pair=<code>
```

### Settings (server/.env)

| Variable | Default | Meaning |
| --- | --- | --- |
| `AUDIOBOOK_ENGINE` | `auk` | `auk` (AuK on a GPU), `kokoro` (Pipecat Kokoro on any CPU), `demo` (placeholder tones) |
| `AUK_VARIANT` | `flash` | `flash` = AuK-Flash (4 steps, fast); `base` = AuK Base (32 steps, ~8× slower, slightly better) — needs `setup_gpu.sh --base` |
| `AUK_CKPT_DIR` | `../AuK/ckpts` | folder with `AuK-Flash/`, `AuK/`, `Qwen2.5-Omni-3B/` |
| `AUK_CPU_OFFLOAD` | `0` | `1` lowers peak GPU memory from ~25 GB to ~17 GB |
| `AUK_DEVICE` | auto | e.g. `cuda:1` |
| `KOKORO_DEVICE` | `auto` | `cpu` to keep Kokoro off the GPU even when one is present |
| `KOKORO_MODEL_PATH` / `KOKORO_VOICES_PATH` | auto-downloaded | keep the Kokoro model somewhere permanent (e.g. Google Drive) |
| `AUDIOBOOK_TOKEN` | *(empty)* | access code the app must send; **set it** whenever the server is reachable from the internet |
| `AUDIOBOOK_DATA` | `server/data` | where books, audio and voice samples are kept |
| `AUDIOBOOK_MAX_CHUNK_SECONDS` | `18` | longest chunk sent to AuK |
| `AUDIOBOOK_AAC_BITRATE` | `64k` | quality of the streaming / `.m4b` audio |
| `AUDIOBOOK_MP3_BITRATE` | `64k` | quality of the MP3 downloads |

## How long does a book take?

A typical novel is 70,000–100,000 words, which is 8–11 hours of listening. The
server measures its own speed as it works, and the app shows a live
"about X left" once the first chunks are done. The speed depends on your GPU and
on Flash vs Base; I couldn't benchmark it here because this laptop has no GPU.
Rendering keeps going if you close the app. If the server restarts it resumes
where it stopped, and finished chapters are never voiced twice.

## Limits worth knowing

- **Languages:** AuK is trained mainly on **English and Chinese**. Other languages
  (French, Arabic, …) may sound wrong or heavily accented. The app warns you and
  suggests trying one chapter first.
- **Scanned PDFs** (page photos without selectable text) need OCR first. The
  server tells you when that's the case.
- **Unusual layouts:** multi-column pages, heavy footnotes and tables are read
  in their extracted order. Untick chapters you don't want read.
- **Locked-screen playback** works from the Home Screen app, as described above.
  If you ever want a completely separate player, download the **MP3** (plays in
  Files, VLC, any podcast or music app) or the **.m4b** (Apple Books, with chapters).
  On iPhone, downloads go to Files → Downloads.

## Project layout

```
server/audiobook/
  main.py            HTTP API (docs/API.md) + serves the app
  pdf_extract.py     PDF → clean paragraphs (headers/footers, hyphens, drop caps)
  chapters.py        chapter detection (bookmarks → headings → font size → sections)
  textprep.py        number reading, sentence splitting, AuK duration estimates, chunking
  voices.py          narrator presets, custom voices, voice samples
  renderer.py        background queue: chunks → AuK → chapter .m4a/.mp3/.flac → book .m4b + .mp3 (resumable)
  engine_manager.py  loads the model, serialises GPU use, previews jump the queue
  engines/auk.py     AuK via its own Python API (AukInfer)
  engines/kokoro.py  Kokoro via Pipecat's KokoroTTSService (CPU)
  engines/demo.py    placeholder engine
  audio.py           trimming, loudness, WAV, ffmpeg AAC / MP3 / FLAC / M4B with chapter markers
  stats.py           per-device listening stats storage (merged by the app)
server/tests/        pytest: text prep, chapter detection on real books, full API flow
app/                 the iPhone web app (plain HTML/CSS/JS, no frameworks, works offline)
deploy/              setup_gpu.sh · start.sh (AuK on a GPU) · run_local.sh (Kokoro on this PC)
                     colab_audiobook.ipynb + make_colab_zip.sh (free GPU) · try_demo.sh
docs/API.md          the API contract between app and server
docs/DESIGN.md       design system and screen spec (made with the taste-skill pack)
```

Run the tests: `cd server && bash tests/get_real_fixtures.sh && .venv/bin/python -m pytest -q`
