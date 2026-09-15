"""Parse a writing-plans document into phases.

Reads only what the plan format guarantees: `### Task N: Title` headings and
the backticked paths in the `**Files:**` block beneath each one. Tolerant by
design -- an unrecognised line is skipped, never fatal, because a parse error
must degrade to "no gate", not to "everything is out of scope".
"""
import re
from typing import Any, Dict, List

_HEADING = re.compile(r"^###\s+Task\s+(\d+)\s*:\s*(.+?)\s*$")
_FILE_LINE = re.compile(r"^\s*-\s*(?:Create|Modify|Test)\s*:\s*`([^`]+)`")
_FILES_HEADER = re.compile(r"^\s*\*\*Files:\*\*\s*$")
_ANY_BOLD_HEADER = re.compile(r"^\s*\*\*[A-Za-z][^*]*:\*\*\s*$")


def _strip_range(path):
    # type: (str) -> str
    """`scripts/x.py:12-40` -> `scripts/x.py`"""
    return re.sub(r":\d+(?:-\d+)?$", "", path.strip())


def parse(text):
    # type: (str) -> List[Dict[str, Any]]
    if not text:
        return []
    lines = text.splitlines()

    starts = []  # type: List[Any]
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            starts.append((index, int(match.group(1)), match.group(2)))

    phases = []  # type: List[Dict[str, Any]]
    for position, (index, number, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        files = []  # type: List[str]
        in_files = False
        for line in lines[index:end]:
            if _FILES_HEADER.match(line):
                in_files = True
                continue
            if in_files:
                match = _FILE_LINE.match(line)
                if match:
                    files.append(_strip_range(match.group(1)))
                    continue
                if _ANY_BOLD_HEADER.match(line):
                    in_files = False
                elif line.strip() and not line.lstrip().startswith("-"):
                    in_files = False
        phases.append({"number": number, "title": title, "files": files,
                       "start_line": index, "end_line": end})
    return phases
