"""report.md: per-stage cost, token classes and peak context (spec §11), page outcomes, and the run
checks the first real-install shakedown reads (spec §15 items 1, 4, 6, 7)."""
import os

from . import gaps
from .common import git, load_json, write_text

STAGE_BUDGET = {"analysis": "analysisBudgetUsd", "recall": "recallBudgetUsd", "writer": "writerBudgetUsd",
                "fixer": "writerBudgetUsd", "verifier": "verifierBudgetUsd"}
FLAG_CAUSES = ("flagged", "unverified")


def git_status_outside_llake(project):
    out = git(project, "status", "--porcelain", "--untracked-files=all", "--", ".", ":(exclude)llake", check=False)
    return sorted({line for line in out.splitlines() if line.strip()})


def run_check_lines(state, cfg, brief, summary, kind, base, head, git_before, git_after, wall_s):
    lines = []

    def add(level, text):
        lines.append("- **{}** {}".format(level, text))

    doc = load_json(gaps.path(state.llake), None)
    errors = gaps.validate(doc, state.llake) if isinstance(doc, dict) else ["ingest-gaps.json missing or not JSON"]
    add("PASS" if not errors else "FAIL", "gap record valid, every claim's quote on its page"
        + ("" if not errors else ": " + "; ".join(errors[:3])))
    gap_causes = {}
    if isinstance(doc, dict) and not errors:
        gap_causes = {g["page"]: g["cause"] for g in doc.get("gaps", [])}
    conflicted = sorted("{} ({})".format(p, c) for p, c in gap_causes.items()
                        if (state.pages.get(p) or {}).get("outcome") in ("corrected", "no-change")
                        and c not in FLAG_CAUSES)
    add("PASS" if not conflicted else "FAIL",
        "a page that ended corrected or no-change carries only a flagged or unverified gap"
        + (": " + ", ".join(conflicted) if conflicted else ""))
    unaccounted, no_change = [], []
    for p in [e["page"] for e in brief.get("pages", []) if e["severity"] == "major"]:
        outcome = (state.pages.get(p) or {}).get("outcome")
        if p in gap_causes or outcome == "corrected":
            continue
        if outcome == "no-change":
            no_change.append(p)
        else:
            unaccounted.append("{} ({})".format(p, outcome))
    add("PASS" if not unaccounted else "FAIL", "every major brief page corrected or a gap"
        + (": " + ", ".join(unaccounted) if unaccounted else ""))
    if no_change:
        add("WARN", "major pages ended no-change (review the rejected leads): " + ", ".join(no_change))
    unrestored = state.ledger.get("unrestored") or []
    if unrestored:
        for u in unrestored:
            add("WARN", "unrestored page left as written: {} (stage {})".format(u.get("page"), u.get("stage")))
    else:
        add("PASS", "no unrestored pages")
    cap = float(cfg.get("maxRunBudgetUsd"))
    add("PASS" if state.spent <= cap else "FAIL", "spend ${:.2f} within the run cap ${:.2f}".format(state.spent, cap))
    analysis = float(cfg.get("analysisBudgetUsd"))
    if analysis > cap:
        add("WARN", "the analysis budget ${:.2f} alone exceeds the run cap ${:.2f}: the analysis spawn is not capped"
            .format(analysis, cap))
    limit = float(cfg.get("timeoutSeconds"))
    add("PASS" if wall_s <= limit else "FAIL", "wall {:.0f} s within the run deadline {:.0f} s".format(wall_s, limit))
    over = ["{} ${:.2f}".format(s["stage"], s.get("cost_usd") or 0) for s in state.ledger.get("stages", [])
            if s.get("kind") in STAGE_BUDGET and float(s.get("cost_usd") or 0) > float(cfg.get(STAGE_BUDGET[s["kind"]]))]
    add("PASS" if not over else "WARN", "every stage within its budget" + (": " + ", ".join(over) if over else ""))
    failed = ["{}: {} ({})".format(s["stage"], s.get("class"), (s.get("reason") or "")[:80])
              for s in state.ledger.get("stages", []) if s.get("class") != "none"]
    add("INFO", "failed stages, each classified infra or work: " + ("; ".join(failed) if failed else "none"))
    add("INFO", "cursor {} → {} ({} run)".format(base[:7], head[:7], kind))
    owned = {os.path.abspath(state.abs(p)) for p in state.journal.get("owned") or []}
    denied = sorted({d for s in state.ledger.get("stages", []) for d in s.get("deniedPaths") or []
                     if os.path.abspath(d) in owned})
    add("PASS" if not denied else "FAIL", "no permission denials on owned pages"
        + (": " + ", ".join(denied) if denied else ""))
    surface = state.ledger.get("surface") or []
    outside = [a["path"] for a in surface if a.get("action") == "reported-outside-llake"]
    add("PASS" if not outside else "FAIL", "no writes outside llake/" + (": " + ", ".join(outside) if outside else ""))
    # git status also moves with the user's own edits during a long run: reported, never failed.
    new_status = sorted(set(git_after) - set(git_before))
    if new_status:
        add("WARN", "changed outside llake/ during the run, not attributed to v3: " + ", ".join(new_status))
    reverted = [a["path"] for a in surface if a.get("action") == "reverted"]
    if reverted:
        add("WARN", "out-of-surface writes reverted under llake/: " + ", ".join(reverted))
    concurrent = (load_json(os.path.join(state.dir, "checks.json"), {}) or {}).get("concurrent") or []
    if concurrent:
        add("INFO", "changed during the run by another writer (not reverted): " + ", ".join(concurrent))
    return lines


def write_report(state, cfg, brief, summary, kind, base, head, git_before, git_after, wall_s):
    out = ["# Ingest v3 run `{}`".format(os.path.basename(state.dir)), "",
           "{} run, `{}..{}`, ${:.2f} list estimate, {:.0f} s.".format(kind, base[:7], head[:7], state.spent, wall_s),
           "", "## Run checks", ""]
    out += run_check_lines(state, cfg, brief, summary, kind, base, head, git_before, git_after, wall_s)
    out += ["", "## Stages", "",
            "| Stage | Kind | Class | $ | Turns | Input | Output | Cache read | Cache write | 1st-turn read | "
            "1st-turn write | Peak ctx | Wall s | Denials |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in state.ledger.get("stages", []):
        t, f = s.get("tokens") or {}, s.get("first_turn") or {}
        out.append("| {} | {} | {} | {:.3f} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            s.get("stage"), s.get("kind"), s.get("class"), float(s.get("cost_usd") or 0), s.get("turns"),
            t.get("input", 0), t.get("output", 0), t.get("cache_read", 0), t.get("cache_write", 0),
            f.get("cache_read", 0), f.get("cache_write", 0), s.get("peak_context", 0), s.get("wall_s", 0),
            s.get("denials", 0)))
    out += ["", "## Pages", "", "| Page | Severity | Bundle | Outcome | History |", "|---|---|---|---|---|"]
    for p, st in sorted(state.pages.items(), key=lambda kv: (kv[1].get("bundle") or "", kv[0])):
        out.append("| {} | {} | {} | {} | {} |".format(p, st.get("severity"), st.get("bundle"), st.get("outcome"),
                                                     "; ".join(st.get("history") or [])[:200]))
    text = "\n".join(out) + "\n"
    write_text(os.path.join(state.dir, "report.md"), text)
    return text
