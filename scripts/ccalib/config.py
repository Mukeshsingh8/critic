"""Configuration with defaults. The critic roster is data, never a constant --
swapping Fable for Haiku must be a one-line change."""
import fnmatch
import json
import os
from typing import Any, Dict, List

DEFAULTS = {
    "critics": [
        {"name": "design", "model": "claude-opus-5", "brief": "design", "timeout": 120},
        # Haiku, not Fable, is the shipped default. Fable is the most expensive
        # model available and has always-on thinking; making it the default
        # would spend an installer's plan at the top rate on every single edit
        # before they had decided the trade was worth it. Swapping it back is
        # one line -- see README, "Choosing the roster".
        {"name": "correctness", "model": "claude-haiku-4-5", "brief": "correctness", "timeout": 90},
    ],
    "path_critics": [
        {"globs": ["*migrations/*", "*migration/*", "*schema/*", "*entities/*", "*models/*"],
         "critic": {"name": "schema", "model": "claude-opus-5", "brief": "schema", "timeout": 120}}
    ],
    "ignore_globs": [
        "*/node_modules/*", "node_modules/*", ".cca/*", "*/.cca/*", "CF.md",
        "*/dist/*", "dist/*", "*/build/*", "build/*", "*.lock", "*lock.json",
        "*/.git/*", "*.min.js", "*/__pycache__/*",
    ],
    "phases": {
        "enabled": True,
        "plan": "",                 # empty -> newest file in docs/superpowers/plans/
        "test_command": "",         # empty -> the test rung is skipped with a warning
        "done": [],                 # escape hatch for history lost to squash/rebase
    },
    "arbiter": {
        "enabled": True,
        "block_wait": 180,          # seconds to hold a blocked edit for the human
        "commit_wait": 300,         # seconds to hold the phase gate for the human
        "stale_after": 15,          # heartbeat older than this means "no arbiter"
    },
}  # type: Dict[str, Any]


def _merge(base, user):
    # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """Merge one level deep. A user who sets `{"arbiter": {"block_wait": 60}}`
    means "change that one knob", not "delete the other three". Lists are
    replaced wholesale -- the critic roster is a complete statement, not a
    patch."""
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


def load(root):
    # type: (str) -> Dict[str, Any]
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    path = os.path.join(root, ".cca", "config.json")
    if not os.path.exists(path):
        return cfg
    try:
        with open(path) as fh:
            user = json.load(fh)
    except (ValueError, IOError):
        return cfg  # a broken config must not disable the plugin
    if isinstance(user, dict):
        _merge(cfg, user)
    return cfg


def should_review(path, cfg):
    # type: (str, Dict[str, Any]) -> bool
    normalised = path.replace(os.sep, "/")
    for glob in cfg.get("ignore_globs", []):
        if fnmatch.fnmatch(normalised, glob) or fnmatch.fnmatch("/" + normalised, glob):
            return False
    return True


def critics_for(path, cfg, plugin_root):
    # type: (str, Dict[str, Any], str) -> List[Dict[str, Any]]
    chosen = list(cfg.get("critics", []))
    normalised = path.replace(os.sep, "/")
    for rule in cfg.get("path_critics", []):
        if any(fnmatch.fnmatch(normalised, g) for g in rule.get("globs", [])):
            chosen.append(rule["critic"])
    resolved = []  # type: List[Dict[str, Any]]
    for critic in chosen:
        item = dict(critic)
        item["brief"] = os.path.join(plugin_root, "critics", "%s.md" % critic["brief"])
        resolved.append(item)
    return resolved
