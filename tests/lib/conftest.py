"""sys.path setup for tests/lib: this directory (v3_helpers) and hooks/lib (ingest_v3, frontmatter)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for path in (HERE, os.path.join(os.path.dirname(os.path.dirname(HERE)), "hooks", "lib")):
    if path not in sys.path:
        sys.path.insert(0, path)
