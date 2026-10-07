"""Brief assembly and validation (spec §5 "Validation by code", §4 "Sweep accounting", §7 "Assembly")."""
import glob
import os

from . import gaps
from .common import dump_json, load_json, norm_ws, read_text
from .schema import load_schema, validate
from .wiki import has_category, norm_page, page_class

ROOT_NAMES = ("index.md", "log.md")


def _is_root_file(raw):
    """True for the project's root index/log in any spelling (absolute, ./, llake/, wiki/)."""
    p = raw.strip().strip("`").strip().replace("\\", "/")
    if "/llake/" in p:
        p = p[p.rindex("/llake/") + len("/llake/"):]
    for prefix in ("./", "llake/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    parts = p.split("/")
    return parts[-1] in ROOT_NAMES and len(parts) <= 2


MISSING_THEMES = "themes.json: missing or not valid JSON"  # the analysis wrote no usable themes.json: work


class InvalidBrief(Exception):
    def __init__(self, errors):
        super(InvalidBrief, self).__init__("; ".join(errors[:5]))
        self.errors = errors


def fill_defaults(d):
    """A new page's description already says why it is needed; analysis often omits `reason` there."""
    if isinstance(d, dict) and d.get("kind") == "new" and "reason" not in d and d.get("description"):
        d["reason"] = d["description"]
    return d


def _entries(brief_dir, errors, warnings):
    schema = load_schema("brief-page")
    out = []
    for path in sorted(glob.glob(os.path.join(brief_dir, "pages", "*.json"))):
        name = os.path.basename(path)
        # A recall failure never fails the run (spec section 6): its problems are warnings.
        problems = warnings if name.startswith("recall-") else errors
        doc = load_json(path, None)
        if doc is None:
            problems.append("{}: not valid JSON".format(name))
            continue
        items = doc if isinstance(doc, list) else [doc]
        for i, d in enumerate(items):
            where = "{}[{}]".format(name, i) if isinstance(doc, list) else name
            errs = validate(fill_defaults(d), schema)
            if errs:
                problems.extend("{}: {}".format(where, e) for e in errs)
                continue
            raw = d["path"].strip().strip("`")
            if _is_root_file(raw):
                warnings.append("{}: dropped {} (out of scope)".format(where, raw))
                continue
            page = norm_page(raw)
            if not page:
                problems.append("{}: bad path {!r}".format(where, raw))
                continue
            if d.get("kind") == "new" and not (d.get("title") and d.get("description")):
                problems.append("{}: a new page needs title and description".format(where))
                continue
            claims = [{"quote": str(c["quote"]).strip(), "head": str(c["head"]).strip(), "severity": c["severity"]}
                      for c in d["stale"]]
            severity = "major" if d["severity"] == "major" or any(c["severity"] == "major" for c in claims) else "minor"
            out.append({"page": page, "kind": d.get("kind", "thematic"), "severity": severity,
                        "themes": list(d.get("themes") or []), "reason": d["reason"], "claims": claims,
                        "newFacts": [str(x) for x in d.get("newFacts") or []],
                        "evidence": [str(x) for x in d.get("evidence") or []],
                        "title": d.get("title"), "description": d.get("description")})
    return out


def _merge(by_page, entry):
    cur = by_page.get(entry["page"])
    if cur is None:
        by_page[entry["page"]] = entry
        return
    seen = {norm_ws(c["quote"]) for c in cur["claims"] if c["quote"]}
    for c in entry["claims"]:
        if not c["quote"] or norm_ws(c["quote"]) not in seen:
            cur["claims"].append(c)
    if entry["severity"] == "major":
        cur["severity"] = "major"
    for k in ("themes", "evidence", "newFacts"):
        cur[k] = list(cur.get(k) or []) + [x for x in entry.get(k) or [] if x not in (cur.get(k) or [])]
    for k in ("carried", "since", "attempts", "hits", "autoAdded"):
        if k in entry and k not in cur:
            cur[k] = entry[k]


def _hit_claims(llake_root, page, names):
    lines = read_text(os.path.join(llake_root, page)).split("\n")
    out = []
    for n, lns in sorted(names.items()):
        for ln in lns[:5]:
            frag = lines[ln - 1].strip().lstrip("-*#> ").strip() if 0 < ln <= len(lines) else n
            out.append({"quote": frag[:200], "head": "`{}` no longer exists in the watched source at head "
                        "(removed-name sweep)".format(n), "severity": "minor", "quoteFound": True,
                        "source": "check:removed-name"})
    return out


def assemble(agent_dir, llake_root, head, gap_only=False):
    bdir = os.path.join(agent_dir, "brief")
    errors, warnings = [], []
    report = {"errors": errors, "warnings": warnings}
    if gap_only:
        themes, entries, considered, files, notes = [], [], [], [], []
    else:
        themes = load_json(os.path.join(bdir, "themes.json"), None)
        if themes is None:
            errors.append(MISSING_THEMES)
        else:
            errors.extend("themes.json: " + e for e in validate(themes, load_schema("brief-themes")))
        entries = _entries(bdir, errors, warnings)
        considered = [c for c in load_json(os.path.join(bdir, "considered.json"), []) or [] if isinstance(c, dict)]
        files = load_json(os.path.join(bdir, "files.json"), []) or []
        notes = load_json(os.path.join(bdir, "notes.json"), []) or []
    if errors:
        dump_json(os.path.join(agent_dir, "brief-report.json"), report)
        raise InvalidBrief(errors)

    by_page, dropped_new = {}, []
    for e in entries:
        if page_class(e["page"]) == "excluded":
            warnings.append("dropped excluded page {}".format(e["page"]))
            continue
        exists = os.path.exists(os.path.join(llake_root, e["page"]))
        if not exists and e["kind"] != "new":
            warnings.append("dropped {}: the page does not exist and is not marked new".format(e["page"]))
            continue
        if not exists and not has_category(llake_root, e["page"]):
            warnings.append("dropped new page {}: its category does not exist".format(e["page"]))
            dropped_new.append(e["page"])
            continue
        _merge(by_page, e)

    missing = 0
    for p in by_page.values():
        text = norm_ws(read_text(os.path.join(llake_root, p["page"])))
        for c in p["claims"]:
            c["quoteFound"] = bool(c["quote"]) and norm_ws(c["quote"]) in text
            missing += not c["quoteFound"]

    hits = load_json(os.path.join(agent_dir, "inputs", "hits.json"), {}) or {}
    considered_pages = {norm_page(str(c.get("path", ""))) for c in considered}
    auto, unaccounted = [], []
    for page, names in sorted(hits.items()):
        cls = page_class(page)
        if page in by_page:
            by_page[page]["hits"] = names
            continue
        if cls == "state":
            reason = "names a removed name (auto-added by the removed-name sweep)"
            auto.append(page)
        elif cls in ("record", "gotcha") and page not in considered_pages:
            reason = "names a removed name; analysis did not account for it"
            unaccounted.append(page)
        else:
            continue
        by_page[page] = {"page": page, "kind": "sweep", "severity": "minor", "themes": [], "newFacts": [],
                         "reason": reason, "evidence": sorted(names), "claims": _hit_claims(llake_root, page, names),
                         "hits": names, "autoAdded": True, "title": None, "description": None}

    carried, dropped, stuck = gaps.carried(gaps.load(llake_root), llake_root, "major" if gap_only else "all")
    for c in carried:
        c["claims"] = [dict(x, quoteFound=norm_ws(x["quote"]) in norm_ws(read_text(os.path.join(llake_root, c["page"]))))
                       for x in c["claims"] if x["quote"]]
        c.update({"title": None, "description": None})
        _merge(by_page, c)
        by_page[c["page"]]["carried"] = True

    blurbs = [p for p in by_page.values() if page_class(p["page"]) == "index"]
    for p in blurbs:
        del by_page[p["page"]]

    for p in by_page.values():
        file = os.path.join(llake_root, p["page"])
        p["create"] = not os.path.exists(file)
        p["weight_kb"] = round(len(read_text(file).encode("utf-8")) / 1024.0, 1)

    out = {"head": head, "gapOnly": bool(gap_only), "themes": themes,
           "pages": sorted(by_page.values(), key=lambda x: x["page"]), "blurbs": blurbs,
           "considered": considered, "files": files, "notes": notes}
    report.update({"claims": sum(len(p["claims"]) for p in out["pages"]), "claims_quote_not_found": missing,
                   "auto_added_state_pages": auto, "unaccounted_record_or_gotcha_pages": unaccounted,
                   "carried": [c["page"] for c in carried], "carried_dropped_missing": dropped,
                   "stuck_skipped": stuck, "dropped_new_pages": dropped_new,
                   "blurbs": [b["page"] for b in blurbs], "pages": len(out["pages"]),
                   "major_pages": sum(1 for p in out["pages"] if p["severity"] == "major")})
    dump_json(os.path.join(agent_dir, "brief.json"), out)
    dump_json(os.path.join(agent_dir, "brief-report.json"), report)
    return out
