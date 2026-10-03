"""Per-run state kept in the agent dir: run.json (journal), ledger.json (costs), pages.json (outcomes)."""
import os

from .common import dump_json, load_json


def new_journal():
    return {"kind": None, "base": None, "head": None, "inFlight": {}, "owned": [], "finalizeWrites": [],
            "logEntry": "", "finalized": False, "aborted": False}


class RunState:
    def __init__(self, project_root, agent_dir):
        self.project = os.path.abspath(str(project_root))
        self.dir = os.path.abspath(str(agent_dir))
        self.llake = os.path.join(self.project, "llake")
        self.ledger = load_json(self._p("ledger.json"), None) or {
            "stages": [], "spent": 0.0, "otherStale": [], "surface": []}
        self.pages = load_json(self._p("pages.json"), None) or {}
        journal = new_journal()
        journal.update(load_json(self._p("run.json"), None) or {})
        self.journal = journal

    def _p(self, name):
        return os.path.join(self.dir, name)

    def save(self):
        dump_json(self._p("ledger.json"), self.ledger)
        dump_json(self._p("pages.json"), self.pages)
        dump_json(self._p("run.json"), self.journal)

    def abs(self, page):
        return os.path.join(self.llake, page)

    @property
    def spent(self):
        return float(self.ledger.get("spent", 0.0) or 0.0)

    def record_stage(self, summary, kind):
        entry = {k: summary.get(k) for k in ("stage", "class", "reason", "cost_usd", "turns", "tokens",
                                             "first_turn", "peak_context", "wall_s", "killed")}
        denials = summary.get("permission_denials") or []
        entry["kind"] = kind
        entry["denials"] = len(denials)
        entry["deniedPaths"] = [str((d.get("tool_input") or {}).get("file_path"))
                                for d in denials if isinstance(d, dict) and (d.get("tool_input") or {}).get("file_path")]
        self.ledger["stages"].append(entry)
        self.ledger["spent"] = round(self.spent + float(summary.get("cost_usd") or 0.0), 4)
