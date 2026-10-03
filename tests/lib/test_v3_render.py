"""ingest_v3.render goes through render-prompt.py and is strict."""
import pytest

import v3_helpers  # noqa: F401
from ingest_v3 import render


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "PROMPTS_DIR", str(tmp_path))
    (tmp_path / "ingest.v3.demo.md.tmpl").write_text("Head {{HEAD}}; body {{BODY}}.\n")
    return tmp_path


def test_renders_all_values(prompts):
    assert render.render("ingest.v3.demo.md.tmpl", {"HEAD": "abc", "BODY": "x {{NOT_A_VAR}}"}) == \
        "Head abc; body x {{NOT_A_VAR}}.\n"


def test_missing_value_raises_naming_it(prompts):
    with pytest.raises(render.RenderError) as err:
        render.render("ingest.v3.demo.md.tmpl", {"HEAD": "abc"})
    assert "BODY" in str(err.value)


def test_placeholders(prompts):
    assert render.placeholders("ingest.v3.demo.md.tmpl") == {"HEAD", "BODY"}


def test_unknown_template_raises(prompts):
    with pytest.raises(render.RenderError):
        render.render("ingest.v3.nope.md.tmpl", {})


def test_default_dirs_point_into_the_plugin():
    assert render.PROMPTS_DIR.endswith("hooks/prompts")
    assert render.TEMPLATES_DIR.endswith("templates")
