"""Renderer strictness for every placeholder of every v3 template (spec §18)."""
import pytest

import v3_helpers  # noqa: F401
from ingest_v3 import render

TEMPLATES = [
    "ingest.v3.analysis.md.tmpl",
    "ingest.v3.recall.md.tmpl",
]
CASES = [(t, n) for t in TEMPLATES for n in sorted(render.placeholders(t))]


@pytest.mark.parametrize("template", TEMPLATES)
def test_template_renders_with_every_placeholder(template):
    names = render.placeholders(template)
    assert names, template
    out = render.render(template, {n: "<{}>".format(n) for n in names})
    for n in names:
        assert "<{}>".format(n) in out
    assert not render._renderer().PLACEHOLDER_RE.search(out)


@pytest.mark.parametrize("template,name", CASES)
def test_each_placeholder_is_required(template, name):
    values = {n: "x" for n in render.placeholders(template) if n != name}
    with pytest.raises(render.RenderError) as err:
        render.render(template, values)
    assert name in str(err.value)
