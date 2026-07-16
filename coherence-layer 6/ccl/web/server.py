"""Standard-library HTTP server (no framework dependency).

Serves three separate interfaces — a landing page, a Teacher console, and a
Student app — and dispatches /api/* to api.py. Each write/sensitive endpoint is
gated by the access controller against the caller's declared role, so the
student app cannot invoke teacher endpoints even though both hit the same API.

(The role is declared by the page for this local demo; a production deployment
would authenticate it. The enforcement path is the same RBAC layer either way.)

Run with:  python -m ccl.web
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import api
from ..access import Permission

APP = None  # set in main()

_ROUTES = {
    "/api/state": api.get_state,
    "/api/course": api.create_course,
    "/api/material": api.add_material,
    "/api/upload": api.upload_material,
    "/api/compile": api.compile_draft,
    "/api/publish": api.publish_contract,
    "/api/tutor": api.tutor,
    "/api/simulate": api.simulate_class,
    "/api/insights": api.insights,
}

# Endpoint -> permissions accepted (caller needs any one). Absent = open (reads).
_REQUIRED = {
    "/api/course": [Permission.CONTRACT_AUTHOR],
    "/api/material": [Permission.MATERIAL_IMPORT],
    "/api/upload": [Permission.MATERIAL_IMPORT],
    "/api/compile": [Permission.CONTRACT_AUTHOR],
    "/api/publish": [Permission.CONTRACT_PUBLISH],
    "/api/insights": [Permission.INSIGHT_VIEW],
    "/api/simulate": [Permission.INSIGHT_VIEW],
    "/api/tutor": [Permission.TUTOR_USE, Permission.TUTOR_PLAYGROUND],
}

_PAGES = {"/": "landing.html", "/teacher": "teacher.html", "/student": "student.html"}
_DIR = os.path.dirname(__file__)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self):
        page = _PAGES.get(self.path)
        if page:
            with open(os.path.join(_DIR, page), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(api.get_state(APP))
        elif self.path == "/api/student/state":
            self._json(api.student_state(APP))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        fn = _ROUTES.get(self.path)
        if fn is None:
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, 400)
            return

        required = _REQUIRED.get(self.path)
        if required:
            principal = APP.principal(body.get("role", "student"))
            if not any(APP.controller.can(principal, p) for p in required):
                self._json({"error": "This action isn't available from your interface."}, 403)
                return

        try:
            self._json(fn(APP, body))
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main(port: int = 8000) -> None:
    global APP
    APP = api.AppState()
    print(f"Curriculum Coherence Layer — running at http://localhost:{port}")
    print(f"  Teacher console:  http://localhost:{port}/teacher")
    print(f"  Student app:      http://localhost:{port}/student")
    print(f"Model: {APP.provider_label}")
    print("Press Ctrl+C to stop.")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
