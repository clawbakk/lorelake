"""Removed and added names, the hit index, and the change leads (spec §4). Code only, $0, no parsers.

  simple names   identifiers of >= 4 chars with an internal capital, an underscore or a digit, or ALLCAPS
  compound names identifier parts joined by `.` or `-`
  removed        zero word-boundary matches in the watched source at head (+ deleted/renamed-away basenames)
  added          the mirror: zero matches at base
Names whose use only shrank (still present at head) are not leads (spec §4) and are dropped.
"""
import difflib
import os
import re
import subprocess
from collections import Counter

from .common import HIT_INDEX_CAP, dump_json, git, read_text, write_text
from .wiki import page_class, wiki_pages

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
COMPOUND = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[.-][A-Za-z_][A-Za-z0-9_]*)+")
STRLIT = re.compile(r"'([^'\\\n]{10,})'|\"([^\"\\\n]{10,})\"|`([^`\n]{10,})`")
TEMPLATE_HOLE = re.compile(r"\$\{[^}]*\}")
CB = r"[A-Za-z0-9_.\-]"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
ANCHOR = re.compile(r"(?:([\w./-]+\.[A-Za-z]{1,5}))?:(\d+)(?:-(\d+))?")
NO_HITS_LEAD = "(no wiki page names a removed name in this range)"


def tree_text(repo, sha, include):
    files = [f for f in git(repo, "ls-tree", "-r", "--name-only", sha, "--", *include).splitlines() if f]
    if not files:
        return "", []
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                          input="".join("{}:{}\n".format(sha, f) for f in files).encode("utf-8"),
                          capture_output=True, check=True)
    out, data, i = [], proc.stdout, 0
    while i < len(data):
        nl = data.index(b"\n", i)
        header = data[i:nl].split()
        if len(header) < 3 or header[-1] == b"missing":
            i = nl + 1
            continue
        size = int(header[2])
        out.append(data[nl + 1:nl + 1 + size].decode("utf-8", "replace"))
        i = nl + 1 + size + 1
    return "\n".join(out), files


def identifier_shaped(t):
    if len(t) < 4:
        return False
    if "_" in t.strip("_") or any(ch.isdigit() for ch in t):
        return True
    if t.isupper():
        return True
    return any(ch.isupper() for ch in t[1:])


def name_pattern(name):
    if IDENT.fullmatch(name):
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")
    return re.compile(r"(?<!{cb})".format(cb=CB) + re.escape(name) + r"(?!{cb})".format(cb=CB))


def _diff_lines(repo, base, head, include):
    removed, added, in_hunk = [], [], False
    for line in git(repo, "diff", "-U0", "-M", base, head, "--", *include).splitlines():
        if line.startswith("diff --git"):
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("-"):
            removed.append(line[1:])
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    return removed, added


def literals(removed_lines, head_text):
    """Message-like string literals on removed lines: `removed` (gone at head) or `changed`."""
    out = {}
    for line in removed_lines:
        for m in STRLIT.finditer(line):
            for frag in TEMPLATE_HOLE.split(next(g for g in m.groups() if g)):
                frag = frag.strip(" :.,;-")
                words = re.findall(r"[A-Za-z]{2,}", frag)
                prose = sum(ch.isalnum() or ch in " .[]'-" for ch in frag) / max(1, len(frag))
                if (len(frag) >= 12 and " " in frag and len(words) >= 2 and prose >= 0.9
                        and not frag.startswith(("http", "/", "@"))):
                    out[frag] = "changed" if frag in head_text else "removed"
    return out


def literal_lines(lits, removed_lines, added_lines):
    out = {}
    for lit, st in lits.items():
        if st != "changed":
            continue
        rm = next((l.strip() for l in removed_lines if lit in l), "")
        cands = [l.strip() for l in added_lines if lit in l]
        ad = max(cands, key=lambda l: difflib.SequenceMatcher(None, rm, l).ratio()) if cands else ""
        out[lit] = [rm[:140], ad[:140]]
    return out


def derive(repo, base, head, include):
    removed_lines, added_lines = _diff_lines(repo, base, head, include)
    head_text, head_files = tree_text(repo, head, include)
    base_text, _ = tree_text(repo, base, include)
    head_counts, base_counts = Counter(IDENT.findall(head_text)), Counter(IDENT.findall(base_text))

    def tokens(lines):
        simple, comp = set(), set()
        for line in lines:
            simple.update(t for t in IDENT.findall(line) if identifier_shaped(t))
            comp.update(COMPOUND.findall(line))
        return simple, comp

    def absent(tok, counts, text):
        if IDENT.fullmatch(tok):
            return counts.get(tok, 0) == 0
        return not name_pattern(tok).search(text)

    r_simple, r_comp = tokens(removed_lines)
    a_simple, a_comp = tokens(added_lines)
    removed = {t for t in r_simple | r_comp if absent(t, head_counts, head_text)}
    added = sorted(t for t in a_simple | a_comp if absent(t, base_counts, base_text))
    head_basenames = {os.path.basename(f) for f in head_files}
    files = set()
    for line in git(repo, "diff", "--name-status", "-M", base, head, "--", *include).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and (parts[0] == "D" or parts[0].startswith("R")):
            bn = os.path.basename(parts[1])
            if bn not in head_basenames:
                files.add(bn)
    lits = literals(removed_lines, head_text)
    return {"removed": sorted(removed | files), "removed_files": sorted(files), "added": added,
            "literals": lits, "literal_lines": literal_lines(lits, removed_lines, added_lines)}


def wiki_hits(llake_root, names, pages=None):
    pats = [(n, name_pattern(n)) for n in names]
    out = {}
    for page in pages if pages is not None else wiki_pages(llake_root):
        if page_class(page) == "excluded" or not pats:
            continue
        for i, line in enumerate(read_text(os.path.join(llake_root, page)).split("\n"), 1):
            for n, p in pats:
                if p.search(line):
                    out.setdefault(page, {}).setdefault(n, []).append(i)
    return out


def hit_index_md(hits, cap=HIT_INDEX_CAP):
    if not hits:
        return "No wiki page names a removed name.\n"
    lines = ["| Page | Class | Names (hit lines) |", "|---|---|---|"]
    for page in sorted(hits, key=lambda p: (-sum(len(v) for v in hits[p].values()), p)):
        parts, shown = [], 0
        for n in sorted(hits[page], key=lambda x: (-len(hits[page][x]), x)):
            ln = hits[page][n]
            room = max(0, cap - shown)
            shown += min(room, len(ln))
            listed = ",".join(str(x) for x in ln[:room])
            more = " +{}".format(len(ln) - room) if len(ln) > room else ""
            parts.append("`{}` ×{}".format(n, len(ln)) + (" (L{}{})".format(listed, more) if listed else more))
        lines.append("| {} | {} | {} |".format(page, page_class(page), "; ".join(parts)))
    return "\n".join(lines) + "\n"


def hit_leads(hits):
    leads = []
    for page in sorted(hits):
        parts = ["`{}` L{}".format(n, ",".join(str(x) for x in lns[:HIT_INDEX_CAP]))
                 for n, lns in sorted(hits[page].items())]
        leads.append("{}: {}".format(page, "; ".join(parts)))
    return leads or [NO_HITS_LEAD]


def changed_ranges(repo, base, head, include):
    out, cur = {}, None
    for line in git(repo, "diff", "-U0", "-M", base, head, "--", *include).splitlines():
        if line.startswith("--- "):
            cur = line[6:] if line.startswith("--- a/") else None
        elif cur and line.startswith("@@"):
            m = HUNK.match(line)
            if m and int(m.group(2) or 1) > 0:
                a = int(m.group(1))
                out.setdefault(cur, []).append((a, a + int(m.group(2) or 1) - 1))
    return out


def anchor_hits(llake_root, ranges):
    by_base = {}
    for f in ranges:
        by_base.setdefault(os.path.basename(f), []).append(f)
    out = {}
    for page in wiki_pages(llake_root):
        if page_class(page) == "excluded":
            continue
        for i, line in enumerate(read_text(os.path.join(llake_root, page)).split("\n"), 1):
            last = None
            for m in ANCHOR.finditer(line):
                path = m.group(1) or last
                if not path:
                    continue
                last = path
                cands = [f for f in by_base.get(os.path.basename(path), [])
                         if f.endswith(path) or path.endswith(f) or "/" not in path]
                if len(cands) != 1:
                    continue
                a = int(m.group(2))
                b = int(m.group(3)) if m.group(3) and int(m.group(3)) >= a else a
                if any(x <= b and a <= y for x, y in ranges[cands[0]]):
                    out.setdefault(page, []).append([i, "{}:{}".format(cands[0], a) + ("-{}".format(b) if b != a else "")])
    return out


def anchor_index_md(hits, cap=4):
    if not hits:
        return "No wiki anchor points into lines the range changed.\n"
    lines = ["| Page | Class | Anchors into changed lines (page line → cited code) |", "|---|---|---|"]
    for page in sorted(hits, key=lambda p: (-len(hits[p]), p)):
        h = hits[page]
        shown = "; ".join("L{} → `{}`".format(ln, a) for ln, a in h[:cap])
        if len(h) > cap:
            shown += " +{}".format(len(h) - cap)
        lines.append("| {} | {} | {} |".format(page, page_class(page), shown))
    return "\n".join(lines) + "\n"


def literal_hits(llake_root, lits):
    out = {}
    if not lits:
        return out
    for page in wiki_pages(llake_root):
        if page_class(page) == "excluded":
            continue
        for i, line in enumerate(read_text(os.path.join(llake_root, page)).split("\n"), 1):
            for lit in lits:
                if lit in line:
                    out.setdefault(page, {}).setdefault(lit, []).append(i)
    return out


def literal_index_md(lit_hits, lits, lit_lines):
    if not lit_hits:
        return "No wiki page quotes a string literal from a removed line.\n"
    lines = ["| Page | Class | Literal (removed / changed at head) → lines |", "|---|---|---|"]
    for page in sorted(lit_hits):
        parts = ['"{}" ({}) L{}'.format(l, lits[l], ",".join(str(x) for x in ln[:5]))
                 for l, ln in sorted(lit_hits[page].items())]
        lines.append("| {} | {} | {} |".format(page, page_class(page), "; ".join(parts)))
    shown = sorted({l for h in lit_hits.values() for l in h if l in lit_lines})
    if shown:
        lines.append("\nChanged literals, first removed → added line carrying each:\n")
        for l in shown:
            rm, ad = lit_lines[l]
            lines.append('- "{}": `-{}` → `+{}`'.format(l, rm, ad) if ad else
                         '- "{}": `-{}` (no added line carries it)'.format(l, rm))
    return "\n".join(lines) + "\n"


def write_inputs(repo, base, head, include, llake_root, out_dir):
    n = derive(repo, base, head, include)
    hits = wiki_hits(llake_root, n["removed"])
    lit_hits = literal_hits(llake_root, n["literals"])
    anc = anchor_hits(llake_root, changed_ranges(repo, base, head, include))
    dump_json(os.path.join(out_dir, "names.json"), n)
    dump_json(os.path.join(out_dir, "hits.json"), hits)
    write_text(os.path.join(out_dir, "hit-index.md"), hit_index_md(hits))
    dump_json(os.path.join(out_dir, "anchor-hits.json"), anc)
    write_text(os.path.join(out_dir, "anchor-index.md"), anchor_index_md(anc))
    dump_json(os.path.join(out_dir, "literal-hits.json"), lit_hits)
    write_text(os.path.join(out_dir, "literal-index.md"), literal_index_md(lit_hits, n["literals"], n["literal_lines"]))
    return {"removed": len(n["removed"]), "added": len(n["added"]), "hit_pages": len(hits),
            "hit_lines": sum(len(v) for h in hits.values() for v in h.values()),
            "literals": len(n["literals"]), "anchor_pages": len(anc)}


def write_empty_inputs(out_dir):
    """Gap-only runs: no range, so no names and no leads."""
    dump_json(os.path.join(out_dir, "names.json"),
              {"removed": [], "removed_files": [], "added": [], "literals": {}, "literal_lines": {}})
    for name in ("hits.json", "anchor-hits.json", "literal-hits.json"):
        dump_json(os.path.join(out_dir, name), {})
    os.makedirs(os.path.join(out_dir, "patches"), exist_ok=True)
