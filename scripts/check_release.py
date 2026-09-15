#!/usr/bin/env python3
"""Refuse to publish with placeholders still in the manifests.

Every identity field is a placeholder until the operator fills it in, which is
deliberate -- but a placeholder that reaches a public registry is permanent and
embarrassing, so this exits non-zero while any remain.

    python3 scripts/check_release.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACEHOLDER = re.compile(r"__[A-Z_]+__")

# `vsce` validates the publisher id syntactically, so that one placeholder has
# to be a legal identifier -- which means it cannot be caught by the pattern
# above and has to be named here instead.
LITERALS = {
    # Nothing outstanding. Add a literal here if a placeholder is ever
    # introduced that has to stay syntactically valid (as the publisher id did,
    # because vsce validates it before anything else runs).
}

FILES = [
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "extension/package.json",
    "LICENSE",
    "extension/LICENSE",
    "README.md",
    "extension/README.md",
]


def main():
    # type: () -> int
    problems = []

    for name in FILES:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            problems.append("%s is missing" % name)
            continue
        with open(path) as fh:
            body = fh.read()
        for found in sorted(set(PLACEHOLDER.findall(body))):
            problems.append("%s still contains %s" % (name, found))
        for literal in LITERALS.get(name, []):
            if literal in body:
                problems.append("%s still contains the placeholder %r" % (name, literal))

    manifest = os.path.join(ROOT, "extension", "package.json")
    if os.path.exists(manifest):
        with open(manifest) as fh:
            package = json.load(fh)
        if package.get("private"):
            problems.append('extension/package.json has "private": true; vsce will refuse it')
        for field in ("publisher", "icon", "repository", "license"):
            if not package.get(field):
                problems.append("extension/package.json is missing %s" % field)

    for name in ("extension/icon.png", "extension/CHANGELOG.md"):
        if not os.path.exists(os.path.join(ROOT, name)):
            problems.append("%s is missing" % name)

    if problems:
        sys.stderr.write("Not ready to publish:\n")
        for problem in problems:
            sys.stderr.write("  - %s\n" % problem)
        sys.stderr.write("\nFill these in, then run this again.\n")
        return 1

    sys.stdout.write("Ready to publish.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
