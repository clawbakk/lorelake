"""The $0 post-write checks (spec §9 "Failure and repair", §10 "$0 checks").

On every page a writer changed: frontmatter (revert to the snapshot when it no longer parses, unless the
page never parsed), dangling [[links]] on added lines (unlinked), then flags: removed-name grep (state
pages), quote residue (corrected non-record pages, rejected claims exempt), anchors on added lines, and
status against diff (reported corrected but unchanged). Files changed with no v3 write are reported.
"""
import difflib
import os
import re
import sys

from . import snapshots
from .anchors import Source, check_anchors
from .common import dump_json, load_json, norm_ws, read_text, write_text
from .names import wiki_hits
from .wiki import page_class, slug, wiki_pages

_LIB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import frontmatter as fmlib  # noqa: E402  (hooks/lib/frontmatter.py)

LINK_RE = re.compile(r"\[\[([^\]|#]+)([|#][^\]]*)?\]\]")
CLAIM_OUTCOMES = ("corrected", "declared", "no-change")


def added_lines(before, after):
    a, b = before.split("\n"), after.split("\n")
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(b[j1:j2])
    return out


def frag(line):
    return line.strip().lstrip("-*#>| ").strip()[:200]


def fm_ok(text):
    fm_text, _body = fmlib.split(text)
    if not fm_text:
        return False
    try:
        fm = fmlib.parse(fm_text)
    except fmlib.FrontmatterParseError:
        return False
    return bool(fm.get("title")) and bool(fm.get("description"))


def unlink(text, before, valid):
    old = set(before.split("\n"))
    out, removed = [], []
    for line in text.split("\n"):
        if line not in old and not line.lstrip().startswith(("- \"[[", "- [[")):
            def sub(m):
                if m.group(1).strip() in valid:
                    return m.group(0)
                removed.append(m.group(1).strip())
                alias = m.group(2)
                return (alias[1:] if alias and alias.startswith("|") else m.group(1)).strip()
            line = LINK_RE.sub(sub, line)
        out.append(line)
    return "\n".join(out), removed


def concurrent_changes(state):
    manifest_path = os.path.join(state.dir, "pre-manifest.json")
    if not os.path.exists(manifest_path):
        return []
    before = load_json(manifest_path, {}) or {}
    now = snapshots.current_manifest(state)
    owned = set(state.journal.get("owned") or [])
    surfaced = {a["path"][len("llake/"):] for a in state.ledger.get("surface", []) if a["path"].startswith("llake/")}
    return sorted(rel for rel in set(now) | set(before)
                  if now.get(rel) != before.get(rel) and rel not in owned and rel not in surfaced)


def run_checks(state, brief, pass_name, src=None):
    by_page = {p["page"]: p for p in brief["pages"]}
    names = (load_json(os.path.join(state.dir, "inputs", "names.json"), {}) or {}).get("removed", [])
    src = src or Source(state.project)
    res = {"flags": {}, "unlinked": {}, "reverted": [], "unrestored": [], "status_mismatch": [], "concurrent": concurrent_changes(state)}
    valid = {slug(p) for p in wiki_pages(state.llake)} | {slug(p["page"]) for p in brief["pages"] if p.get("create")}
    for p, st in sorted(state.pages.items()):
        if st.get("outcome") not in CLAIM_OUTCOMES:
            continue
        entry = by_page.get(p, {})
        snap = st.get("snap")
        before = read_text(os.path.join(snap, p)) if snap else ""
        text = read_text(state.abs(p))
        if st.get("status") == "no-change" and st.get("changed"):
            res["status_mismatch"].append("{}: reported no-change but changed".format(p))
        if not st.get("changed"):
            if st.get("status") == "corrected":
                res["status_mismatch"].append("{}: reported corrected but unchanged".format(p))
                res["flags"][p] = [dict(c, source=c.get("source") or "brief")
                                   for c in entry.get("claims", []) if c.get("quoteFound")]
            continue
        if not fm_ok(text) and (not before or fm_ok(before)):
            fix = bool(st.get("fixSnap"))
            outcome = snapshots.restore_page(state, p, st["fixSnap"] if fix else snap)
            if outcome == "unrestored":
                st["outcome"], st["unrestored"] = "reverted", True
                st["history"].append("frontmatter invalid after {}: could not restore".format("fix" if fix else "write"))
                state.ledger.setdefault("unrestored", []).append({"stage": "checks-" + pass_name, "page": p})
                res["unrestored"].append(p)
                continue
            if fix:
                st["history"].append("fixer broke the frontmatter: reverted to the post-write state")
                text = read_text(state.abs(p))
            else:
                st["outcome"], st["changed"] = "reverted", False
                st["history"].append("frontmatter invalid after write: reverted")
                res["reverted"].append(p)
                continue
        fixed, removed = unlink(text, before, valid)
        if removed:
            write_text(state.abs(p), fixed)
            text = fixed
            res["unlinked"][p] = removed
        flags = []
        cls = page_class(p)
        if cls == "state" and names:
            lines = text.split("\n")
            for n, lns in sorted((wiki_hits(state.llake, names, [p]).get(p) or {}).items()):
                for ln in lns:
                    flags.append({"quote": frag(lines[ln - 1]), "head": "`{}` no longer exists at head".format(n),
                                  "severity": "minor", "source": "check:removed-name"})
        if cls != "record" and st.get("status") == "corrected":
            rejected = [norm_ws(r) for r in st.get("rejected") or [] if r]
            ntext = norm_ws(text)
            for c in entry.get("claims", []):
                q = norm_ws(c.get("quote", ""))
                if not q or not c.get("quoteFound") or c.get("source") == "check:removed-name":
                    continue
                if any(q in r or r in q for r in rejected):
                    continue
                if q in ntext:
                    flags.append({"quote": c["quote"], "head": c["head"], "severity": c["severity"],
                                  "source": "check:quote-residue"})
        for line, why in check_anchors(src, added_lines(before, text)):
            flags.append({"quote": frag(line), "head": why, "severity": "minor", "source": "check:anchor"})
        if flags:
            res["flags"][p] = flags
    state.save()
    dump_json(os.path.join(state.dir, "checks.json"), res)
    dump_json(os.path.join(state.dir, "checks.{}.json".format(pass_name)), res)
    return res
