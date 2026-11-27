import argparse
import asyncio
import logging
import os
import sys

import uvicorn

from app.checks import FAIL, OK, check_data, run_checks
from app.config import Settings
from app.server import create_app

MARKS = {OK: "ok  ", "skip": "--  ", FAIL: "FAIL"}


def print_checks(checks) -> None:
    for check in checks:
        print(f"  [{MARKS[check.status]}] {check.name:7} {check.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GT voice agent")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--check", action="store_true", help="Test the data, model key and speech key, then exit")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.check:
        print("Checking setup...")
        checks = asyncio.run(run_checks(settings))
        print_checks(checks)
        sys.exit(1 if any(c.status == FAIL for c in checks) else 0)

    data = check_data(settings)
    print_checks([data])
    if data.status == FAIL:
        sys.exit(1)
    model = settings.model_label if settings.llm_ready else "not configured (add GEMINI_API_KEY to .env)"
    print(f"  model   {settings.llm_provider}: {model}")
    print(f"  speech  {'Sarvam' if settings.sarvam_api_key else 'browser (Chrome or Edge)'}")
    print(f"\nOpen http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port} in Chrome or Edge\n")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
