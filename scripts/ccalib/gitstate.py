"""Read-only git inspection.

Never mutates anything -- no add, no commit, no init. `None` is returned
whenever the answer cannot be determined, and every caller must treat it as
"no gate" rather than as "clean" or "nothing shipped".
"""
import re
import subprocess
from typing import Dict, List, Optional

_TAG = re.compile(r"^\[phase-(\d+)\]")
_TIMEOUT = 15


def _run(root, args):
    # type: (str, List[str]) -> Optional[str]
    try:
        proc = subprocess.Popen(["git"] + args, cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        out, _err = proc.communicate(timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return out


def is_repo(root):
    # type: (str) -> bool
    out = _run(root, ["rev-parse", "--is-inside-work-tree"])
    return bool(out) and out.strip() == "true"


def phase_commits(root):
    # type: (str) -> Optional[Dict[int, str]]
    if not is_repo(root):
        return None
    out = _run(root, ["log", "--format=%h%x09%s"])
    if out is None:
        return None
    found = {}  # type: Dict[int, str]
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        match = _TAG.match(subject.strip())
        if match:
            found.setdefault(int(match.group(1)), sha)
    return found


def dirty_paths(root):
    # type: (str) -> Optional[List[str]]
    if not is_repo(root):
        return None
    # -uall is essential: without it git collapses an untracked directory to
    # `dir/`, hiding every file inside it from phase classification.
    out = _run(root, ["status", "--porcelain", "-uall"])
    if out is None:
        return None
    paths = []  # type: List[str]
    for line in out.splitlines():
        if len(line) > 3:
            paths.append(line[3:].strip())
    return paths
