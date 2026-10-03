"""The quality bar's anchor rule ($0), shared by writer leads and the post-write checks.

An anchor `path:line[-end]` (or `path#L12`) on a page line is good when the path resolves to one
tracked file at head, the line is in range, and the cited lines (±2) show a backticked identifier from
the anchoring line, or failing that share a word part with it.
"""
import os
import re

from .common import BROKEN_ANCHOR_CAP, git, norm_ws, read_text

SOURCE_EXTS = ("ts|tsx|js|jsx|mjs|cjs|py|sh|bash|json|sql|ya?ml|go|rs|java|kt|rb|php|cs|c|h|cc|cpp|hpp|"
               "swift|toml|md|txt|prisma|graphql|css|scss|html")
ANCHOR_RE = re.compile(
    r"(?<![\w/.-])((?:[\w.@-]+/)*[\w@-][\w.@-]*\.(?:" + SOURCE_EXTS + r"))(?::|#L)(\d+)(?:\s*[-–]\s*L?(\d+))?")
PATHLIKE_RE = re.compile(r"(/|\.(?:" + SOURCE_EXTS + r")\b)")
SPAN_RE = re.compile(r"`([^`]+)`")
IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
IDENT_STOP = {"true", "false", "null", "undefined", "const", "let", "var", "return", "new", "this", "async",
              "await", "function", "class", "import", "export", "from", "void", "string", "number", "boolean",
              "any", "type", "interface", "readonly", "private", "public", "protected", "static"}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
TOKEN_STOP = {"this", "that", "with", "from", "const", "return", "string", "number", "boolean", "public", "private",
              "static", "import", "export", "class", "type", "async", "await", "void", "true", "false", "null",
              "undefined", "readonly", "function", "interface", "each", "when", "then", "else", "only", "also",
              "into", "have", "does", "line", "lines", "see", "file", "test", "tests"}


def _identifiers(line):
    idents = []
    for span in SPAN_RE.findall(line):
        if ANCHOR_RE.search(span) or PATHLIKE_RE.search(span):
            continue
        idents.extend(t for t in IDENT_RE.findall(span) if len(t) >= 3 and t not in IDENT_STOP)
    return list(dict.fromkeys(idents))


def _tokens(text):
    out = set()
    for word in WORD_RE.findall(text):
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", word):
            part = part.lower()
            if len(part) > 4 and part.endswith("s"):
                part = part[:-1]
            if len(part) >= 4 and part not in TOKEN_STOP and not part.isdigit():
                out.add(part)
    return out


class Source:
    """Tracked files outside llake/, read from the working tree (the code at head)."""

    def __init__(self, project):
        self.project = str(project)
        self.files = [f for f in git(self.project, "ls-files", check=False).splitlines()
                      if f and not f.startswith("llake/")]
        self.cache = {}

    def resolve(self, path):
        if path in self.files:
            return path
        suffix = [f for f in self.files if f.endswith("/" + path)]
        if len(suffix) == 1:
            return suffix[0]
        base = [f for f in self.files if os.path.basename(f) == os.path.basename(path)]
        return base[0] if len(base) == 1 else None

    def lines(self, path):
        if path not in self.cache:
            self.cache[path] = read_text(os.path.join(self.project, path)).split("\n")
        return self.cache[path]


def check_anchors(src, lines):
    flags = []
    for line in lines:
        matches = list(ANCHOR_RE.finditer(line))
        if not matches:
            continue
        idents = _identifiers(line)
        for m in matches:
            cited, l1 = re.sub(r"^(\.\./)+", "", m.group(1)), int(m.group(2))
            l2 = int(m.group(3)) if m.group(3) else l1
            real = src.resolve(cited)
            if real is None:
                flags.append((line, "anchor `{}` names no file at head".format(m.group(0))))
                continue
            text = src.lines(real)
            if l1 < 1 or l1 > len(text) or l2 < l1:
                flags.append((line, "anchor `{}` is outside the file ({} lines at head)".format(m.group(0), len(text))))
                continue
            window = "\n".join(text[max(0, l1 - 3):min(len(text), l2 + 2)])
            found = [i for i in idents
                     if re.search(r"(?<![A-Za-z0-9_$])" + re.escape(i) + r"(?![A-Za-z0-9_$])", window)]
            shared = _tokens(ANCHOR_RE.sub(" ", line)) & _tokens(window)
            if not found and not shared and idents:
                flags.append((line, "anchor `{}` shows none of {} within ±2 lines at head".format(
                    m.group(0), ", ".join("`{}`".format(n) for n in idents[:3]))))
    return flags


def broken_anchors(src, page_text, quotes=(), cap=BROKEN_ANCHOR_CAP):
    qs = [norm_ws(q) for q in quotes if q]
    hot, cold = [], []
    for n, line in enumerate(page_text.split("\n"), 1):
        flags = check_anchors(src, [line])
        if flags:
            nl = norm_ws(line)
            target = hot if any(q in nl or nl in q for q in qs) else cold
            target.extend("L{}: {}".format(n, why) for _line, why in flags)
    return (hot + cold)[:cap]
