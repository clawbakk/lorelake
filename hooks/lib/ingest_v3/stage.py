"""Analysis inputs ($0) and the analysis and recall prompts (spec §4 "Patches", §5, §6)."""
import glob
import os

from . import gaps
from .common import HIT_INDEX_CAP, PATCH_MAX_BYTES, git, load_json, read_text, write_text
from .render import render
from .wiki import catalog_md, norm_page, page_class


def patch_stem(path):
    return path.replace("/", "__")


def _changed_files(repo, base, head, include):
    raw = git(repo, "-c", "core.quotePath=false", "diff", "--name-only", "-z", "-M", base, head, "--", *include)
    return [f for f in raw.split("\0") if f]


def stage_patches(repo, base, head, include, dest, max_bytes=PATCH_MAX_BYTES):
    os.makedirs(dest, exist_ok=True)
    out = []
    for f in _changed_files(repo, base, head, include):
        patch = git(repo, "-c", "core.quotePath=false", "diff", "-M", base, head, "--", f)
        stem = patch_stem(f)
        if len(patch.encode("utf-8")) <= max_bytes:
            write_text(os.path.join(dest, stem + ".patch"), patch)
            out.append(stem + ".patch")
            continue
        part, buf, n = 1, [], 0
        for line in patch.splitlines(True):
            size = len(line.encode("utf-8"))
            if n + size > max_bytes and buf and line.startswith("@@"):
                name = "{}.patch.part{}".format(stem, part)
                write_text(os.path.join(dest, name), "".join(buf))
                out.append(name)
                part, buf, n = part + 1, [], 0
            buf.append(line)
            n += size
        name = "{}.patch.part{}".format(stem, part)
        write_text(os.path.join(dest, name), "".join(buf))
        out.append(name)
    return out


def stage_inputs(repo, base, head, include, llake_root, agent_dir):
    inputs = os.path.join(agent_dir, "inputs")
    os.makedirs(os.path.join(agent_dir, "brief", "pages"), exist_ok=True)
    rng = "{}..{}".format(base, head)
    write_text(os.path.join(inputs, "commits.md"),
               git(repo, "log", "--reverse", "--format=## %h %s%n%n%b", rng, "--", *include))
    commits = git(repo, "-c", "core.quotePath=false", "log", "--reverse", "--format=%h %s", rng, "--", *include).strip()
    numstat = git(repo, "-c", "core.quotePath=false", "diff", "--numstat", "-M", base, head, "--", *include).strip()
    write_text(os.path.join(inputs, "numstat.txt"), numstat + "\n")
    patches = stage_patches(repo, base, head, include, os.path.join(inputs, "patches"))
    write_text(os.path.join(inputs, "catalog.md"), catalog_md(llake_root))
    return {"commits": commits, "numstat": numstat, "patches": patches}


def fmt_names(xs, rel_inputs, cap=150):
    if not xs:
        return "none"
    shown = ", ".join("`{}`".format(x) for x in xs[:cap])
    if len(xs) > cap:
        shown += " … (+{}, full list in `{}/names.json`)".format(len(xs) - cap, rel_inputs)
    return shown


def owed_md(doc):
    rows = ["- `llake/{}` ({}, {}, {} recorded claims)".format(g.get("page"), g.get("severity"), g.get("cause"),
                                                              len(g.get("claims") or []))
            for g in gaps.open_gaps(doc)]
    if not rows:
        return "None."
    return ("These pages are owed from earlier runs. Code adds them to the brief with their recorded claims, so "
            "do not re-derive them; write an entry for one only if this range makes more of it stale.\n\n"
            + "\n".join(rows))


def analysis_prompt(project_root, llake_root, agent_dir, base, head, include, staged):
    inputs = os.path.join(agent_dir, "inputs")
    rel_inputs = os.path.relpath(inputs, project_root)
    names = load_json(os.path.join(inputs, "names.json"), {}) or {}
    prompt = render("ingest.v3.analysis.md.tmpl", {
        "BRIEF_DIR": os.path.join(agent_dir, "brief"), "INPUTS": rel_inputs, "BASE": base, "HEAD": head,
        "INCLUDE": " ".join("`{}`".format(p) for p in include),
        "COMMITS": staged.get("commits") or "(none)", "NUMSTAT": staged.get("numstat") or "(none)",
        "REMOVED_NAMES": fmt_names(names.get("removed", []), rel_inputs),
        "ADDED_NAMES": fmt_names(names.get("added", []), rel_inputs),
        "HIT_CAP": HIT_INDEX_CAP, "HIT_INDEX": read_text(os.path.join(inputs, "hit-index.md")),
        "ANCHOR_INDEX": read_text(os.path.join(inputs, "anchor-index.md")),
        "LITERAL_INDEX": read_text(os.path.join(inputs, "literal-index.md")),
        "OWED": owed_md(gaps.load(llake_root)),
    })
    write_text(os.path.join(agent_dir, "analysis.prompt.md"), prompt)
    return prompt


def recall_prompt(project_root, llake_root, agent_dir):
    bdir = os.path.join(agent_dir, "brief")
    inputs = os.path.join(agent_dir, "inputs")
    themes = load_json(os.path.join(bdir, "themes.json"), []) or []
    listed = {}
    for path in sorted(glob.glob(os.path.join(bdir, "pages", "*.json"))):
        doc = load_json(path, None)
        for d in doc if isinstance(doc, list) else [doc]:
            if isinstance(d, dict) and norm_page(str(d.get("path", ""))):
                listed[norm_page(d["path"])] = d.get("reason", "")
    for page in sorted(load_json(os.path.join(inputs, "hits.json"), {}) or {}):
        if page not in listed and page_class(page) == "state":
            listed[page] = "names a removed name (added automatically)"
    considered = [c for c in load_json(os.path.join(bdir, "considered.json"), []) or [] if isinstance(c, dict)]
    themes_md = "\n".join("- **{}: {}**. {}".format(t.get("id"), t.get("title"), t.get("summary", ""))
                          for t in themes if isinstance(t, dict)) or "(none)"
    listed_md = "\n".join("- `llake/{}`: {}".format(p, r) for p, r in sorted(listed.items())) or "(none)"
    considered_md = "\n".join("- `llake/{}`: {}".format(norm_page(str(c.get("path", ""))) or c.get("path"),
                                                        c.get("reason", "")) for c in considered) or "(none)"
    prompt = render("ingest.v3.recall.md.tmpl", {
        "BRIEF_DIR": bdir, "INPUTS": os.path.relpath(inputs, project_root), "THEMES": themes_md,
        "LISTED": listed_md, "CONSIDERED": considered_md})
    write_text(os.path.join(agent_dir, "recall.prompt.md"), prompt)
    return prompt
