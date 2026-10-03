"""Finalize (spec §2 write order, §9 "Who owns what", §10 indexes, §12 gap record, log entry, cursor).

Write order: pages (`updated:`) → indexes → gap record → log entry → cursor and clock. Each file is added
to the journal's finalizeWrites (the log entry text to logEntry) and saved before it is written, so a kill
can undo it; `finalized` is set only after the cursor write, so a crash leaves the cursor on the old base
and the next run redoes the range. Finalize touches only this run's own pages, the indexes of their
categories, the gap record, log.md and the cursor/clock, all under llake/; it never edits the root index.md.
"""
import os
import re
import time

from . import gaps, plan, snapshots
from .common import dump_json, load_json, norm_ws, read_text, write_text
from .wiki import category_dir, frontmatter_scalars, norm_page, slug, wiki_pages

ROW_RE = re.compile(r"^\|\s*\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]\s*\|\s*(.*?)\s*\|(.*)$")
COUNT_RE = re.compile(r"^(\s*)(\d+)(\s+pages?\b)", re.M)
NO_LEADS = "(no wiki page names a removed name in this range)"
SKIP_LOG_LEADS = 20


def set_updated(text, today):
    """Set the frontmatter's `updated:` (insert it after `created:` when absent). A page without a terminated
    frontmatter block is returned unchanged."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    for i in range(1, end):
        if lines[i].startswith("updated:"):
            lines[i] = "updated: " + today + ("\r" if lines[i].endswith("\r") else "")
            return "\n".join(lines)
    anchor = next((j for j in range(1, end) if lines[j].startswith("created:")), end - 1)
    eol = "\r" if lines[anchor].endswith("\r") else ""
    lines.insert(anchor + 1, "updated: " + today + eol)
    return "\n".join(lines)


def write_cursor(llake_root, sha, now=None):
    """The cursor and its clock together, as advance_ingest_cursor (hooks/lib/ingest-cursor.sh) writes them."""
    write_text(os.path.join(llake_root, "last-ingest-sha"), sha + "\n")
    write_text(os.path.join(llake_root, ".state", "last-ingest-at"),
               "{}\n".format(int(time.time() if now is None else now)))


def _desc(root, page):
    return frontmatter_scalars(read_text(os.path.join(root, page))).get("description", "").strip()


def rebuild_indexes(state, changed, created, today, will_write):
    pre = os.path.join(state.dir, "pre")
    all_pages = wiki_pages(state.llake)
    touched = []
    for cat in sorted({category_dir(p) for p in list(changed) + list(created)}):
        idx = cat + "/" + os.path.basename(cat) + ".md"
        path = os.path.join(state.llake, idx)
        if not os.path.exists(path):
            continue
        text = read_text(path)
        members = [p for p in all_pages if category_dir(p) == cat and p != idx]
        out, last_row, seen = [], -1, set()
        for line in text.split("\n"):
            m = ROW_RE.match(line)
            if m:
                s = m.group(1).strip()
                page = cat + "/" + s + ".md"
                if page in changed:
                    new_desc = _desc(state.llake, page)
                    if new_desc and norm_ws(new_desc) != norm_ws(_desc(pre, page)):
                        line = "| [[{}]] | {} |{}".format(s, new_desc.replace("|", "\\|"), m.group(3))
                seen.add(s)
                out.append(line)
                last_row = len(out) - 1
            else:
                out.append(line)
        new_rows = ["| [[{}]] | {} |".format(slug(p), _desc(state.llake, p).replace("|", "\\|"))
                    for p in sorted(created) if category_dir(p) == cat and slug(p) not in seen]
        if new_rows:
            if last_row >= 0:
                out[last_row + 1:last_row + 1] = new_rows
            else:
                out += new_rows
        new = "\n".join(out)
        if any(category_dir(p) == cat for p in created):
            new = COUNT_RE.sub(lambda mm: "{}{}{}".format(mm.group(1), len(members), mm.group(3)), new, count=1)
        if new != text:
            will_write(idx)
            write_text(path, set_updated(new, today))
            touched.append(idx)
    return touched


def _verifier_findings(st):
    rejected = [norm_ws(r) for r in st.get("rejected") or [] if r]
    out = []
    for kind in ("accuracy", "residual"):
        for f in (st.get("verifier") or {}).get(kind, []):
            q = norm_ws(f.get("quote", ""))
            if q and not any(q in r or r in q for r in rejected):
                out.append(dict(f, source="verifier:" + kind))
    return out


def outcomes(state, brief, checks):
    """One outcome per run page (the gaps.next_record shape). Two run-level facts override the page outcome:
    a page left unrestored after a failed write is owed as `reverted`; a page whose fixer failed is owed as
    `flagged` with the findings it was given (first checks pass and verifier) plus whatever is flagged now."""
    by_page = {p["page"]: p for p in brief.get("pages", [])}
    flags = (checks or {}).get("flags") or {}
    write_flags = (load_json(os.path.join(state.dir, "checks.write.json"), {}) or {}).get("flags") or {}
    out = []
    for p, st in sorted(state.pages.items()):
        outcome = st.get("outcome") or "timeout"
        found = list(flags.get(p, [])) + _verifier_findings(st)
        if st.get("unrestored") and outcome not in gaps.CAUSES:
            outcome = "reverted"
        elif st.get("fixSnap") and not st.get("fixed"):
            left = (st.get("claimsLeft") or []) if outcome == "declared" else []
            found = list(write_flags.get(p, [])) + found + list(left)
            outcome = "flagged"
        out.append({"page": p, "severity": st["severity"], "carried": bool(st.get("carried")),
                    "create": bool(st.get("create")), "outcome": outcome, "flags": found,
                    "claimsLeft": st.get("claimsLeft") or [], "briefClaims": (by_page.get(p) or {}).get("claims") or [],
                    "unverified": bool(st.get("unverified")), "attempts": st.get("attempts", 0),
                    "since": st.get("since")})
    return out


def log_entry(kind, base, head, today, agent_id, spent, themes, changed, created, indexes, doc, resolved, notes):
    rng = "gap-only at {}".format(head[:7]) if kind == "gap-only" else "{}..{}".format(base[:7], head[:7])
    n_major = sum(1 for g in doc["gaps"] if g["severity"] == "major")
    lines = ["", "## [{}] ingest | {}: v3 — {} updated, {} created, {} gaps ({} major)".format(
        today, rng, len(changed), len(created), len(doc["gaps"]), n_major), ""]
    if kind == "gap-only":
        tail = "No new range: carried gaps only."
    else:
        tail = "Themes: {}.".format("; ".join("{} {}".format(t.get("id"), t.get("title")) for t in themes or []))
    lines += ["Agent `{}`, ${:.2f} list estimate. {}".format(agent_id, spent, tail), ""]
    affected = (["[[{}]] (updated)".format(slug(p)) for p in changed]
                + ["[[{}]] (created)".format(slug(p)) for p in created]
                + ["[[{}]]".format(slug(i)) for i in indexes])
    lines.append("Pages affected: " + (", ".join(affected) or "none"))
    if doc["gaps"]:
        lines += ["", "Gaps (every claim is in `llake/ingest-gaps.json`):"]
        for g in doc["gaps"]:
            c = g["claims"][0]
            more = " (+{} more)".format(len(g["claims"]) - 1) if len(g["claims"]) > 1 else ""
            lines.append('- [[{}]] — {}, cause: {}: "{}" → {}{}{}'.format(
                slug(g["page"]), g["severity"], g["cause"], c["quote"][:120], c["head"][:160], more,
                " — needs a human" if g["stuck"] else ""))
    if resolved:
        lines += ["", "Carried gaps resolved: " + ", ".join("[[{}]]".format(slug(p)) for p in resolved)]
    for n in notes:
        lines += ["", "Note: " + n]
    return "\n".join(lines) + "\n"


def _will_write(state):
    def will_write(rel):
        if rel not in state.journal["finalizeWrites"]:
            snapshots.snapshot_finalize_write(state, rel)
            state.journal["finalizeWrites"].append(rel)
            state.save()
    return will_write


def _append_log(state, entry):
    state.journal["logEntry"] = entry
    state.save()
    path = os.path.join(state.llake, "log.md")
    text = read_text(path)
    write_text(path, (text.rstrip("\n") + "\n" if text else "") + entry)


def _advance(state, will_write, head, now):
    """Cursor and clock, then `finalized` (only after the cursor landed), then the failure counter is cleared
    (spec §12 rows 3, 4, 7). Clearing after `finalized` means a kill can never lose the counter of a run the
    kill revert undoes."""
    will_write("last-ingest-sha")
    write_cursor(state.llake, head, now)
    state.journal["finalized"] = True
    state.journal["inFlight"] = {}
    state.save()
    plan.clear_failures(state.llake)


def _notes(state):
    report = load_json(os.path.join(state.dir, "brief-report.json"), {}) or {}
    notes = ["new page `{}` dropped: its category does not exist".format(p)
             for p in report.get("dropped_new_pages") or []]
    notes += ["out-of-surface write to `{}` by {}: {}".format(a.get("path"), a.get("stage"), a.get("action"))
              for a in state.ledger.get("surface") or [] if isinstance(a, dict)]
    return notes


def finalize(state, cfg, brief, agent_id, base, head, kind, today=None, now=None):
    today = today or time.strftime("%Y-%m-%d")
    pre = os.path.join(state.dir, "pre")
    before = load_json(os.path.join(state.dir, "pre-manifest.json"), {}) or {}
    will_write = _will_write(state)

    changed, created = [], []
    for p in sorted(set(state.journal.get("owned") or [])):
        path = state.abs(p)
        if norm_page(p) != p or not os.path.exists(path):
            continue
        text = read_text(path)
        if p not in before:
            created.append(p)
        elif text != read_text(os.path.join(pre, p)):
            changed.append(p)
        else:
            continue
        new = set_updated(text, today)
        if new != text:
            will_write(p)
            write_text(path, new)

    indexes = rebuild_indexes(state, changed, created, today, will_write)

    checks = load_json(os.path.join(state.dir, "checks.json"), {}) or {}
    doc, resolved, notes = gaps.next_record(
        gaps.load(state.llake), outcomes(state, brief, checks), state.ledger.get("otherStale") or [],
        brief.get("blurbs") or [], head, agent_id, today, state.llake, cfg.get("verifierMode") != "off")
    notes = _notes(state) + notes
    will_write(gaps.GAPS_FILE)
    gaps.save(state.llake, doc)

    _append_log(state, log_entry(kind, base, head, today, agent_id, state.spent, brief.get("themes"), changed,
                                 created, indexes, doc, resolved, notes))
    _advance(state, will_write, head, now)
    summary = {"kind": kind, "head": head, "updated": changed, "created": created, "indexes": indexes,
               "gaps": len(doc["gaps"]), "major_gaps": sum(1 for g in doc["gaps"] if g["severity"] == "major"),
               "resolved": resolved, "notes": notes, "spent": state.spent}
    dump_json(os.path.join(state.dir, "finalize.json"), summary)
    return summary


def record_skip(state, agent_id, base, head, leads, today=None, now=None):
    """Cursor-table row 7: a single-commit range failed analysis twice; advance past it, recorded."""
    today = today or time.strftime("%Y-%m-%d")
    leads = [str(l) for l in leads or [] if str(l).strip()] or [NO_LEADS]
    will_write = _will_write(state)
    prev = gaps.load(state.llake)
    doc = {"version": 1, "asOf": head, "agent": agent_id, "date": today, "gaps": prev.get("gaps", []),
           "ranges": list(prev.get("ranges", [])) + [{"base": base, "head": head, "cause": "analysis-failed",
                                                       "leads": leads}]}
    will_write(gaps.GAPS_FILE)
    gaps.save(state.llake, doc)
    entry = ("\n## [{d}] ingest | {b}..{h}: v3 — skipped: analysis failed twice\n\nAgent `{a}`. This "
             "single-commit range was not ingested. It is recorded under `ranges` in `llake/ingest-gaps.json` "
             "with its leads:\n\n").format(d=today, b=base[:7], h=head[:7], a=agent_id)
    entry += "\n".join("- " + l for l in leads[:SKIP_LOG_LEADS]) + "\n"
    if len(leads) > SKIP_LOG_LEADS:
        entry += "- … and {} more (all in the gap record)\n".format(len(leads) - SKIP_LOG_LEADS)
    _append_log(state, entry)
    _advance(state, will_write, head, now)
