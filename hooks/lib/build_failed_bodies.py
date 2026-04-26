#!/usr/bin/env python3
"""LoreLake ingest v2 — build the FAILED_PAGE_BODIES slot for the fixer prompt.

Reads a failed.json list and a wiki root, emits a Markdown-formatted block per
failed slug whose page still exists. Used by ingest-v2.sh to build the embedded
slot so the fixer agent doesn't need to Read each page itself.

Output format (stdout):

    ### <slug-1>
    ```
    <full page content>
    ```

    ### <slug-2>
    ```
    <full page content>
    ```

Empty / missing pages are skipped silently.

CLI:
    python3 build_failed_bodies.py <failed.json> <wiki-root>
    Exit 0 always. Diagnostics on stderr.
"""
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        print("usage: build_failed_bodies.py <failed.json> <wiki-root>", file=sys.stderr)
        sys.exit(2)
    failed_path = Path(sys.argv[1])
    wiki_root = Path(sys.argv[2])

    try:
        failed = json.loads(failed_path.read_text())
    except (IOError, OSError, json.JSONDecodeError) as e:
        print(f"build_failed_bodies: cannot read {failed_path}: {e}", file=sys.stderr)
        sys.exit(2)

    blocks = []
    for entry in failed:
        slug = entry.get("slug", "")
        if not slug:
            continue
        matches = list(wiki_root.rglob(f"{slug}.md"))
        if not matches:
            continue
        try:
            body = matches[0].read_text()
        except (IOError, OSError):
            continue
        blocks.append(f"### {slug}\n```\n{body}\n```\n")

    print("\n".join(blocks))


if __name__ == "__main__":
    main()
