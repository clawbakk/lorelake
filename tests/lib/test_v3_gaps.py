"""Gap record: validation, owed/carried, claims on page, the next record (stuck after 3)."""
import json
import subprocess
import sys

from v3_helpers import REPO_ROOT, page_text, write
from ingest_v3 import gaps

QUOTE = "The loop retries forever on error"


def proj(tmp_path, body="Intro.\n\n" + QUOTE + ".\n"):
    llake = tmp_path / "llake"
    write(llake, "wiki/arch/loop.md", page_text("Loop", "The tick loop", body))
    return llake


def gap(page="wiki/arch/loop.md", attempts=1, cause="declared", severity="minor", stuck=None, quote=QUOTE):
    return {"page": page, "severity": severity, "cause": cause, "since": "s0", "attempts": attempts,
            "stuck": attempts >= 3 if stuck is None else stuck,
            "claims": [{"quote": quote, "head": "src/x.py:1", "severity": severity, "source": "brief"}]}


def doc(*gs, ranges=()):
    return {"version": 1, "asOf": "h", "agent": "a", "date": "2026-10-02", "gaps": list(gs), "ranges": list(ranges)}


def outcome(outcome, **kw):
    o = {"page": "wiki/arch/loop.md", "severity": "major", "carried": False, "create": False,
         "outcome": outcome, "flags": [], "claimsLeft": [], "unverified": False, "attempts": 0, "since": None,
         "briefClaims": [{"quote": QUOTE, "head": "src/x.py:9", "severity": "major"}]}
    o.update(kw)
    return o


def test_validate_ok_and_quote_on_page(tmp_path):
    llake = proj(tmp_path)
    assert gaps.validate(doc(gap()), str(llake)) == []
    assert any("quote not on the page" in e for e in gaps.validate(doc(gap(quote="absent text")), str(llake)))


def test_validate_stuck_must_agree_with_attempts(tmp_path):
    assert any("disagrees" in e for e in gaps.validate(doc(gap(attempts=3, stuck=False))))
    assert any("disagrees" in e for e in gaps.validate(doc(gap(attempts=1, stuck=True))))


def test_validate_schema_errors_and_duplicates():
    assert gaps.validate({"version": 1}) != []
    assert any("duplicate" in e for e in gaps.validate(doc(gap(), gap())))


def test_owed_major():
    assert not gaps.owed_major(doc())
    assert not gaps.owed_major(doc(gap(severity="minor")))
    assert not gaps.owed_major(doc(gap(severity="major", attempts=3)))
    assert gaps.owed_major(doc(gap(severity="major")))


def test_carried_scopes(tmp_path):
    llake = proj(tmp_path)
    write(llake, "wiki/arch/other.md", page_text("O", "o", "x"))
    d = doc(gap(severity="major"), gap(page="wiki/arch/other.md", severity="minor", quote="x"),
            gap(page="wiki/arch/gone.md"), gap(page="wiki/arch/stuck.md", attempts=3))
    entries, dropped, stuck = gaps.carried(d, str(llake), "all")
    assert [e["page"] for e in entries] == ["wiki/arch/loop.md", "wiki/arch/other.md"]
    assert entries[0]["carried"] and entries[0]["claims"][0]["quote"] == QUOTE
    assert dropped == ["wiki/arch/gone.md"] and stuck == ["wiki/arch/stuck.md"]
    majors, _, _ = gaps.carried(d, str(llake), "major")
    assert [e["page"] for e in majors] == ["wiki/arch/loop.md"]


def test_claims_on_page_filters_and_dedupes():
    text = "a b c " + QUOTE
    claims = [{"quote": QUOTE, "head": "h"}, {"quote": QUOTE, "head": "h"}, {"quote": "nope", "head": "h"},
              {"quote": "", "head": "h"}]
    out = gaps.claims_on_page(claims, text)
    assert out == [{"quote": QUOTE, "head": "h", "severity": "minor", "source": "brief"}]


def test_placeholder_claim_quotes_description():
    text = page_text("T", "The tick loop", "body")
    assert gaps.placeholder_claim(text, "why")[0]["quote"] == "The tick loop"


def test_stuck_after_three_attempts(tmp_path):
    llake = proj(tmp_path)
    prev = doc(gap(severity="major", attempts=2, cause="writer-failed"))
    new, resolved, _ = gaps.next_record(prev, [outcome("writer-failed", carried=True)], [], [], "h2", "a2",
                                        "2026-10-03", str(llake), False)
    g = new["gaps"][0]
    assert (g["attempts"], g["stuck"], g["cause"], g["since"]) == (3, True, "writer-failed", "s0")
    assert gaps.validate(new, str(llake)) == []


def test_no_attempt_causes(tmp_path):
    llake = proj(tmp_path)
    for cause in ("run-cap", "timeout", "infra"):
        new, _, _ = gaps.next_record(doc(), [outcome(cause)], [], [], "h", "a", "d", str(llake), False)
        assert new["gaps"][0]["attempts"] == 0 and new["gaps"][0]["cause"] == cause


def test_corrected_carried_page_is_resolved(tmp_path):
    llake = proj(tmp_path, body="Rewritten.\n")
    new, resolved, _ = gaps.next_record(doc(gap()), [outcome("corrected", carried=True)], [], [], "h", "a", "d",
                                        str(llake), False)
    assert new["gaps"] == [] and resolved == ["wiki/arch/loop.md"]


def test_flagged_counts_attempt_and_takes_flag_severity(tmp_path):
    llake = proj(tmp_path)
    flags = [{"quote": QUOTE, "head": "`x` gone", "severity": "minor", "source": "check:removed-name"}]
    new, _, _ = gaps.next_record(doc(), [outcome("corrected", flags=flags)], [], [], "h", "a", "d", str(llake), False)
    g = new["gaps"][0]
    assert (g["cause"], g["attempts"], g["severity"], g["claims"][0]["source"]) == \
        ("flagged", 1, "minor", "check:removed-name")


def test_declared_uses_claims_left(tmp_path):
    llake = proj(tmp_path)
    left = [{"quote": QUOTE, "head": "h", "severity": "minor"}]
    new, _, _ = gaps.next_record(doc(), [outcome("declared", severity="minor", claimsLeft=left)], [], [],
                                 "h", "a", "d", str(llake), False)
    assert new["gaps"][0]["cause"] == "declared" and new["gaps"][0]["attempts"] == 1


def test_unverified_only_when_verifier_on(tmp_path):
    llake = proj(tmp_path)
    off, _, _ = gaps.next_record(doc(), [outcome("corrected", unverified=True)], [], [], "h", "a", "d",
                                 str(llake), False)
    on, _, _ = gaps.next_record(doc(), [outcome("corrected", unverified=True)], [], [], "h", "a", "d",
                                str(llake), True)
    assert off["gaps"] == []
    assert on["gaps"][0]["cause"] == "unverified" and on["gaps"][0]["severity"] == "minor"


def test_other_stale_is_flagged_without_attempt_and_unfound_quote_noted(tmp_path):
    llake = proj(tmp_path)
    write(llake, "wiki/arch/far.md", page_text("Far", "far", "Far page says the cache is unbounded."))
    other = [{"page": "llake/wiki/arch/far.md", "quote": "the cache is unbounded", "head": "h", "severity": "major"},
             {"page": "wiki/arch/far.md", "quote": "not there", "head": "h", "severity": "minor"},
             {"page": "wiki/arch/missing.md", "quote": "x", "head": "h", "severity": "minor"}]
    new, _, notes = gaps.next_record(doc(), [], other, [], "h", "a", "d", str(llake), False)
    g = new["gaps"][0]
    assert (g["page"], g["cause"], g["attempts"], g["severity"]) == ("wiki/arch/far.md", "flagged", 0, "major")
    assert g["claims"][0]["source"] == "writer:other"
    assert any("missing.md" in n for n in notes)
    assert any("far.md" in n and "not there" in n for n in notes)


def test_blurb_gap(tmp_path):
    llake = proj(tmp_path)
    write(llake, "wiki/arch/arch.md", page_text("Arch", "Category index for arch.", "Blurb says polling."))
    new, _, _ = gaps.next_record(doc(), [], [], [{"page": "wiki/arch/arch.md", "reason": "blurb false",
                                                   "claims": [{"quote": "Blurb says polling", "head": "h"}]}],
                                 "h", "a", "d", str(llake), False)
    assert new["gaps"][0]["cause"] == "blurb" and new["gaps"][0]["attempts"] == 0


def test_untouched_prev_gap_is_kept(tmp_path):
    llake = proj(tmp_path)
    new, _, _ = gaps.next_record(doc(gap()), [], [], [], "h", "a", "d", str(llake), False)
    assert new["gaps"][0]["since"] == "s0" and new["asOf"] == "h"


def test_vanished_carried_page_is_dropped_with_note(tmp_path):
    llake = proj(tmp_path)
    new, _, notes = gaps.next_record(doc(gap(page="wiki/arch/gone.md")), [], [], [], "h", "a", "d",
                                     str(llake), False)
    assert new["gaps"] == [] and any("gone.md" in n for n in notes)


def test_planned_page_not_created_is_noted(tmp_path):
    llake = proj(tmp_path)
    new, _, notes = gaps.next_record(doc(), [outcome("run-cap", page="wiki/arch/new.md", create=True)], [], [],
                                     "h", "a", "d", str(llake), False)
    assert new["gaps"] == [] and any("new.md" in n and "not created" in n for n in notes)


def test_ranges_are_appended(tmp_path):
    llake = proj(tmp_path)
    r = {"base": "a", "head": "b", "cause": "analysis-failed", "leads": ["x"]}
    new, _, _ = gaps.next_record(doc(ranges=[r]), [], [], [], "h", "a", "d", str(llake), False,
                                 new_ranges=[dict(r, base="b", head="c")])
    assert len(new["ranges"]) == 2


def run_cli(*args):
    return subprocess.run([sys.executable, str(REPO_ROOT / "hooks" / "lib" / "ingest-v3.py")] + list(args),
                          capture_output=True, text=True)


def test_cli_owed_major(tmp_path):
    llake = proj(tmp_path)
    assert run_cli("owed-major", "--llake-root", str(llake)).returncode == 1
    (llake / "ingest-gaps.json").write_text(json.dumps(doc(gap(severity="major"))))
    assert run_cli("owed-major", "--llake-root", str(llake)).returncode == 0
    (llake / "ingest-gaps.json").write_text("{not json")
    assert run_cli("owed-major", "--llake-root", str(llake)).returncode == 1


def test_cli_validate_gaps(tmp_path):
    llake = proj(tmp_path)
    res = run_cli("validate-gaps", "--llake-root", str(llake))
    assert res.returncode == 0 and res.stdout.strip() == "ABSENT"
    r = {"base": "a" * 40, "head": "b" * 40, "cause": "analysis-failed", "leads": ["wiki/arch/loop.md: `X` L3"]}
    (llake / "ingest-gaps.json").write_text(json.dumps(doc(gap(attempts=3, severity="major"), ranges=[r])))
    res = run_cli("validate-gaps", "--llake-root", str(llake))
    assert res.returncode == 0
    assert res.stdout.startswith("OK: 1 gaps (1 major, 1 stuck), 1 skipped ranges")
    assert "STUCK: wiki/arch/loop.md" in res.stdout and "RANGE: aaaaaaa..bbbbbbb" in res.stdout
    (llake / "ingest-gaps.json").write_text(json.dumps(doc(gap(quote="absent"))))
    res = run_cli("validate-gaps", "--llake-root", str(llake))
    assert res.returncode == 1 and res.stdout.startswith("INVALID:")


def test_other_stale_mixed_report_notes_the_off_page_quote(tmp_path):
    llake = proj(tmp_path)
    write(llake, "wiki/arch/far.md", page_text("Far", "far", "Far page says the cache is unbounded."))
    other = [{"page": "wiki/arch/far.md", "quote": "the cache is unbounded", "head": "h", "severity": "major"},
             {"page": "wiki/arch/far.md", "quote": "text nobody wrote", "head": "h", "severity": "minor"}]
    new, _, notes = gaps_next(llake, other)
    assert [c["quote"] for c in new["gaps"][0]["claims"]] == ["the cache is unbounded"]
    assert any("far.md" in n and "text nobody wrote" in n for n in notes)


def gaps_next(llake, other):
    return gaps.next_record(doc(), [], other, [], "h", "a", "d", str(llake), False)
