# Audiobook server API

All endpoints are JSON unless noted. Base path: `/api`.

## Auth

If the server was started with `AUDIOBOOK_TOKEN`, every `/api/*` request must carry it,
either as header `X-Access-Token: <token>` or query `?token=<token>` (audio/cover/m4b
URLs use the query form because `<audio>`/`<img>` can't send headers).
Missing/wrong token -> `401 {"detail": "..."}`.

`GET /api/status` is the only endpoint that works without a token; it reports
`auth_required: true|false` and `authorized: true|false` so the app can ask for a code.

## Errors

Non-2xx responses: `{"detail": "human readable message"}`.

## Status

`GET /api/status`
```json
{
  "app": "AuK Audiobooks", "version": "1.0.0",
  "auth_required": true, "authorized": true,
  "engine": {
    "name": "auk",                  // "auk" | "demo"
    "label": "AuK-Flash on cuda",   // human label
    "state": "ready",               // "idle" | "loading" | "ready" | "error"
    "detail": null,                 // error text when state == "error"
    "is_demo": false,               // true => placeholder beeps, not real speech
    "realtime_factor": 12.5,        // audio seconds produced per wall second (null until measured)
    "voice_mode": "described",      // "described" (AuK: voices from descriptions) | "preset" (Kokoro: fixed speakers)
    "custom_voices": true,          // false => hide "Design your own voice"
    "languages": ["en", "zh"]       // book languages this engine narrates well
  },
  "queue": ["bk_ab12cd34"]          // book ids waiting/rendering, first = rendering now
}
```

## Voices

`GET /api/voices` -> `{"voices": [Voice...]}`

```json
Voice = {
  "id": "james",
  "name": "James",
  "language": "en",                 // "en" | "zh"
  "gender": "male",                 // "male" | "female"
  "tagline": "Deep, calm classic narrator",
  "description": "A calm male narrator in his fifties ...",   // the AuK voice prompt
  "custom": false,
  "preview_url": "/api/voices/james/preview.wav?v=3" ,       // null until a sample exists
  "take": 3,                         // increments on each re-roll
  "accent": "British",               // "" for AuK voices; e.g. "American", "British", "French" for Kokoro
  "retakable": true                  // false for Kokoro's fixed voices => hide "Another take"
}
```

`POST /api/voices/{id}/preview` body `{"retake": false}` -> `Voice`
Generates the voice sample if missing (or a new one when `retake: true`), then returns the
voice with `preview_url` set. Synchronous: can take a few seconds, or minutes the very
first time while the model loads (app should show a spinner and allow a long timeout).

`GET /api/voices/{id}/preview.wav` -> `audio/wav` (Range supported)

`POST /api/voices` body `{"name": "Grandpa", "description": "An old man with ...", "language": "en", "gender": "male"}`
-> `Voice` (custom voice; no sample yet — call preview next)

`DELETE /api/voices/{id}` -> `{"ok": true}` (custom voices only)

## Books

```json
Book = {
  "id": "bk_ab12cd34",
  "title": "Frankenstein", "author": "Mary Shelley",
  "language": "en",
  "language_warning": null,          // string when the book isn't English/Chinese
  "pages": 312,                      // pages in the PDF file
  "page_numbering": "printed",       // "printed" = the numbers the book prints (what its index uses) | "pdf"
  "first_page": 1, "last_page": 288, // the range the reader can type (in that numbering)
  "parts": ["Part I", "Part II"],    // ordered part titles, [] when the book has no parts
  "created_at": 1790000000,
  "cover_url": "/api/books/bk_ab12cd34/cover.jpg",
  "detection": {"method": "outline", "note": "Found 24 chapters in the PDF's bookmarks."},
                                     // method: "outline" | "headings" | "fontsize" | "sections"
  "status": "draft",                 // "draft" | "queued" | "rendering" | "paused" | "ready" | "error"
  "error": null,
  "voice_id": null, "voice_name": null, "pace": 1.0,
  "made_with": "kokoro",             // engine that narrated the finished audio ("auk" | "kokoro" | "demo"), null if unknown
  // engine traits also appear in /api/status: voice_mode, custom_voices, languages
  "voice_available": true,           // false when the book's voice belongs to another engine => offer "Change voice"
  "progress": {                      // null while draft
    "done_chunks": 120, "total_chunks": 900,
    "percent": 13.3,
    "eta_seconds": 5400,             // null when unknown
    "current_chapter": 3,
    "stage": "voicing"               // "voicing" | "packaging" (building the whole-book files) | "done"
  },
  "total_words": 75000,
  "est_seconds": 30000,              // estimated narration length of INCLUDED chapters
  "audio_seconds": 1830.2,           // total length of chapters rendered so far
  "m4b_url": null,                   // "/api/books/<id>/book.m4b?v=N" when status == ready (download, Apple Books)
  "stream_url": null,                // "/api/books/<id>/stream.m4a?v=N" when ready: the WHOLE book as ONE continuous
                                     //   audio file for playback; chapters are positions inside it (Chapter.offset)
  "mp3_url": null,                   // "/api/books/<id>/book.mp3?v=N" when ready: whole book, one MP3 (download)
  "mp3_zip_url": null,               // "/api/books/<id>/chapters-mp3.zip?v=N" when ready: every chapter as its own MP3
  "chapters": [Chapter...]
}

Chapter = {
  "index": 0,
  "title": "Letter 1",
  "include": true,                   // user can untick (e.g. "Contents", "Index")
  "words": 1830,
  "est_seconds": 700,
  "preview": "To Mrs. Saville, England. St. Petersburgh, Dec. 11th, 17—. You will rejoice to hear...",
  "status": "ready",                 // "pending" | "rendering" | "ready" | "skipped"
  "done_chunks": 12, "total_chunks": 12,
  "duration": 702.4,                 // seconds, when ready
  "audio_url": "/api/books/bk_ab12cd34/audio/0.m4a?v=1",  // when ready (streams while the rest of the book renders)
  "mp3_url": "/api/books/bk_ab12cd34/audio/0.mp3?v=1",    // when ready (download, attachment)
  "offset": 1830.2,                  // start of this chapter inside Book.stream_url, seconds (null until the book is ready)
  "first_page": 31, "last_page": 38,  // pages this chapter covers, as the reader sees them (null for roman pages)
  "first_page_label": "31",           // the exact printed label ("xii" for front matter)
  "part": "Part II",                  // the part this chapter belongs to (null if none)
  "sections": [{"title": "2.1 Tides", "page": 33, "page_label": "33"}],  // jump points inside the chapter
  "page_marks": [[31, 0.6], [32, 48.2]],  // when ready: [page as the reader sees it, seconds into THIS chapter]
  "parts": [Part...],                // while the chapter is NOT ready: ~30 s pieces, playable as soon as each is made
  "part_durations": [31.4, 29.8]     // chapter ready but book not yet: exact part lengths (chapter time = sum of earlier parts)
}

Part = {
  "index": 0,
  "ready": true,
  "url": "/api/books/<id>/audio/<chapter>/part/<n>.m4a?v=...",   // when ready (Range supported)
  "duration": 31.4,
  "first_page": 31, "last_page": 32, "first_page_label": "31",
  "marks": [[31, 0.6], [32, 18.3]]    // [page as the reader sees it, seconds into THIS part]
}
A chapter plays as: its parts in order while being made; its chapter audio_url once ready (identical audio:
the chapter file is exactly its parts joined, so part n starts at the sum of the earlier parts' durations).
Parts are not necessarily made in order: a start page / "go to page" gets voiced first.
```

`GET /api/books` -> `{"books": [Book...]}` (chapters included, newest first)

`POST /api/books` multipart form, field `file` = the PDF -> `Book` (status `draft`)
Errors: 400 not a PDF / no selectable text (scanned book) / empty.

`GET /api/books/{id}` -> `Book`

`PATCH /api/books/{id}` body (all optional):
`{"title": "...", "author": "...", "chapters": [{"index": 0, "include": false, "title": "New title"}]}`
-> `Book`. Only allowed while status is `draft`, `paused`, `error` or `ready`
(changing chapters of a finished book marks it `paused` so it can be re-rendered).

`POST /api/books/{id}/render` body `{"voice_id": "james", "pace": 1.0}` -> `Book` (status `queued`)
`pace`: 0.85 (relaxed) .. 1.15 (brisk); 1.0 normal. Re-posting on a paused book resumes;
already-rendered chunks are reused if voice and pace didn't change.

`POST /api/books/{id}/render` also accepts `"start_page": 57` to voice that page first.

`GET /api/books/{id}/locate?page=57` -> where a page is in the audio. `page` is in the book's own
numbering (`Book.page_numbering`); a page label such as `xii` is accepted too:
```json
{"page": 57, "resolved_page": 57,          // pages in skipped chapters resolve to the next narrated page
 "page_label": "57", "pdf_page": 79,       // the printed label, and the page of the PDF file
 "skipped_chapter_title": null,            // set when the page sits in a chapter that isn't narrated
 "chapter": 4, "chapter_title": "Chapter 3", "part_title": "Part II",
 "chapter_ready": false, "chapter_time": null,   // seconds into the chapter audio (when the chapter is ready)
 "book_time": null,                               // seconds into stream_url (when the book is ready)
 "part": 12, "part_ready": false, "part_url": null, "part_time": null}   // while rendering
```
409 if the book has never been started (no voice yet).

`POST /api/books/{id}/prioritize` body `{"page": 57}` -> same shape as locate. Tells the renderer to
voice that page's part next (then carry on forward). Poll `/locate` (or the book) until `part_ready`.

`POST /api/books/{id}/pause` -> `Book` (status `paused`)

`DELETE /api/books/{id}` -> `{"ok": true}`

## Media

Playback rule for the app: when `stream_url` is set (book finished), play that single file and
implement chapters as seeks to `offset` (current chapter = last chapter whose offset <= currentTime).
There is then no file switch at chapter boundaries, which is what keeps playback going on a locked
iPhone. While the book is still rendering, play the per-chapter `audio_url` files in sequence.

- `GET /api/books/{id}/cover.jpg` -> image/jpeg (first page of the PDF)
- `GET /api/books/{id}/audio/{index}.m4a` -> audio/mp4 (one chapter, AAC, Range supported)
- `GET /api/books/{id}/audio/{index}.mp3` -> audio/mpeg (one chapter, attachment "03 - Chapter title.mp3")
- `GET /api/books/{id}/stream.m4a` -> audio/mp4 whole book, inline, Range supported (for the player)
- `GET /api/books/{id}/book.m4b` -> audio/mp4 whole book with embedded chapter markers
  (`Content-Disposition: attachment`; opens in Apple Books with chapters)
- `GET /api/books/{id}/book.mp3` -> audio/mpeg whole book with ID3 chapter markers (attachment)
- `GET /api/books/{id}/chapters-mp3.zip` -> application/zip of all chapter MP3s (attachment)

## Listening stats (for the Stats screen and the library's progress covers)

The app measures everything itself and keeps it in local storage; the server only stores one
document per device so stats survive a reinstall and combine across devices.

`GET /api/stats` -> `{"devices": {"<device_id>": StatsDoc, ...}}`

`PUT /api/stats/{device_id}` body `StatsDoc` -> `{"ok": true}` (replaces that device's document;
device_id = 8-64 chars `[A-Za-z0-9_-]`, generated once by the app and kept in localStorage)

```json
StatsDoc = {
  "version": 1,
  "days": {                                   // local calendar date of the device
    "2026-09-21": {
      "listen": 1830.5,                       // seconds of actual playback (wall clock, not book time)
      "app": 2400,                            // seconds the app was open in the foreground
      "books": {"bk_ab12cd34": 1830.5}        // listening seconds per book that day
    }
  },
  "books": {
    "bk_ab12cd34": {
      "title": "Frankenstein", "author": "Mary Shelley",   // kept so history survives deleting the book
      "furthest": 5230.2,                     // furthest point reached, seconds of book audio
      "total": 27600,                         // book length in seconds (sum of included chapters, or estimate)
      "finished_at": "2026-09-21",            // first day furthest >= 98% of total, else null
      "last_played_at": 1790000000            // unix seconds
    }
  },
  "goal_minutes": 30,                         // daily listening goal
  "updated_at": 1790000000
}
```

Merging rule (done in the app): this device's live local document + every *other* device's
document from the server. Days: add `listen`, `app` and per-book seconds. Books: max `furthest`,
max `total`, earliest non-null `finished_at`, max `last_played_at`. `goal_minutes`: the most
recently updated document wins.
