"""The wiki's page model: paths, classes, slugs and the page catalog (flat and nested layouts).

A page is any wiki/**/<slug>.md. A category index is a page whose stem equals its directory's
name. Class comes from the top-level directory (schema/core.md). Slugs are globally unique.
"""
import os
import re

from .common import read_text

PAGE_RE = re.compile(r"^wiki/(?:[^./][^/]*/)+[^./][^/]*\.md$")
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
RECORD_CATEGORIES = ("decisions",)
GOTCHA_CATEGORIES = ("gotchas",)
EXCLUDED_CATEGORIES = ("discussions",)


def norm_page(path):
    p = str(path).strip().strip("`").replace("\\", "/")
    marker = "/llake/wiki/"
    if marker in p:
        p = p[p.index(marker) + len("/llake/"):]
    for prefix in ("./", "llake/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    if not p.startswith("wiki/"):
        p = "wiki/" + p
    if not p.endswith(".md"):
        p += ".md"
    if ".." in p.split("/"):
        return None
    return p if PAGE_RE.match(p) else None


def slug(page):
    return os.path.basename(page)[:-3]


def category_dir(page):
    return os.path.dirname(page)


def is_index(page):
    return slug(page) == os.path.basename(category_dir(page))


def index_of(page):
    d = category_dir(page)
    return d + "/" + os.path.basename(d) + ".md"


def top_category(page):
    return page.split("/")[1]


def page_class(page):
    if not page or not PAGE_RE.match(page):
        return "excluded"
    top = top_category(page)
    if top in EXCLUDED_CATEGORIES:
        return "excluded"
    if is_index(page):
        return "index"
    if top in RECORD_CATEGORIES:
        return "record"
    if top in GOTCHA_CATEGORIES:
        return "gotcha"
    return "state"


def wiki_pages(llake_root):
    root = os.path.join(llake_root, "wiki")
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if not name.endswith(".md") or name.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), llake_root).replace(os.sep, "/")
            if PAGE_RE.match(rel):
                out.append(rel)
    return sorted(out)


def slug_map(llake_root):
    return {slug(p): p for p in wiki_pages(llake_root)}


def page_links(text):
    return [m.group(1).strip().split("/")[-1] for m in LINK_RE.finditer(text)]


def frontmatter_scalars(text):
    """Top-level `key: value` scalars of the frontmatter; {} when absent or unterminated. Lenient."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fm
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$", line)
        if m:
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            fm[m.group(1)] = v
    return {}


def has_category(llake_root, page):
    return os.path.exists(os.path.join(llake_root, index_of(page)))


def catalog_md(llake_root, planned=()):
    """Every non-discussion page with its description, grouped by category directory."""
    lines, current = [], None
    pages = [p for p in wiki_pages(llake_root) if page_class(p) != "excluded"]
    for page in sorted(pages, key=lambda p: (category_dir(p), p)):
        cat = category_dir(page)[len("wiki/"):]
        if cat != current:
            lines.append("\n## {}\n".format(cat))
            current = cat
        desc = frontmatter_scalars(read_text(os.path.join(llake_root, page))).get("description", "")
        lines.append("- `{}` — {}".format(page, desc.strip()))
    for p in planned:
        lines.append("- `{}` — (planned new page) {}".format(p["page"], p.get("description") or ""))
    return "\n".join(lines).lstrip() + "\n"
