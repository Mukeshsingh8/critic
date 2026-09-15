#!/usr/bin/env python3
"""Serve the CCA dashboard and compute the tuning statistics.

Usage: python3 scripts/dashboard.py [project_root] [port]
"""
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stats(events):
    # type: (List[Dict[str, Any]]) -> Dict[str, Any]
    edits = sum(1 for e in events if e["type"] == "edit_started")
    votes = [e["payload"].get("decision") for e in events if e["type"] == "vote"]

    per_file = {}  # type: Dict[str, List[str]]
    findings_by_critic = {}  # type: Dict[str, int]
    for e in events:
        if e["type"] != "critic_verdict":
            continue
        p = e["payload"]
        per_file.setdefault(p.get("file", ""), []).append(p.get("verdict", ""))
        name = p.get("critic", "?")
        findings_by_critic[name] = findings_by_critic.get(name, 0) + len(p.get("findings", []))

    compared = [v for v in per_file.values() if len(v) >= 2]
    agreement = None  # type: Optional[float]
    if compared:
        agreed = sum(1 for v in compared if len(set(v)) == 1)
        agreement = round(float(agreed) / len(compared), 3)

    return {
        "edits": edits,
        "blocked": votes.count("block"),
        "queued": votes.count("queue"),
        "passed": votes.count("pass"),
        "agreement_rate": agreement,
        "findings_by_critic": findings_by_critic,
    }


def phase_strip(events):
    # type: (List[Dict[str, Any]]) -> Dict[str, Any]
    strip = {"current": None, "total": None, "title": "", "rung": "",
             "checks": {}, "fenced": 0}  # type: Dict[str, Any]
    for e in events:
        kind = e.get("type")
        p = e.get("payload", {})
        if kind == "phase_context":
            strip["current"] = p.get("current")
            strip["total"] = p.get("total")
        elif kind == "phase_gate":
            strip["rung"] = p.get("rung", "")
            strip["checks"] = p.get("checks", {})
            if strip["current"] is None:
                strip["current"] = p.get("phase")
        elif kind == "phase_fenced" and not p.get("allowed"):
            strip["fenced"] += 1
    return strip


def serve(root, port):
    # type: (str, int) -> int
    import functools
    import http.server
    import socketserver

    target = os.path.join(root, ".cca")
    os.makedirs(target, exist_ok=True)

    class Utf8Handler(http.server.SimpleHTTPRequestHandler):
        """SimpleHTTPRequestHandler sends text/html with no charset, so browsers
        fall back to latin-1 and mangle every non-ASCII character."""

        def guess_type(self, path):
            base = http.server.SimpleHTTPRequestHandler.guess_type(self, path)
            if base.startswith("text/") and "charset=" not in base:
                return base + "; charset=utf-8"
            if path.endswith(".jsonl"):
                return "text/plain; charset=utf-8"
            return base

        def translate_path(self, path):
            """Board assets are served live from the plugin's dashboard/, and
            only the event log comes from .cca/. Copying them in at startup
            meant every edit to the board was invisible until a restart."""
            clean = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
            if clean in ("", "index.html", "dashboard.css", "dashboard.js"):
                return os.path.join(PLUGIN_ROOT, "dashboard", clean or "index.html")
            return http.server.SimpleHTTPRequestHandler.translate_path(self, path)

        def end_headers(self):
            # The board is re-copied from dashboard/ on every start. Without
            # this the browser keeps serving the previous skin and engine, and
            # an edit to the dashboard looks like it did nothing.
            self.send_header("Cache-Control", "no-store, must-revalidate")
            http.server.SimpleHTTPRequestHandler.end_headers(self)

        def log_message(self, *args):
            pass  # a request log per 500ms poll is noise, not information

        def _answer(self, project_root, body):
            """Answer a live arbiter request from the board.

            This is what makes a fix immediate: the PostToolUse hook is still
            holding Claude, so the decision lands while it is stopped rather
            than waiting for the next Stop."""
            from ccalib import arbiter as arbiter_mod

            choice = str(body.get("choice", ""))
            where = arbiter_mod.paths(project_root)
            try:
                names = sorted(n for n in os.listdir(where["pending"]) if n.endswith(".json"))
            except OSError:
                names = []
            for name in names:
                request = arbiter_mod.read_json(os.path.join(where["pending"], name))
                if not request or choice not in (request.get("options") or []):
                    continue
                arbiter_mod.write_json(
                    os.path.join(where["decisions"], name),
                    {"id": request.get("id"), "choice": choice,
                     "ts": time.time(), "note": str(body.get("note", ""))})
                return {"ok": True, "delivered": True, "id": request.get("id")}
            return {"ok": True, "delivered": False}

        def do_POST(self):
            """The only write this server accepts: queueing a fix the operator
            accepted on the board. Bound to 127.0.0.1, and it can do exactly
            one thing -- it never touches code, only the queue."""
            from ccalib import events as events_mod
            from ccalib import fixes as fixes_mod

            route = self.path.split("?", 1)[0].rstrip("/")
            if route not in ("/fixes", "/fixes/skip", "/fixes/dismiss", "/answer"):
                self.send_error(404, "no such endpoint")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except (ValueError, UnicodeDecodeError):
                self.send_error(400, "expected a JSON body")
                return
            if not isinstance(body, dict):
                self.send_error(400, "expected a JSON object")
                return

            if route == "/answer":
                reply = self._answer(root, body)
            elif route == "/fixes/skip":
                item = fixes_mod.skip(root, body)
                events_mod.append("fix_skipped",
                                  {"id": item.get("id", ""), "key": item.get("key", ""),
                                   "file": item.get("file", ""), "via": "dashboard"}, root)
                reply = {"ok": True, "id": item.get("id", ""), "status": "dismissed"}
            elif route == "/fixes/dismiss":
                count = fixes_mod.dismiss(root, str(body.get("id", "")))
                reply = {"ok": bool(count)}
            else:
                item = fixes_mod.accept(root, body)
                events_mod.append("fix_accepted",
                                  {"id": item["id"], "file": item["file"],
                                   "key": item.get("key", ""), "via": "dashboard"}, root)
                reply = {"ok": True, "id": item["id"], "status": item["status"]}

            payload = json.dumps(reply).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    class Server(socketserver.TCPServer):
        # without this, restarting inside the TIME_WAIT window fails to bind
        allow_reuse_address = True

    # `directory=` rather than os.chdir: chdir is process-global and would
    # corrupt anything else running in this interpreter.
    handler = functools.partial(Utf8Handler, directory=target)

    try:
        httpd = Server(("127.0.0.1", port), handler)
    except OSError as exc:
        sys.stderr.write(
            "CCA dashboard: cannot listen on port %s - %s.\n"
            "Another server is probably already using it. Try a different port:\n"
            "  python3 scripts/dashboard.py %s %d\n"
            % (port, exc, root, (port or 7878) + 1))
        return 1

    with httpd:
        print("CCA dashboard: http://127.0.0.1:%d/index.html"
              % httpd.server_address[1])
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    project = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    listen_port = int(sys.argv[2]) if len(sys.argv) > 2 else 7878
    sys.exit(serve(project, listen_port))
