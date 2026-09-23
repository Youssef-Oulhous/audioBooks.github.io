#!/usr/bin/env bash
# Packs the project for the free-GPU notebook: upload the zip to Google Drive > AuKAudiobooks.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$HOME/auk-audiobook.zip}"
cd "$(dirname "$HERE")"
NAME="$(basename "$HERE")"
rm -f "$OUT"
zip -qr "$OUT" "$NAME" \
  -x "$NAME/server/.venv/*" "$NAME/server/data/*" "$NAME/server/demo-data/*" "$NAME/server/local-data/*" \
     "$NAME/server/tests/fixtures/*.pdf" "$NAME/app-tests/screenshots/*" "$NAME/**/__pycache__/*" "$NAME/.git/*"
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo "Upload it to Google Drive > AuKAudiobooks, then run deploy/colab_audiobook.ipynb in Colab."
