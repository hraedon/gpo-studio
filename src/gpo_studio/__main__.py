"""Command-line entry point."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local GPO Studio web workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default="gpo-studio.db")
    args = parser.parse_args()
    os.environ["GPO_STUDIO_DB"] = args.database
    uvicorn.run("gpo_studio.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
