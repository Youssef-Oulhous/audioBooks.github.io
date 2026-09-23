"""python -m audiobook [--host 0.0.0.0] [--port 8000]"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="AuK Audiobooks server")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--engine", choices=["auk", "kokoro", "demo"], help="override AUDIOBOOK_ENGINE")
    parser.add_argument("--token", help="access code the app must send (overrides AUDIOBOOK_TOKEN)")
    args = parser.parse_args()
    if args.engine:
        os.environ["AUDIOBOOK_ENGINE"] = args.engine
    if args.token is not None:
        os.environ["AUDIOBOOK_TOKEN"] = args.token

    import uvicorn

    uvicorn.run("audiobook.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
