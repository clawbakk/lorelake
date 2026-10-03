"""Shared fixtures for the ingest v3 tests: temp git projects with a LoreLake wiki."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "hooks" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                          text=True, check=True).stdout


def page_text(title, description, body, status="current", related=()):
    rel = "".join('\n  - "[[{}]]"'.format(r) for r in related) if related else " []"
    return ('---\ntitle: "{t}"\ndescription: "{d}"\ntags: [test]\ncreated: 2026-01-01\n'
            'updated: 2026-01-01\nstatus: {s}\nrelated:{r}\n---\n\n# {t}\n\n{b}\n'
            ).format(t=title, d=description, s=status, r=rel, b=body)


def index_text(category, rows):
    lines = ["---", 'title: "{}"'.format(category.title()),
             'description: "Category index for {}."'.format(category), "tags: [{}]".format(category),
             "created: 2026-01-01", "updated: 2026-01-01", "---", "", "# {}".format(category.title()), "",
             "Pages about {}.".format(category), "", "{} pages.".format(len(rows)), "",
             "| Page | Description |", "|---|---|"]
    lines += ["| [[{}]] | {} |".format(s, d) for s, d in rows]
    return "\n".join(lines) + "\n"


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)
    return path


def commit(repo, files=None, msg="change", remove=()):
    for rel, text in (files or {}).items():
        write(repo, rel, text)
    for rel in remove:
        (Path(repo) / rel).unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def _desc(text):
    m = re.search(r'^description:\s*"?(.*?)"?\s*$', text, re.M)
    return m.group(1) if m else ""


def make_project(tmp_path, src=None, pages=None, v3=None, include=("src/",)):
    """A git repo with src/ files and llake/; the cursor (untracked file) sits on the first commit."""
    repo = Path(tmp_path) / "proj"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    git(repo, "config", "commit.gpgsign", "false")
    cfg = {"ingest": {"pipeline": "v3", "branch": "main", "include": list(include),
                      "schedule": {"enabled": False}, "v3": dict(v3 or {})}}
    write(repo, "llake/config.json", json.dumps(cfg, indent=2))
    write(repo, "llake/index.md", "# Test LoreLake Index\n")
    write(repo, "llake/log.md", "# LoreLake Activity Log\n")
    pages = dict(pages or {})
    cats = {}
    for rel, text in pages.items():
        write(repo, "llake/wiki/" + rel, text)
        cats.setdefault(os.path.dirname(rel), []).append(rel)
    for d, rels in cats.items():
        idx = d + "/" + os.path.basename(d) + ".md"
        if idx in pages:
            continue
        rows = [(os.path.basename(r)[:-3], _desc(pages[r])) for r in sorted(rels)]
        write(repo, "llake/wiki/" + idx, index_text(os.path.basename(d), rows))
    for rel, text in (src or {"src/app.py": "print('hello')\n"}).items():
        write(repo, rel, text)
    write(repo, ".gitignore", "llake/.state/\nllake/last-ingest-sha\n")
    sha = commit(repo, msg="initial")
    write(repo, "llake/last-ingest-sha", sha + "\n")
    (repo / "llake" / ".state").mkdir(parents=True, exist_ok=True)
    return repo
