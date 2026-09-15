"""Find the files a Bash command would WRITE.

The phase fence originally matched only Edit and Write, so `cat > file <<EOF`
walked straight through it. This closes that hole.

The hard requirement is no false positives: `grep foo cli.py` mentions a file
but writes nothing, and denying that would make the fence unusable. So this
detects write *operations* -- redirection targets and the operands of known
mutating commands -- never mere mentions of a path.

Interpreter payloads (`python3 -c ...`, `python3 - <<'PY'`, `sh -c ...`,
`node -e ...`) are inspected STATICALLY -- the Python ones via `ast.parse`,
never by executing anything.

The remaining limit is fundamental rather than an omission: a dynamic path
(`open(name, "w")` where `name` is computed) cannot be resolved without running
the program. Those are not detected, and the fence must not guess at them.
"""
import ast
import re
import shlex
from typing import Any, List, Optional, Set

# Commands whose trailing operand(s) are written rather than read.
_TEE = "tee"
_COPIERS = frozenset(["cp", "mv", "install"])
_TOUCHERS = frozenset(["touch", "truncate"])

_SEPARATORS = frozenset([";", "&&", "||", "|", "&"])

_PYTHON = re.compile(r"^(python[0-9.]*|py)$")
_SHELL = frozenset(["sh", "bash", "zsh", "dash"])
_NODE = frozenset(["node", "nodejs"])

# open() modes that write. "+" covers r+, and w/a/x are writes by definition.
_WRITE_MODE = re.compile(r"[wax+]")

# `<<DELIM` / `<<'DELIM'` / `<<-DELIM` ... up to a line that is exactly DELIM
_HEREDOC = re.compile(
    r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?[^\n]*\n(.*?)^\s*\1\s*$",
    re.S | re.M)

_NODE_WRITERS = re.compile(
    r"\b(?:writeFileSync|appendFileSync|writeFile|appendFile|createWriteStream|"
    r"rmSync|unlinkSync|renameSync)\s*\(\s*['\"]([^'\"]+)['\"]")

_PY_OS_ONE_ARG = frozenset(["remove", "unlink", "rmdir", "removedirs", "truncate"])
_PY_OS_TWO_ARG = frozenset(["rename", "renames", "replace", "link", "symlink"])
_PY_SHUTIL_DEST = frozenset(["copy", "copy2", "copyfile", "copytree", "move"])
_PY_PATH_WRITERS = frozenset(["write_text", "write_bytes", "unlink", "rename",
                              "replace", "touch", "mkdir"])

_MAX_DEPTH = 3


def _is_flag(token):
    # type: (str) -> bool
    return token.startswith("-")


def _tokenise(command):
    # type: (str) -> List[str]
    """shlex keeps quoted content in one token, so a `>` inside a string is not
    mistaken for a redirection. An unparseable command yields nothing, which
    means 'allow' -- the fence never guesses."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return []


def _segments(tokens):
    # type: (List[str]) -> List[List[str]]
    """Split a token stream on shell separators into individual commands."""
    out = [[]]  # type: List[List[str]]
    for token in tokens:
        if token in _SEPARATORS:
            out.append([])
        else:
            out[-1].append(token)
    return [segment for segment in out if segment]


def _redirect_targets(tokens):
    # type: (List[str]) -> List[str]
    targets = []  # type: List[str]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        # `>`, `>>`, `1>`, `2>>` as their own token -- target is the next one
        stripped = token.lstrip("0123456789")
        if stripped in (">", ">>", ">|", ">&") and stripped.startswith(">"):
            if stripped == ">&":       # fd duplication, not a file write
                index += 2
                continue
            if index + 1 < len(tokens):
                targets.append(tokens[index + 1])
            index += 2
            continue
        index += 1
    return targets


def _command_targets(tokens):
    # type: (List[str]) -> List[str]
    if not tokens:
        return []
    name = tokens[0].rsplit("/", 1)[-1]
    operands = [t for t in tokens[1:] if not _is_flag(t) and not t.startswith(">")]

    if name == _TEE:
        return operands
    if name in _COPIERS:
        return operands[-1:] if len(operands) >= 2 else []
    if name in _TOUCHERS:
        return operands
    if name == "sed":
        # only `-i` rewrites in place; BSD sed takes an extra '' argument
        if any(t == "-i" or t.startswith("-i") for t in tokens[1:]):
            return [t for t in operands if "/" in t or t.endswith(
                (".py", ".ts", ".js", ".md", ".json", ".txt", ".yml", ".yaml"))] or operands[-1:]
        return []
    if name == "dd":
        return [t.split("=", 1)[1] for t in tokens[1:] if t.startswith("of=")]
    return []


def _const_str(node):
    # type: (Any) -> Optional[str]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _receiver_arg(node):
    # type: (Any) -> Optional[str]
    """For `Path('x').write_text(...)`, recover the 'x'."""
    if isinstance(node, ast.Call) and node.args:
        return _const_str(node.args[0])
    return None


def _python_write_targets(source):
    # type: (str) -> List[str]
    """Static analysis only. Nothing is executed."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    targets = []  # type: List[str]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

        # Rules are independent, NOT mutually exclusive: `rename`, `replace` and
        # `unlink` are both pathlib methods and os functions, so an elif chain
        # would let the pathlib branch shadow `os.rename` and find nothing.
        if name == "open":
            path = _const_str(node.args[0]) if node.args else None
            mode = _const_str(node.args[1]) if len(node.args) > 1 else None
            for keyword in node.keywords:
                if keyword.arg == "mode":
                    mode = _const_str(keyword.value) or mode
            if path and mode and _WRITE_MODE.search(mode):
                targets.append(path)

        if name in _PY_PATH_WRITERS and isinstance(func, ast.Attribute):
            # `Path('x').write_text(...)` -- the receiver must itself be a call
            path = _receiver_arg(func.value)
            if path:
                targets.append(path)

        if name in _PY_OS_ONE_ARG:
            path = _const_str(node.args[0]) if node.args else None
            if path:
                targets.append(path)

        if name in _PY_OS_TWO_ARG:
            for argument in node.args[:2]:
                path = _const_str(argument)
                if path:
                    targets.append(path)

        if name in _PY_SHUTIL_DEST:
            path = _const_str(node.args[1]) if len(node.args) > 1 else None
            if path:
                targets.append(path)
    return targets


def _heredoc_bodies(command):
    # type: (str) -> List[str]
    return [body for _delim, body in _HEREDOC.findall(command)]


def _interpreter_targets(segment, command, depth):
    # type: (List[str], str, int) -> List[str]
    if not segment or depth >= _MAX_DEPTH:
        return []
    name = segment[0].rsplit("/", 1)[-1]
    targets = []  # type: List[str]

    if _PYTHON.match(name):
        for index, token in enumerate(segment[1:], start=1):
            if token == "-c" and index + 1 < len(segment):
                targets.extend(_python_write_targets(segment[index + 1]))
        # `python3 - <<'PY'` feeds the script on stdin
        for body in _heredoc_bodies(command):
            targets.extend(_python_write_targets(body))
    elif name in _SHELL:
        for index, token in enumerate(segment[1:], start=1):
            if token == "-c" and index + 1 < len(segment):
                targets.extend(write_targets(segment[index + 1], depth + 1))
    elif name in _NODE:
        for index, token in enumerate(segment[1:], start=1):
            if token in ("-e", "--eval", "-p") and index + 1 < len(segment):
                targets.extend(_NODE_WRITERS.findall(segment[index + 1]))
        for body in _heredoc_bodies(command):
            targets.extend(_NODE_WRITERS.findall(body))
    return targets


def write_targets(command, depth=0):
    # type: (str, int) -> List[str]
    if not command or not command.strip():
        return []
    tokens = _tokenise(command)
    if not tokens:
        return []

    found = []  # type: List[str]
    seen = set()  # type: Set[str]
    for segment in _segments(tokens):
        candidates = (_redirect_targets(segment)
                      + _command_targets(segment)
                      + _interpreter_targets(segment, command, depth))
        for target in candidates:
            target = target.strip()
            if not target or target.startswith("&") or target in seen:
                continue
            seen.add(target)
            found.append(target)
    return found
