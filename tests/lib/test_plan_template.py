"""Tests for templates/plan.md.tmpl invariants after the native-plugin-hooks migration."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_TMPL = REPO_ROOT / "templates" / "plan.md.tmpl"


def test_plan_template_exists():
    assert PLAN_TMPL.is_file(), f"templates/plan.md.tmpl must exist at {PLAN_TMPL}"


def test_no_settings_json_references():
    """Phase 4 was deleted; CC hook registration is handled by the plugin manifest."""
    content = PLAN_TMPL.read_text()
    assert "settings.json" not in content, (
        "templates/plan.md.tmpl should not reference settings.json — "
        "hook registration is handled by hooks/hooks.json"
    )


def test_phase_numbering_consecutive():
    """After Phase 4 deletion, phase headings should be consecutive starting at 1."""
    content = PLAN_TMPL.read_text()
    phase_numbers = []
    for line in content.splitlines():
        if line.startswith("## Phase "):
            parts = line.split()
            phase_numbers.append(int(parts[2]))
    assert phase_numbers, "no Phase headings found in plan.md.tmpl"
    assert phase_numbers == list(range(1, len(phase_numbers) + 1)), (
        f"phases should be consecutive 1..N, got {phase_numbers}"
    )


def test_log_lines_match_phase_numbers():
    """Each phase's 'Phase N complete' log line should match its heading number."""
    content = PLAN_TMPL.read_text()
    lines = content.splitlines()
    current_phase = None
    for line in lines:
        if line.startswith("## Phase "):
            current_phase = int(line.split()[2])
        elif current_phase is not None and "install-plan | Phase " in line and "complete" in line:
            # Extract the number after "Phase " in the log-line template
            idx = line.index("Phase ") + len("Phase ")
            # Number is the next whitespace-delimited token
            num_str = line[idx:].split()[0]
            logged = int(num_str)
            assert logged == current_phase, (
                f"log line references Phase {logged} inside heading Phase {current_phase}"
            )
