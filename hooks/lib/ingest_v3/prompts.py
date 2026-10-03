"""Writer and verifier prompts (spec §8 "Context", §10 "Verifier"). Shared prefix first, bundle part last."""
import os

from .anchors import broken_anchors
from .common import read_text
from .render import render
from .stage import fmt_names
from .wiki import catalog_md, page_class

MODE_RULES = {
    "edit": "Prefer targeted `Edit` calls on the passages that change; use `Write` only for a new page.",
    "write": "Rewrite each page you change with one whole-page `Write` (same frontmatter and structure, "
             "corrected content); do not use `Edit`.",
}
ACCURACY_CHECK = ("1. **accuracy**: every statement the writer **added** (`+` lines) must be true at head. A false "
                  "one is a finding: `quote` the added text, `head` the truth.")
RESIDUAL_CHECK = ("2. **residual**: each brief claim must no longer be present **in substance** on the page, "
                  "reworded or not (a record page under `decisions/` is exempt: it keeps its text and gets a "
                  "status note). A claim still present is a finding: `quote` the page text that still says it.")


def shared_prefix(llake_root, brief, names, write_mode, today, rel_inputs=""):
    if write_mode not in MODE_RULES:
        raise ValueError("ingest.v3.writeMode must be 'edit' or 'write', got {!r}".format(write_mode))
    themes = "\n".join("- **{} {}**: {}".format(t.get("id"), t.get("title"), t.get("summary", ""))
                       for t in brief.get("themes") or []) or \
        "(gap-only run: no new range; see each page's carried claims)"
    planned = [{"page": p["page"], "description": p.get("description") or ""}
               for p in brief.get("pages", []) if p.get("create")]
    return render("ingest.v3.writer-shared.md.tmpl", {
        "TODAY": today, "MODE_RULE": MODE_RULES[write_mode], "THEMES": themes,
        "REMOVED_NAMES": fmt_names(names.get("removed", []), rel_inputs), "ADDED_NAMES": fmt_names(names.get("added", []), rel_inputs),
        "CATALOG": catalog_md(llake_root, planned)})


def page_block(entry, llake_root, src, anchor_hits, literal_hits, findings=None):
    p = entry["page"]
    lines = ["### `llake/{}`".format(p), "",
             "- Class: {}; severity: **{}**; kind: {}; themes: {}".format(
                 page_class(p), entry["severity"], entry.get("kind"), ", ".join(entry.get("themes") or []) or "—"),
             "- Why: {}".format(entry.get("reason") or "—")]
    if entry.get("carried"):
        lines.append("- Carried from an earlier run (since `{}`, {} failed attempts): its claims were recorded "
                     "then; re-verify them at head.".format((entry.get("since") or "")[:7], entry.get("attempts", 0)))
    if entry.get("create"):
        lines.append("- **Create this page.** Title: {}; description: {}".format(
            entry.get("title") or "—", entry.get("description") or "—"))
    if entry.get("claims"):
        lines += ["", "Stale claims (leads to verify):"]
        for i, c in enumerate(entry["claims"], 1):
            q = '"{}"'.format(c["quote"]) if c.get("quote") else "(no quote)"
            lines.append("{}. [{}] page says {} — head: {}".format(i, c["severity"], q, c["head"]))
    if entry.get("newFacts"):
        lines += ["", "New facts whose home is this page:"] + ["- " + f for f in entry["newFacts"]]
    if entry.get("hits"):
        lines += ["", "Removed names on this page (line numbers at base): " + "; ".join(
            "`{}` L{}".format(n, ",".join(str(x) for x in v[:20])) for n, v in sorted(entry["hits"].items()))]
    anchor_leads = (anchor_hits or {}).get(p) or []
    if anchor_leads:
        lines += ["", "Anchors on this page into lines the range changed (page line → cited code): " + "; ".join(
            "L{} → `{}`".format(ln, a) for ln, a in anchor_leads[:10])]
    lits = (literal_hits or {}).get(p) or {}
    if lits:
        lines += ["", "Log or error strings on this page that the range removed or changed: " + "; ".join(
            '"{}" L{}'.format(l, ",".join(str(x) for x in ln[:5])) for l, ln in sorted(lits.items()))]
    if entry.get("evidence"):
        lines += ["", "Evidence: " + ", ".join("`{}`".format(e) for e in entry["evidence"][:12])]
    path = os.path.join(llake_root, p)
    if src is not None and os.path.exists(path):
        bad = broken_anchors(src, read_text(path), [c.get("quote") for c in entry.get("claims") or []])
        if bad:
            lines += ["", "Anchors on this page already broken at head (code-checked; fix or remove each one on "
                          "a line you add or change):"] + ["- " + b for b in bad]
    if findings:
        lines += ["", "**Flagged after writing (fix these; each is a lead to verify at head):**"]
        for f in findings:
            q = '"{}" '.format(f["quote"]) if f.get("quote") else ""
            lines.append("- [{}, {}] {}— {}".format(f.get("severity", "minor"), f.get("source", "check"), q,
                                                    f.get("head", "")))
    return "\n".join(lines)


def bundle_prompt(job, brief, llake_root, src, anchor_hits, literal_hits, patches_rel, fixer=False):
    by_page = {p["page"]: p for p in brief["pages"]}
    findings = job.get("findings") or {}
    blocks = [page_block(by_page[p], llake_root, src, anchor_hits, literal_hits, findings.get(p))
              for p in job["pages"]]
    task = ("fix what was flagged on your pages after writing" if fixer
            else "bring your pages up to date with the code at head")
    return render("ingest.v3.writer-bundle.md.tmpl", {
        "TASK": task, "BASE": job["base"][:7], "HEAD": job["head"][:7], "PATCHES": patches_rel,
        "PAGE_LIST": "\n".join("- `llake/{}`".format(p) for p in job["pages"]), "PAGES": "\n\n".join(blocks)})


def verifier_prompt(job, brief, diffs, patches_rel, mode):
    if mode == "accuracy":
        checks = ACCURACY_CHECK + "\nReturn `residual` as an empty list."
    elif mode == "accuracy+residual":
        checks = ACCURACY_CHECK + "\n" + RESIDUAL_CHECK
    else:
        raise ValueError("verifier mode must be accuracy or accuracy+residual, got {!r}".format(mode))
    by_page = {p["page"]: p for p in brief["pages"]}
    blocks = []
    for p in job["pages"]:
        claims = "\n".join('- [{}] "{}" — head: {}'.format(c["severity"], c.get("quote", ""), c["head"])
                           for c in by_page[p].get("claims", [])) or "- (none listed)"
        blocks.append("### `llake/{}`\n\n```diff\n{}\n```\n\nBrief claims:\n{}".format(p, diffs.get(p, ""), claims))
    return render("ingest.v3.verifier.md.tmpl", {
        "BASE": job["base"][:7], "HEAD": job["head"][:7], "CHECKS": checks, "PATCHES": patches_rel,
        "PAGES": "\n\n".join(blocks)})
