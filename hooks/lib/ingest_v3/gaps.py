"""The gap record, llake/ingest-gaps.json (spec §12): load, validate, carry, and compute the next one."""
import os

from .common import STUCK_ATTEMPTS, dump_json, load_json, norm_ws, read_text
from .schema import load_schema, validate as schema_validate
from .wiki import frontmatter_scalars, norm_page, page_class

GAPS_FILE = "ingest-gaps.json"
CAUSES = ("declared", "flagged", "writer-failed", "reverted", "run-cap", "timeout", "infra", "unverified", "blurb")
ATTEMPT_CAUSES = frozenset(["declared", "flagged", "writer-failed", "reverted"])
SOURCES = ("brief", "verifier:residual", "verifier:accuracy", "check:removed-name", "check:quote-residue",
           "check:anchor", "writer:other")
FAILURE_OUTCOMES = ("writer-failed", "reverted", "run-cap", "timeout", "infra")


def empty_record(head, agent, date):
    return {"version": 1, "asOf": head, "agent": agent, "date": date, "gaps": [], "ranges": []}


def path(llake_root):
    return os.path.join(llake_root, GAPS_FILE)


def load(llake_root):
    doc = load_json(path(llake_root))
    if not isinstance(doc, dict):
        return empty_record("", "", "")
    if not isinstance(doc.get("gaps"), list):
        doc["gaps"] = []
    if not isinstance(doc.get("ranges"), list):
        doc["ranges"] = []
    return doc


def save(llake_root, doc):
    dump_json(path(llake_root), doc)


def validate(doc, llake_root=None):
    errors = schema_validate(doc, load_schema("gap-record"))
    if errors:
        return errors
    seen = set()
    for i, g in enumerate(doc["gaps"]):
        where = "gaps[{}]".format(i)
        if norm_page(g["page"]) != g["page"]:
            errors.append("{}: page {!r} is not a wiki page path".format(where, g["page"]))
        if g["page"] in seen:
            errors.append("{}: duplicate page {}".format(where, g["page"]))
        seen.add(g["page"])
        if g["stuck"] != (g["attempts"] >= STUCK_ATTEMPTS):
            errors.append("{}: stuck={} disagrees with attempts={}".format(where, g["stuck"], g["attempts"]))
        if llake_root is not None:
            file = os.path.join(llake_root, g["page"])
            if not os.path.exists(file):
                errors.append("{}: page {} does not exist".format(where, g["page"]))
                continue
            text = norm_ws(read_text(file))
            for j, c in enumerate(g["claims"]):
                if norm_ws(c["quote"]) not in text:
                    errors.append("{}.claims[{}]: quote not on the page".format(where, j))
    return errors


def open_gaps(doc):
    return [g for g in doc.get("gaps", []) if isinstance(g, dict) and not g.get("stuck")]


def owed_major(doc):
    return any(g.get("severity") == "major" for g in open_gaps(doc))


def _sev(value):
    return value if value in ("major", "minor") else "minor"


def carried(doc, llake_root, scope):
    entries, dropped, stuck = [], [], []
    for g in doc.get("gaps", []):
        if not isinstance(g, dict):
            continue
        page = norm_page(str(g.get("page", "")))
        if not page:
            continue
        if g.get("stuck"):
            stuck.append(page)
            continue
        if not os.path.exists(os.path.join(llake_root, page)):
            dropped.append(page)
            continue
        if scope == "major" and g.get("severity") != "major":
            continue
        claims = [{"quote": str(c.get("quote", "")), "head": str(c.get("head", "")), "severity": _sev(c.get("severity")),
                   "source": c.get("source") if c.get("source") in SOURCES else "brief"}
                  for c in g.get("claims", []) if isinstance(c, dict)]
        entries.append({"page": page, "kind": "carried", "severity": _sev(g.get("severity")), "themes": [],
                        "reason": "carried gap ({})".format(g.get("cause")), "newFacts": [], "evidence": [],
                        "claims": claims, "carried": True, "since": g.get("since"),
                        "attempts": int(g.get("attempts", 0) or 0), "prevCause": g.get("cause")})
    return entries, dropped, stuck


def claims_on_page(claims, text, default_source="brief"):
    t = norm_ws(text)
    out = []
    for c in claims:
        q = str(c.get("quote", "")).strip()
        if not q or norm_ws(q) not in t:
            continue
        head = str(c.get("head", "")).strip() or "(no detail recorded)"
        if any(norm_ws(q) == norm_ws(o["quote"]) and head == o["head"] for o in out):
            continue
        src = c.get("source", default_source)
        out.append({"quote": q, "head": head, "severity": _sev(c.get("severity")),
                    "source": src if src in SOURCES else default_source})
    return out


def placeholder_claim(text, why):
    desc = frontmatter_scalars(text).get("description", "").strip()
    if desc and norm_ws(desc) in norm_ws(text):
        quote = desc
    else:
        quote = next((l.strip() for l in text.split("\n") if l.strip() and l.strip() != "---"), "")
    return [{"quote": quote, "head": why, "severity": "minor", "source": "brief"}] if quote else []


def _gap(page, severity, cause, since, attempts, claims):
    return {"page": page, "severity": severity, "cause": cause, "since": since, "attempts": attempts,
            "stuck": attempts >= STUCK_ATTEMPTS, "claims": claims}


def next_record(prev, outcomes, other_stale, blurbs, head, agent, date, llake_root, verifier_on, new_ranges=()):
    prev_by = {g.get("page"): g for g in prev.get("gaps", []) if isinstance(g, dict)}
    gaps, resolved, notes = {}, [], []
    run_pages = {o["page"] for o in outcomes}
    for o in outcomes:
        p = o["page"]
        file = os.path.join(llake_root, p)
        if not os.path.exists(file):
            if o.get("create"):
                notes.append("planned new page `{}` was not created ({})".format(p, o["outcome"]))
            else:
                notes.append("page `{}` no longer exists; not recorded".format(p))
            continue
        text = read_text(file)
        prev_g = prev_by.get(p, {})
        attempts = int(prev_g.get("attempts", o.get("attempts", 0)) or 0)
        since = prev_g.get("since") or o.get("since") or head
        sev, outcome, cause, claims = o["severity"], o["outcome"], None, []
        if outcome in ("corrected", "no-change"):
            claims = claims_on_page(o.get("flags", []), text)
            if claims:
                cause = "flagged"
                sev = "major" if any(c["severity"] == "major" for c in claims) else "minor"
            elif o.get("unverified") and verifier_on:
                cause, sev = "unverified", "minor"
                claims = placeholder_claim(text, "the verifier did not check this page's rewrite this run")
        elif outcome == "flagged":  # a failed fixer: owed even when its findings left the page (placeholder)
            cause = "flagged"
            claims = claims_on_page(o.get("flags", []), text)
            if claims:
                sev = "major" if any(c["severity"] == "major" for c in claims) else "minor"
        elif outcome == "declared":
            cause = "declared"
            claims = claims_on_page(o.get("claimsLeft", []), text) or claims_on_page(o.get("briefClaims", []), text)
        elif outcome in FAILURE_OUTCOMES:
            cause = outcome
            claims = claims_on_page(o.get("briefClaims", []), text)
        else:
            raise ValueError("unknown page outcome {!r} for {}".format(outcome, p))
        if cause is None:
            if o.get("carried") or p in prev_by:
                resolved.append(p)
            continue
        if cause in ATTEMPT_CAUSES:
            attempts += 1
        claims = claims or placeholder_claim(text, "owed ({}); no recorded quote is on the page".format(cause))
        if not claims:
            notes.append("page `{}` is empty; its {} gap is not recorded".format(p, cause))
            continue
        gaps[p] = _gap(p, sev, cause, since, attempts, claims)

    other = {}
    for s in other_stale:
        p = norm_page(str(s.get("page", "")))
        if not p or p in run_pages or page_class(p) in ("excluded", "index"):
            continue
        other.setdefault(p, []).append(dict(s, source="writer:other"))
    for p, found in sorted(other.items()):
        file = os.path.join(llake_root, p)
        if not os.path.exists(file):
            notes.append("a writer reported `{}` stale, but the page does not exist; not recorded".format(p))
            continue
        text = read_text(file)
        claims = claims_on_page(found, text, "writer:other")
        for s in found:
            q = str(s.get("quote", "")).strip()
            if claims and (not q or norm_ws(q) not in norm_ws(text)):
                notes.append("a writer reported `{}` stale with a quote that is not on the page ({!r}); that "
                             "quote is not recorded".format(p, q[:60]))
        if not claims:
            notes.append("a writer reported `{}` stale, but its quote is not on the page; not recorded".format(p))
            continue
        prev_g = prev_by.get(p) or {}
        if prev_g:
            claims = claims_on_page(list(prev_g.get("claims", [])) + claims, text)
        attempts = int(prev_g.get("attempts", 0) or 0)
        sev = "major" if any(c["severity"] == "major" for c in claims) else "minor"
        gaps[p] = _gap(p, sev, "flagged", prev_g.get("since") or head, attempts, claims)

    for b in blurbs:
        p = b["page"]
        file = os.path.join(llake_root, p)
        if not os.path.exists(file):
            continue
        text = read_text(file)
        claims = claims_on_page(b.get("claims", []), text) or \
            placeholder_claim(text, b.get("reason") or "the category blurb is false at head")
        if claims:
            gaps[p] = _gap(p, "minor", "blurb", prev_by.get(p, {}).get("since") or head, 0, claims)

    for p, g in prev_by.items():
        if p in gaps or p in run_pages or p in resolved:
            continue
        file = os.path.join(llake_root, p) if p else ""
        if not p or not os.path.exists(file):
            notes.append("carried gap `{}` dropped: the page no longer exists".format(p))
            continue
        text = read_text(file)
        kept = dict(g)
        kept["claims"] = claims_on_page(g.get("claims", []), text) or \
            placeholder_claim(text, "owed ({}); no recorded quote is on the page".format(g.get("cause")))
        if kept["claims"]:
            gaps[p] = kept

    out = {"version": 1, "asOf": head, "agent": agent, "date": date,
           "gaps": sorted(gaps.values(), key=lambda g: (g["severity"] != "major", g["page"])),
           "ranges": list(prev.get("ranges", [])) + list(new_ranges)}
    return out, sorted(resolved), notes
