#!/usr/bin/env python3
"""Test stub for `claude -p` in ingest v3 runs ($0). Acts on LLAKE_AGENT_STAGE.

  analysis    writes brief/themes.json (not when V3_STUB_NO_THEMES=1) and, from V3_STUB_BRIEF (raw JSON text),
              brief/pages/batch-1.json.
              The brief dir comes from the `Edit(//<dir>/**)` allow rule.
  recall      writes brief/pages/recall-1.json from V3_STUB_RECALL (raw text) when set;
              V3_STUB_RECALL_CLOBBER=1 truncates analysis's batch-1.json to an invalid fragment;
              V3_STUB_RECALL_EXTRA (raw text) writes brief/pages/extra.json.
  writer-* /  for each owned page (`Edit(//<abs path>)` rules) replaces every quoted brief claim
  fixer-*     (`page says "<quote>" — head`) with "the corrected statement" and reports corrected;
              creates a missing page. V3_STUB_DECLARE=1: leaves pages and declares every claim instead.
  verifier-*  returns empty findings.
Switches (comma-separated stage names): V3_STUB_FAIL (budget error: work), V3_STUB_INFRA (usage limit:
infra). V3_STUB_SLEEP=<stage>:<seconds> sleeps after editing, touching V3_STUB_MARK first;
V3_STUB_IGNORE_TERM=1 ignores SIGTERM meanwhile and exits once its parent is gone; V3_STUB_STAY=1 keeps
sleeping after its parent is gone (an orphan); V3_STUB_PIDFILE gets the sleeper's pid. V3_STUB_COST per call (0.05).
V3_STUB_OTHER_STALE: JSON list for writers' otherStale. V3_STUB_LOG: file to append each stage name to.
"""
import json
import os
import re
import signal
import sys
import time

stage = os.environ.get("LLAKE_AGENT_STAGE", "")
parent = os.getppid()
argv = sys.argv[1:]
prompt = sys.stdin.read()
allowed = argv[argv.index("--allowedTools") + 1] if "--allowedTools" in argv else ""
rules = re.findall(r"Edit\(/(/[^)]*)\)", allowed)
NEW_PAGE = ('---\ntitle: "Stub"\ndescription: "Created by the stub"\ntags: [stub]\ncreated: 2026-01-01\n'
            'updated: 2026-01-01\nstatus: current\nrelated: []\n---\n\n# Stub\n\nCreated.\n')


def listed(var):
    return stage in [s for s in os.environ.get(var, "").split(",") if s]


def emit(event):
    print(json.dumps(event), flush=True)


def finish(structured=None, writes=()):
    if os.environ.get("V3_STUB_LOG"):
        with open(os.environ["V3_STUB_LOG"], "a") as fh:
            fh.write(stage + "\n")
    emit({"type": "system", "subtype": "init"})
    emit({"type": "assistant", "message": {
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 500},
        "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": w}} for w in writes]}})
    res = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "result": "done",
           "total_cost_usd": float(os.environ.get("V3_STUB_COST", "0.05")),
           "usage": {"input_tokens": 20, "output_tokens": 100, "cache_read_input_tokens": 2000,
                     "cache_creation_input_tokens": 500}}
    if listed("V3_STUB_FAIL"):
        res.update({"subtype": "error_max_budget_usd", "is_error": True, "result": None})
    elif listed("V3_STUB_INFRA"):
        res.update({"subtype": "error_during_execution", "is_error": True, "result": "Claude AI usage limit reached"})
    elif structured is not None:
        res["structured_output"] = structured
    emit(res)
    sys.exit(1 if res["is_error"] else 0)


def maybe_sleep():
    spec = os.environ.get("V3_STUB_SLEEP", "")
    if ":" not in spec:
        return
    name, secs = spec.rsplit(":", 1)
    if name != stage:
        return
    if os.environ.get("V3_STUB_PIDFILE"):
        with open(os.environ["V3_STUB_PIDFILE"], "w") as fh:
            fh.write(str(os.getpid()))
    if os.environ.get("V3_STUB_MARK"):
        with open(os.environ["V3_STUB_MARK"], "w") as fh:
            fh.write(stage)
    if os.environ.get("V3_STUB_IGNORE_TERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    end = time.time() + float(secs)
    while time.time() < end:
        if os.getppid() != parent and not os.environ.get("V3_STUB_STAY"):
            sys.exit(1)
        time.sleep(0.2)


if stage == "analysis":
    brief = [r for r in rules if r.endswith("/**")][0][:-3]
    os.makedirs(os.path.join(brief, "pages"), exist_ok=True)
    if os.environ.get("V3_STUB_NO_THEMES") != "1":
        with open(os.path.join(brief, "themes.json"), "w") as fh:
            json.dump([{"id": "T1", "title": "stub theme", "summary": "Stub summary."}], fh)
    if os.environ.get("V3_STUB_BRIEF"):
        with open(os.path.join(brief, "pages", "batch-1.json"), "w") as fh:
            fh.write(os.environ["V3_STUB_BRIEF"])
    for name in ("considered", "files", "notes"):
        with open(os.path.join(brief, name + ".json"), "w") as fh:
            json.dump([], fh)
    maybe_sleep()
    finish()
elif stage == "recall":
    pages_dir = [r for r in rules if r.endswith("/**")][0][:-3]
    if os.environ.get("V3_STUB_RECALL"):
        with open(os.path.join(pages_dir, "recall-1.json"), "w") as fh:
            fh.write(os.environ["V3_STUB_RECALL"])
    if os.environ.get("V3_STUB_RECALL_CLOBBER") == "1":
        with open(os.path.join(pages_dir, "batch-1.json"), "w") as fh:
            fh.write('[{"path": "llake/wiki/arch/client.md", ')
    if os.environ.get("V3_STUB_RECALL_EXTRA"):
        with open(os.path.join(pages_dir, "extra.json"), "w") as fh:
            fh.write(os.environ["V3_STUB_RECALL_EXTRA"])
    finish()
elif stage.startswith(("writer", "fixer")):
    owned = [r for r in rules if not r.endswith("/**")]
    quotes = re.findall(r'page says "(.+?)" — head', prompt)
    out, writes = [], []
    for path in owned:
        page = "llake/" + path.split("/llake/", 1)[1]
        if os.environ.get("V3_STUB_DECLARE") == "1" and os.path.exists(path):
            text = open(path).read()
            left = [{"quote": q, "head": "stub", "severity": "minor"} for q in quotes if q in text]
            out.append({"page": page, "status": "declared-gap", "claimsLeft": left, "rejected": [], "note": "stub"})
            continue
        if os.path.exists(path):
            text = open(path).read()
            for q in quotes:
                text = text.replace(q, "the corrected statement")
        else:
            text = NEW_PAGE
        with open(path, "w") as fh:
            fh.write(text)
        writes.append(path)
        out.append({"page": page, "status": "corrected", "claimsLeft": [], "rejected": [], "note": "stub"})
    maybe_sleep()
    other = json.loads(os.environ.get("V3_STUB_OTHER_STALE", "[]")) if stage.startswith("writer") else []
    finish({"pages": out, "otherStale": other}, writes)
elif stage.startswith("verifier"):
    pages = re.findall(r"^### `(llake/wiki/[^`]+\.md)`", prompt, re.M)
    finish({"pages": [{"page": p, "accuracy": [], "residual": []} for p in pages]})
else:
    finish()
