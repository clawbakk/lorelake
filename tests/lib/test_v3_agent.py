"""Spawn flags, infra/work classification, stream summary, timeouts."""
import json
import os
import sys
import textwrap
import time

import pytest

import v3_helpers  # noqa: F401
from ingest_v3 import agent

INIT = {"type": "system", "subtype": "init"}


def assistant(usage=None, content=None):
    return {"type": "assistant", "message": {"usage": usage or {"input_tokens": 5, "cache_read_input_tokens": 100,
                                                                 "cache_creation_input_tokens": 50},
                                             "content": content or []}}


def result(**kw):
    r = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.25, "num_turns": 2,
         "result": "done", "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30,
                                     "cache_creation_input_tokens": 40}}
    r.update(kw)
    return r


def stream(tmp_path, *events):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in events) + "not json\n")
    return str(p)


@pytest.mark.parametrize("events,killed,cls", [
    ([INIT, assistant(), result()], None, "none"),
    ([INIT, assistant(), result(subtype="error_max_budget_usd", is_error=True, result=None)], None, "work"),
    ([INIT, assistant(), result(subtype="error_max_turns", is_error=True, result=None)], None, "work"),
    ([INIT, assistant(), result(subtype="error_during_execution", is_error=True,
                                result="Claude AI usage limit reached")], None, "infra"),
    ([INIT, result(subtype="error_during_execution", is_error=True, result="x", api_error_status=429)], None, "infra"),
    ([INIT, result(subtype="error_during_execution", is_error=True, result="Overloaded")], None, "infra"),
    ([INIT, {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
      result(subtype="error_during_execution", is_error=True, result="x")], None, "infra"),
    ([], None, "infra"),
    ([INIT], None, "infra"),
    ([INIT, assistant()], None, "work"),
    ([INIT, assistant()], "timeout", "work"),
    ([INIT, assistant()], "infra-stop", "infra"),
    ([INIT, assistant(), result(subtype="success", is_error=False)], "timeout", "work"),
    ([INIT, result(subtype="error_during_execution", is_error=True, result="x")], None, "infra"),
    ([INIT, result(subtype="success", is_error=True, result="API Error: 500 Internal server error")], None, "infra"),
    ([INIT, result(subtype="success", is_error=True, result="Credit balance is too low")], None, "infra"),
    ([INIT, assistant(), result(subtype="success", is_error=True, result="API Error: 500 Internal server error")],
     None, "infra"),
    ([INIT, assistant(), result(subtype="error_during_execution", is_error=True, result="bad structured output")],
     None, "work"),
])
def test_classification(tmp_path, events, killed, cls):
    assert agent.summarize(stream(tmp_path, *events), 1, killed)["class"] == cls


def test_summary_fields(tmp_path):
    edit = {"type": "tool_use", "name": "Edit", "input": {"file_path": "/p/llake/wiki/a/x.md"}}
    read = {"type": "tool_use", "name": "Read", "input": {"file_path": "/p/src/a.py"}}
    s = agent.summarize(stream(tmp_path, INIT,
                               assistant(usage={"input_tokens": 1, "cache_read_input_tokens": 7,
                                                "cache_creation_input_tokens": 3}),
                               assistant(usage={"input_tokens": 2, "cache_read_input_tokens": 900,
                                                "cache_creation_input_tokens": 100}, content=[edit, read]),
                               result(structured_output={"pages": []},
                                      permission_denials=[{"tool_name": "Edit", "tool_input": {"file_path": "/x"}}])),
                        0)
    assert s["cost_usd"] == 0.25 and s["turns"] == 2
    assert s["tokens"] == {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}
    assert s["first_turn"] == {"cache_read": 7, "cache_write": 3, "input": 1}
    assert s["peak_context"] == 1002
    assert s["structured"] == {"pages": []}
    assert s["writes"] == [{"tool": "Edit", "file_path": "/p/llake/wiki/a/x.md"}]
    assert len(s["permission_denials"]) == 1


def test_build_argv_isolation_flags():
    argv = agent.build_argv("sonnet", "medium", 3.0, "Read,Glob,Grep,Edit,Write", "Read,Edit(//p/x.md)",
                            json_schema='{"type":"object"}', system_file="/tmp/prefix.md")
    assert argv[0] == "claude" and "-p" in argv
    pairs = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)}
    assert pairs["--model"] == "sonnet" and pairs["--effort"] == "medium"
    assert pairs["--max-budget-usd"] == "3.0"
    assert pairs["--setting-sources"] == ""
    assert pairs["--permission-mode"] == "dontAsk"
    assert pairs["--tools"] == "Read,Glob,Grep,Edit,Write"
    assert pairs["--allowedTools"] == "Read,Edit(//p/x.md)"
    assert pairs["--output-format"] == "stream-json"
    assert pairs["--json-schema"] == '{"type":"object"}'
    assert pairs["--append-system-prompt-file"] == "/tmp/prefix.md"
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--exclude-dynamic-system-prompt-sections",
                 "--verbose"):
        assert flag in argv
    assert "Bash" not in ",".join(argv)


def test_allow_rule_is_double_slash_absolute(tmp_path):
    assert agent.allow_rule(str(tmp_path / "x.md")) == "Edit(/" + str(tmp_path / "x.md") + ")"
    assert agent.allow_rule(str(tmp_path / "x.md")).startswith("Edit(//")


def test_clip_timeout():
    assert agent.clip_timeout(900, deadline=1000.0, now=500.0) == 500.0
    assert agent.clip_timeout(900, deadline=10000.0, now=500.0) == 900.0
    assert agent.clip_timeout(900, deadline=100.0, now=500.0) == 5.0


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "claude"
    script.write_text(textwrap.dedent("""\
        #!{py}
        import json, os, sys, time
        prompt = sys.stdin.read()
        with open(os.environ["FAKE_OUT"], "w") as fh:
            json.dump({{"prompt": prompt, "argv": sys.argv[1:],
                       "env": {{k: os.environ.get(k) for k in ("IS_LLAKE_AGENT", "CLAUDE_CODE_PROMPT_CACHE_TTL",
                                                             "LLAKE_AGENT_STAGE", "LLAKE_AGENT_ID")}},
                       "cwd": os.getcwd()}}, fh)
        time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
        print(json.dumps({{"type": "assistant", "message": {{"usage": {{}}}}}}))
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.1}}))
        """.format(py=sys.executable)))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_OUT", str(tmp_path / "seen.json"))
    return tmp_path


def test_agent_runs_with_env_stdin_and_cwd(fake_claude):
    out = fake_claude / "stages"
    a = agent.Agent("writer-b01", agent.build_argv("sonnet", "medium", 1, "Read", "Read"), "THE PROMPT",
                    str(fake_claude), str(out), 60, "5m", agent_id="run-1_writer-b01").start()
    s = a.wait(sleep=lambda _: time.sleep(0.05))
    seen = json.loads((fake_claude / "seen.json").read_text())
    assert seen["prompt"] == "THE PROMPT"
    assert seen["env"] == {"IS_LLAKE_AGENT": "true", "CLAUDE_CODE_PROMPT_CACHE_TTL": "5m",
                           "LLAKE_AGENT_STAGE": "writer-b01", "LLAKE_AGENT_ID": "run-1_writer-b01"}
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(str(fake_claude))
    assert s["class"] == "none" and s["stage"] == "writer-b01"
    assert (out / "writer-b01.prompt.md").read_text() == "THE PROMPT"
    assert json.loads((out / "writer-b01.summary.json").read_text())["class"] == "none"
    assert a.first_turn_done()


def test_agent_timeout_kills_and_is_work(fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    t0 = time.time()
    a = agent.Agent("writer-b02", agent.build_argv("sonnet", "medium", 1, "Read", "Read"), "p",
                    str(fake_claude), str(fake_claude / "stages"), 1, "5m").start()
    s = a.wait(sleep=lambda _: time.sleep(0.1))
    assert s["killed"] == "timeout" and s["class"] == "work"
    assert time.time() - t0 < 20


def test_missing_binary_is_infra(tmp_path):
    argv = agent.build_argv("sonnet", "medium", 1, "Read", "Read")
    argv[0] = str(tmp_path / "no-such-claude")
    s = agent.Agent("analysis", argv, "p", str(tmp_path), str(tmp_path / "stages"), 5, "5m").start().wait(
        sleep=lambda _: None)
    assert s["class"] == "infra"


def test_allow_rule_rejects_globs_and_directories(tmp_path):
    for bad in ("*.md", "a?.md", "a[1].md", "a{b,c}.md"):
        with pytest.raises(ValueError):
            agent.allow_rule(str(tmp_path / "wiki" / bad))
    with pytest.raises(ValueError):
        agent.allow_rule(str(tmp_path))


def test_build_argv_rejects_non_allowlisted_tools():
    with pytest.raises(ValueError):
        agent.build_argv("sonnet", "medium", 1, "Read,Bash", "Read")
    agent.build_argv("sonnet", "medium", 1, "Read,Glob,Grep,Edit,Write", "Read")
