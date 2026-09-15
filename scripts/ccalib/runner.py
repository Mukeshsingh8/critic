"""Spawn headless Claude critics in parallel.

Every failure path degrades to `warn`. A critic must never be able to block
the coder by being slow, broken, or absent.
"""
import concurrent.futures
import os
import subprocess
from typing import Any, Dict, List

from . import verdict as verdict_mod

DEFAULT_TOOLS = ["Read", "Grep", "Glob"]


def build_command(model, brief_path, allowed_tools):
    # type: (str, str, List[str]) -> List[str]
    cmd = [
        "claude", "-p",
        "--model", model,
        "--system-prompt-file", brief_path,
        "--permission-mode", "dontAsk",
        "--allowedTools",
    ]
    cmd.extend(allowed_tools)
    return cmd


def _degraded(critic, reason):
    # type: (str, str) -> Dict[str, Any]
    return {
        "critic": critic, "stated": "", "verdict": "warn", "degraded": True,
        "findings": [{
            "severity": "minor", "kind": "correctness",
            "file": "(critic)", "line": 0,
            "issue": "Critic did not complete: %s" % reason,
            "evidence": "", "suggestion": "",
        }],
    }


def run_one(model, brief_path, payload, timeout, critic_name, cwd):
    # type: (str, str, str, int, str, str) -> Dict[str, Any]
    env = os.environ.copy()
    env["CCA_INNER"] = "1"  # recursion firewall -- set on the CHILD only
    cmd = build_command(model, brief_path, DEFAULT_TOOLS)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=cwd, universal_newlines=True,
        )
    except OSError as exc:
        return _degraded(critic_name, "cannot launch claude (%s)" % exc)

    try:
        out, _err = proc.communicate(input=payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return _degraded(critic_name, "timed out after %ss" % timeout)

    if proc.returncode != 0:
        return _degraded(critic_name, "exit code %s" % proc.returncode)

    return verdict_mod.parse(out, critic_name)


def run_all(critics, payload, cwd):
    # type: (List[Dict[str, Any]], str, str) -> List[Dict[str, Any]]
    if not critics:
        return []
    results = []  # type: List[Dict[str, Any]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(critics)) as pool:
        futures = [
            pool.submit(run_one, c["model"], c["brief"], payload,
                        c.get("timeout", 120), c["name"], cwd)
            for c in critics
        ]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # a runner bug must not block the coder
                results.append(_degraded("unknown", "runner error: %s" % exc))
    return results
