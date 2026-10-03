"""Deterministic bundling and dispatch order (spec §7).

Bundles form within severity tiers (major bundles first, so the run cap drops minor work first),
greedily by shared themes (x2) and evidence files. bundleMaxPages and bundleMaxWeight always hold;
a page heavier than the weight cap goes alone. A record page that links (at base) to a page already in
the bundle gets the highest affinity and may join from the minor tier, so one writer marks the record
and corrects the page it links. Within a tier: carried before fresh, oldest `since` first, new pages last.
"""
import os

from .common import read_text
from .wiki import page_class, page_links, slug_map

RECORD_LINK_AFFINITY = 10
NEVER = 10 ** 9


def weight(p):
    return float(p.get("weight_kb", 0) or 0) + len(p.get("claims") or [])


def _record_links(llake_root, pages):
    slugs = slug_map(llake_root)
    out = {}
    for p in pages:
        if page_class(p["page"]) == "record" and not p.get("create"):
            text = read_text(os.path.join(llake_root, p["page"]))
            out[p["page"]] = {slugs[s] for s in page_links(text) if s in slugs}
    return out


def make_bundles(pages, llake_root, max_pages, max_weight, since_rank=None):
    rank = since_rank or {}
    max_pages = max(1, int(max_pages))
    links = _record_links(llake_root, pages)

    def key(p):
        carried = bool(p.get("carried"))
        return (0 if p["severity"] == "major" else 1, 0 if carried else 1,
                rank.get(p.get("since"), NEVER) if carried else 0, 1 if p.get("create") else 0,
                -len(p.get("claims") or []), p["page"])

    def affinity(a, b):
        score = (len(set(a.get("themes") or []) & set(b.get("themes") or [])) * 2
                 + len(set(a.get("evidence") or []) & set(b.get("evidence") or [])))
        if b["page"] in links.get(a["page"], ()) or a["page"] in links.get(b["page"], ()):
            score += RECORD_LINK_AFFINITY
        return score

    remaining = sorted(pages, key=key)
    bundles = []
    for tier in ("major", "minor"):
        while True:
            pool = [p for p in remaining if p["severity"] == tier]
            if not pool:
                break
            seed = pool[0]
            remaining.remove(seed)
            group, w = [seed], weight(seed)
            if w < max_weight:
                while len(group) < max_pages:
                    def best(p):
                        return max(affinity(p, g) for g in group)
                    cands = [p for p in remaining if p["severity"] == tier
                             or (page_class(p["page"]) == "record" and best(p) >= RECORD_LINK_AFFINITY)]
                    if not cands:
                        break
                    scored = sorted(cands, key=lambda p: (-best(p), key(p)))
                    affine = [p for p in scored if best(p) > 0]
                    same_tier = [p for p in scored if p["severity"] == tier]
                    nxt = next((p for p in (affine or same_tier) if w + weight(p) <= max_weight), None)
                    if nxt is None:
                        break
                    remaining.remove(nxt)
                    group.append(nxt)
                    w += weight(nxt)
            bundles.append({"severity": "major" if any(p["severity"] == "major" for p in group) else "minor",
                            "pages": [p["page"] for p in group], "weight": round(w, 1)})
    for i, b in enumerate(bundles, 1):
        b["id"] = "b{:02d}".format(i)
    return bundles
