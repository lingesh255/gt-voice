import argparse

import uvicorn

from app.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GT voice agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Open http://{args.host}:{args.port} in Chrome or Edge")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
