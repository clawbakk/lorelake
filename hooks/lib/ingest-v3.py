#!/usr/bin/env python3
"""LoreLake ingest v3 — command-line entry. Subcommands live in ingest_v3/cli.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ingest_v3.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
