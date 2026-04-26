"""LoreLake — minimal YAML frontmatter parser/serializer.

Handles only the shapes wiki pages actually use:
  - top-level scalar `key: value`
  - `key: "double-quoted"` strings
  - `key: [a, b, c]` inline scalar lists
  - block lists under a key:
        related:
          - "[[a]]"
          - "[[b]]"

Anything richer raises FrontmatterParseError. This is intentional — wiki pages
should not contain richer structures, and a permissive parser would silently
mangle them on round-trip. Pure stdlib, no pyyaml dependency.
"""
import re

DELIMITER = "---"


class FrontmatterParseError(ValueError):
    pass


def split(text):
    """Return (frontmatter_text, body_text). frontmatter_text is empty if absent."""
    if not text.startswith(DELIMITER + "\n") and not text.startswith(DELIMITER + "\r\n"):
        return "", text
    end = text.find("\n" + DELIMITER, len(DELIMITER))
    if end == -1:
        return "", text
    fm = text[len(DELIMITER) + 1:end]
    rest_start = end + len("\n" + DELIMITER)
    if rest_start < len(text) and text[rest_start] == "\n":
        rest_start += 1
    return fm, text[rest_start:]


_SCALAR_KEY_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*)$")


def _strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _parse_inline_list(s):
    inner = s.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise FrontmatterParseError(f"expected inline list [...] got {s!r}")
    inner = inner[1:-1].strip()
    if not inner:
        return []
    return [_strip_quotes(x.strip()) for x in inner.split(",")]


def parse(fm_text):
    """Return ordered dict-like (regular dict in py3.7+) of frontmatter values."""
    out = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith(" ") or line.startswith("\t"):
            raise FrontmatterParseError(f"unexpected indented line outside block context: {line!r}")
        m = _SCALAR_KEY_RE.match(line)
        if not m:
            raise FrontmatterParseError(f"cannot parse line: {line!r}")
        key, rhs = m.group(1), m.group(2).strip()
        if rhs == "":
            # Block list expected starting next line with "  - "
            items = []
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                if not ln.strip():
                    j += 1
                    continue
                if ln.startswith("  - "):
                    items.append(_strip_quotes(ln[4:]))
                    j += 1
                else:
                    break
            out[key] = items
            i = j
        elif rhs.startswith("["):
            out[key] = _parse_inline_list(rhs)
            i += 1
        else:
            out[key] = _strip_quotes(rhs)
            i += 1
    return out


def _format_scalar(v):
    s = str(v)
    if s == "" or any(c in s for c in [":", "#", "[", "]", "{", "}", ","]):
        return f'"{s}"'
    if s in ("true", "false", "null") or re.match(r"^-?\d", s):
        return f'"{s}"'
    return s


_BARE_SCALAR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _needs_quotes(value):
    """Return True if a string value should be double-quoted in YAML output."""
    if value == "":
        return True
    if not _BARE_SCALAR_RE.match(value):
        return True
    return False


def serialize(d):
    """Render the parsed frontmatter back to text (without the --- delimiters)."""
    lines = []
    for key, value in d.items():
        if isinstance(value, list):
            if (value and len(value) <= 5
                    and all(isinstance(x, str) and " " not in x
                            and not x.startswith("[[") for x in value)):
                # Short list of bare scalars → inline
                lines.append(f"{key}: [{', '.join(value)}]")
            else:
                lines.append(f"{key}:")
                for item in value:
                    if isinstance(item, str) and not (item.startswith('"') and item.endswith('"')):
                        lines.append(f'  - "{item}"')
                    else:
                        lines.append(f"  - {item}")
        else:
            if isinstance(value, str) and _needs_quotes(value):
                lines.append(f'{key}: "{value}"')
            elif isinstance(value, str):
                lines.append(f"{key}: {value}")
            else:
                lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"
