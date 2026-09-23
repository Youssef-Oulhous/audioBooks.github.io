#!/usr/bin/env bash
# Downloads three typeset public-domain novels used by the chapter-detection tests.
set -euo pipefail
cd "$(dirname "$0")/fixtures"
for book in frankenstein alices-adventures-in-wonderland the-great-gatsby crime-and-punishment pride-and-prejudice war-and-peace; do
  [ -f "$book.pdf" ] || curl -sfL -A "Mozilla/5.0" -o "$book.pdf" "https://www.planetebook.com/free-ebooks/$book.pdf"
done
[ -f thinkpython2.pdf ] || curl -sfL -o thinkpython2.pdf "https://greenteapress.com/thinkpython2/thinkpython2.pdf"
ls -la
