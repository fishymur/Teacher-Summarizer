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

import base64
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import api
from ..access import Permission

APP = None  # set in main()

# Optional shared-password gate for a small private group. When
# CCL_ACCESS_PASSWORD is set, every request needs HTTP Basic Auth with that
# password (any username). Unset = open, which is fine for local use. This gate
# only keeps outsiders out; it does NOT decide teacher vs student — the page
# still declares that, and the RBAC layer enforces it per endpoint.
_ACCESS_PASSWORD = os.environ.get("CCL_ACCESS_PASSWORD") or None
_PUBLIC_PATHS = {"/healthz"}  # unauthenticated so host health checks pass

_ROUTES = {
    "/api/state": api.get_state,
    "/api/course": api.create_course,
    "/api/material": api.add_material,
    "/api/material/delete": api.delete_material,
    "/api/upload": api.upload_material,
    "/api/compile": api.compile_draft,
    "/api/publish": api.publish_contract,
    "/api/publish-course": api.publish_course,
    "/api/tutor": api.tutor,
    "/api/simulate": api.simulate_class,
    "/api/insights": api.insights,
}

# Endpoint -> permissions accepted (caller needs any one). Absent = open (reads).
_REQUIRED = {
    "/api/course": [Permission.CONTRACT_AUTHOR],
    "/api/material": [Permission.MATERIAL_IMPORT],
    "/api/material/delete": [Permission.MATERIAL_IMPORT],
    "/api/upload": [Permission.MATERIAL_IMPORT],
    "/api/compile": [Permission.CONTRACT_AUTHOR],
    "/api/publish": [Permission.CONTRACT_PUBLISH],
    "/api/publish-course": [Permission.CONTRACT_PUBLISH],
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

    def _authorized(self) -> bool:
        """True if access is allowed. When no password is configured, always
        open. Otherwise require HTTP Basic Auth matching the shared password
        (constant-time compare). Sends the 401 challenge itself when denied."""
        if _ACCESS_PASSWORD is None:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                _, _, supplied = decoded.partition(":")
                if hmac.compare_digest(supplied, _ACCESS_PASSWORD):
                    return True
            except Exception:  # noqa: BLE001 — malformed header -> treat as unauthorized
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Coherence Layer"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if self.path in _PUBLIC_PATHS:
            self._send(200, b"ok", "text/plain")
            return
        if not self._authorized():
            return
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
        if not self._authorized():
            return
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
            result = fn(APP, body)
            APP.persist()  # commit the write on a file DB so it survives restart
            self._json(result)
        except Exception as e:  # noqa: BLE001
            APP.rollback()  # discard the failed transaction; keep the session usable
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def _resolve_db_url() -> tuple[str, str]:
    """Return (sqlalchemy_url, human_label).

    Priority: DATABASE_URL (a full url, e.g. Postgres from Neon) > CCL_DB (a full
    url, or a bare path treated as a SQLite file) > a local SQLite file default.
    Postgres schemes are normalized to the psycopg (v3) driver.
    """
    raw = os.environ.get("DATABASE_URL") or os.environ.get("CCL_DB")
    if not raw:
        path = "./ccl_demo.db"
        return f"sqlite+pysqlite:///{path}", f"SQLite file: {os.path.abspath(path)}"
    if "://" not in raw:  # bare path -> local SQLite file (back-compat)
        return f"sqlite+pysqlite:///{raw}", f"SQLite file: {os.path.abspath(raw)}"
    url = raw
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    label = "PostgreSQL" if url.startswith("postgresql") else url.split("://", 1)[0]
    return url, label


def main(port: int | None = None) -> None:
    global APP
    # Bind/port from env so the same code runs locally and on a host. Local
    # default stays 127.0.0.1 (not exposed); a container/PaaS sets HOST=0.0.0.0
    # and injects PORT.
    host = os.environ.get("HOST", "127.0.0.1")
    port = port or int(os.environ.get("PORT") or os.environ.get("CCL_PORT") or "8000")
    db_url, db_label = _resolve_db_url()
    APP = api.AppState(db_url)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"Curriculum Coherence Layer — running at http://{shown}:{port}")
    print(f"  Teacher console:  http://{shown}:{port}/teacher")
    print(f"  Student app:      http://{shown}:{port}/student")
    print(f"Model: {APP.provider_label}")
    gate = "ON (shared password required)" if _ACCESS_PASSWORD else "OFF (open — set CCL_ACCESS_PASSWORD to require a password)"
    print(f"Access gate: {gate}")
    print(f"Database: {db_label}")
    print("  (persists across restarts; bump SCHEMA_VERSION or reset the DB to re-seed)")
    print("Press Ctrl+C to stop.")
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
