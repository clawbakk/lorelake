"""Render ingest v3 prompt templates through the plugin's one strict renderer (render-prompt.py).

Values are passed in-process, so large values (catalog, page blocks) never touch argv.
"""
import importlib.util
import os

from .common import read_text

LIB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(os.path.dirname(LIB_DIR), "prompts")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(LIB_DIR)), "templates")
_MODULE = []


class RenderError(RuntimeError):
    pass


def _renderer():
    if not _MODULE:
        spec = importlib.util.spec_from_file_location(
            "llake_render_prompt", os.path.join(LIB_DIR, "render-prompt.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE.append(mod)
    return _MODULE[0]


def placeholders(template_name):
    text = read_text(os.path.join(PROMPTS_DIR, template_name))
    return {m.group(1) for m in _renderer().PLACEHOLDER_RE.finditer(text)}


def render(template_name, values, config=None):
    path = os.path.join(PROMPTS_DIR, template_name)
    if not os.path.exists(path):
        raise RenderError("no template {}".format(template_name))
    mod = _renderer()
    rendered, unresolved, errors = mod.render_text(
        read_text(path), mod.template_section_name(template_name), config or {},
        {k: str(v) for k, v in values.items()}, TEMPLATES_DIR)
    if unresolved:
        raise RenderError("{}: unresolved placeholders: {}{}".format(
            template_name, ", ".join(unresolved), ("; " + "; ".join(errors)) if errors else ""))
    return rendered
