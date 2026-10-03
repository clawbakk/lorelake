"""The one claude -p spawn helper (spec §9 "Spawn", §11, §12 "Failure classes").

Every spawn: --model/--effort/--max-budget-usd from the stage's knobs, stream-JSON, --tools and
--allowedTools (no Bash anywhere), --permission-mode dontAsk, --setting-sources "", --strict-mcp-config,
--no-session-persistence, --exclude-dynamic-system-prompt-sections; env IS_LLAKE_AGENT=true and
CLAUDE_CODE_PROMPT_CACHE_TTL; cwd the project root; the prompt on stdin.
A failure is infra when the CLI reports a rate limit, overload, auth or network error, the stream shows a
rejected rate-limit event, or the agent produced no first turn. Anything else is work.
"""
import json
import os
import re
import signal
import subprocess
import time

INFRA_RE = re.compile(
    r"rate.?limit|API Error: 5\d\d|internal server error|credit balance|usage limit|limit reached|overloaded|\b(?:429|529)\b|authenticat|unauthori[sz]ed|"
    r"invalid api key|oauth|network|ECONN|ETIMEDOUT|ENOTFOUND|socket hang up|fetch failed|"
    r"connection (?:error|refused|reset)", re.I)
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


ALLOWED_TOOLS = frozenset(("Read", "Glob", "Grep", "Edit", "Write"))
GLOB_CHARS = frozenset("*?[]{}")


def build_argv(model, effort, budget, tools, allowed, json_schema=None, system_file=None):
    bad = [t for t in str(tools).split(",") if t.strip() not in ALLOWED_TOOLS]
    if bad:
        raise ValueError("tools outside the v3 allowlist: {}".format(",".join(bad)))
    argv = ["claude", "-p", "--model", str(model), "--effort", str(effort), "--max-budget-usd", str(budget),
            "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
            "--exclude-dynamic-system-prompt-sections", "--permission-mode", "dontAsk",
            "--tools", tools, "--allowedTools", allowed, "--output-format", "stream-json", "--verbose"]
    if json_schema:
        argv += ["--json-schema", json_schema]
    if system_file:
        argv += ["--append-system-prompt-file", system_file]
    return argv


def allow_rule(path):
    p = os.path.abspath(path)
    if GLOB_CHARS & set(p) or os.path.isdir(p):
        raise ValueError("not a single-file path: {}".format(p))
    return "Edit(/" + p + ")"


def clip_timeout(stage_timeout, deadline, now=None):
    now = time.time() if now is None else now
    return max(5.0, min(float(stage_timeout), float(deadline) - now))


def _events(stream_path):
    events = []
    try:
        with open(stream_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return events


def _writes(assistants):
    out = []
    for e in assistants:
        for block in (e.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in WRITE_TOOLS:
                inp = block.get("input") or {}
                fp = inp.get("file_path") or inp.get("notebook_path")
                if fp:
                    out.append({"tool": block["name"], "file_path": str(fp)})
    return out


def summarize(stream_path, exit_code, killed=None):
    events = _events(stream_path)
    results = [e for e in events if e.get("type") == "result"]
    assistants = [e for e in events if e.get("type") == "assistant"]
    statuses = {(e.get("rate_limit_info") or {}).get("status") for e in events if e.get("type") == "rate_limit_event"}
    r = results[-1] if results else {}
    usage = r.get("usage") or {}
    first = ((assistants[0].get("message") or {}).get("usage") or {}) if assistants else {}
    peak = 0
    for e in assistants:
        u = (e.get("message") or {}).get("usage") or {}
        peak = max(peak, u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0))
    s = {"exit_code": exit_code, "killed": killed, "cost_usd": float(r.get("total_cost_usd") or 0.0),
         "turns": r.get("num_turns") or len(assistants), "subtype": r.get("subtype"),
         "is_error": bool(r.get("is_error")),
         "tokens": {"input": usage.get("input_tokens", 0), "output": usage.get("output_tokens", 0),
                    "cache_read": usage.get("cache_read_input_tokens", 0),
                    "cache_write": usage.get("cache_creation_input_tokens", 0)},
         "first_turn": {"cache_read": first.get("cache_read_input_tokens", 0),
                        "cache_write": first.get("cache_creation_input_tokens", 0),
                        "input": first.get("input_tokens", 0)},
         "peak_context": peak, "permission_denials": r.get("permission_denials") or [],
         "structured": r.get("structured_output"), "result": r.get("result"), "writes": _writes(assistants)}
    text = " ".join(str(x) for x in (r.get("result"), r.get("api_error_status"), r.get("errors")) if x)
    if killed:
        cls, reason = ("infra", killed) if killed == "infra-stop" else ("work", killed)
    elif r and not r.get("is_error") and r.get("subtype", "success") == "success":
        cls, reason = "none", "success"
    elif r and r.get("is_error") and (INFRA_RE.search(text) or "rejected" in statuses or not assistants):
        cls, reason = "infra", "{}: {}".format(r.get("subtype"), text[:200])
    elif r:
        cls, reason = "work", "{}: {}".format(r.get("subtype"), text[:200])
    elif "rejected" in statuses or not assistants:
        err = ""
        try:
            with open(stream_path + ".err", encoding="utf-8", errors="replace") as fh:
                err = fh.read()[-300:]
        except OSError:
            pass
        cls, reason = "infra", "no result, no first turn {}".format(err.strip()[:200])
    else:
        cls, reason = "work", "no result after {} turns".format(len(assistants))
    s["class"], s["reason"] = cls, reason
    return s


class Agent:
    """One claude process: start() → poll()/kill() → summary()."""

    def __init__(self, stage, argv, prompt, cwd, out_dir, timeout, cache_ttl, agent_id=""):
        self.stage, self.argv, self.prompt, self.cwd = stage, argv, prompt, cwd
        self.out_dir, self.timeout, self.cache_ttl, self.agent_id = out_dir, timeout, cache_ttl, agent_id
        self.stream = os.path.join(out_dir, stage + ".jsonl")
        self.proc, self.t0, self.killed, self._summary, self._exit = None, None, None, None, None
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, stage + ".prompt.md"), "w", encoding="utf-8") as fh:
            fh.write(prompt)

    def start(self):
        env = dict(os.environ)
        env.update({"IS_LLAKE_AGENT": "true", "CLAUDE_CODE_PROMPT_CACHE_TTL": str(self.cache_ttl),
                    "LLAKE_AGENT_STAGE": self.stage, "LLAKE_AGENT_ID": self.agent_id})
        self._out = open(self.stream, "w", encoding="utf-8")
        self._err = open(self.stream + ".err", "w", encoding="utf-8")
        self.t0 = time.time()
        try:
            self.proc = subprocess.Popen(self.argv, cwd=self.cwd, env=env, stdin=subprocess.PIPE,
                                         stdout=self._out, stderr=self._err, start_new_session=True,
                                         universal_newlines=True)
        except OSError as exc:
            self._err.write("spawn failed: {}\n".format(exc))
            self._err.flush()
            self._exit = 127
            return self
        try:
            self.proc.stdin.write(self.prompt)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        return self

    def poll(self):
        if self.proc is None:
            return True
        if self.proc.poll() is not None:
            return True
        if self.timeout and time.time() - self.t0 > self.timeout:
            self.kill("timeout")
            return True
        return False

    def kill(self, why):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.killed = why
        for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                self.proc.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue

    def first_turn_done(self):
        for e in _events(self.stream):
            if e.get("type") == "assistant":
                return True
        return False

    def wait(self, sleep=time.sleep):
        while not self.poll():
            sleep(0.25)
        return self.summary()

    def summary(self):
        if self._summary is not None:
            return self._summary
        code = self._exit
        if self.proc is not None:
            code = self.proc.wait()
        self._out.close()
        self._err.close()
        s = summarize(self.stream, code, self.killed)
        s["stage"] = self.stage
        s["wall_s"] = round(time.time() - self.t0, 1)
        with open(os.path.join(self.out_dir, self.stage + ".summary.json"), "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2)
        self._summary = s
        return s
