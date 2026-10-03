"""Command line for ingest v3, entered through hooks/lib/ingest-v3.py."""
import argparse
import json
import os

from . import gaps
from .common import load_json


def cmd_owed_major(a):
    return 0 if gaps.owed_major(gaps.load(a.llake_root)) else 1


def cmd_validate_gaps(a):
    p = gaps.path(a.llake_root)
    if not os.path.exists(p):
        print("ABSENT")
        return 0
    doc = load_json(p)
    if doc is None:
        print("INVALID: not valid JSON")
        return 1
    errors = gaps.validate(doc, a.llake_root)
    if errors:
        print("INVALID: " + errors[0])
        for e in errors[1:]:
            print("  " + e)
        return 1
    stuck = [g for g in doc["gaps"] if g["stuck"]]
    print("OK: {} gaps ({} major, {} stuck), {} skipped ranges".format(
        len(doc["gaps"]), sum(1 for g in doc["gaps"] if g["severity"] == "major"), len(stuck),
        len(doc["ranges"])))
    for g in stuck:
        print("STUCK: {} ({}, cause {}, {} attempts) — needs a human".format(
            g["page"], g["severity"], g["cause"], g["attempts"]))
    for r in doc["ranges"]:
        print("RANGE: {}..{} ({}) — not ingested; leads: {}".format(
            r["base"][:7], r["head"][:7], r["cause"], "; ".join(r["leads"][:3])))
    return 0


def cmd_revert_run(a):
    from .snapshots import revert_run
    print(json.dumps(revert_run(a.project_root, a.agent_dir)))
    return 0


def cmd_run(a):
    from .run import run
    return run(a.project_root, a.agent_id, a.agent_dir, float(a.deadline))


def build_parser():
    ap = argparse.ArgumentParser(prog="ingest-v3.py")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("owed-major", help="exit 0 when an open, non-stuck major gap is owed")
    p.add_argument("--llake-root", required=True)
    p.set_defaults(func=cmd_owed_major)
    p = sub.add_parser("validate-gaps", help="validate llake/ingest-gaps.json (doctor)")
    p.add_argument("--llake-root", required=True)
    p.set_defaults(func=cmd_validate_gaps)
    p = sub.add_parser("revert-run", help="undo a killed run's writes under llake/ (kill trap)")
    p.add_argument("--project-root", required=True)
    p.add_argument("--agent-dir", required=True)
    p.set_defaults(func=cmd_revert_run)
    p = sub.add_parser("run", help="run ingest v3 once (called by hooks/lib/ingest-v3.sh)")
    p.add_argument("--project-root", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--agent-dir", required=True)
    p.add_argument("--deadline", required=True, help="run deadline, epoch seconds")
    p.set_defaults(func=cmd_run)
    return ap, sub


def main(argv=None):
    ap, _sub = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)
