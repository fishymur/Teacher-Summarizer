#!/usr/bin/env python3
"""Regenerate the auto-generated snapshot block in HANDOFF.md from the live code.

Stdlib-only, no side effects beyond editing HANDOFF.md. Run from the repo root
(or anywhere — it locates the repo relative to this file). Safe to run repeatedly
and on every commit via the scripts/pre-commit hook.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
HANDOFF = ROOT / "HANDOFF.md"
START, END = "<!-- AUTOGEN:START -->", "<!-- AUTOGEN:END -->"


def _tree() -> str:
    lines = []
    for path in sorted((ROOT / "ccl").rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        if path.suffix in {".py", ".html"}:
            lines.append(str(path.relative_to(ROOT)))
    return "\n".join(lines)


def _grep(pattern: str, *files: pathlib.Path) -> list[str]:
    out: set[str] = set()
    rx = re.compile(pattern)
    for f in files:
        if f.exists():
            out.update(rx.findall(f.read_text()))
    return sorted(out)


def _all_py() -> list[pathlib.Path]:
    return [p for p in (ROOT / "ccl").rglob("*.py") if "__pycache__" not in p.parts]


def _requirements() -> str:
    req = ROOT / "requirements.txt"
    if not req.exists():
        return "(no requirements.txt)"
    keep = [ln.strip() for ln in req.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    return "\n".join(keep)


def build_snapshot() -> str:
    server = ROOT / "ccl" / "web" / "server.py"
    env_vars = _grep(r'os\.environ\.get\(\s*"([A-Z_]+)"', *_all_py())
    routes = _grep(r'"(/api/[a-z/_-]+)"', server)
    pages = _grep(r'_PAGES\s*=|"(/(?:teacher|student)?)"\s*:', server)  # informational
    test_files = sorted(p.name for p in (ROOT / "tests").glob("test_*.py")) if (ROOT / "tests").exists() else []
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return "\n".join([
        f"_Snapshot generated {now}. Facts below are derived from the code; the "
        f"narrative sections outside the markers are hand-maintained._",
        "",
        f"**Environment variables referenced in code:** {', '.join(env_vars) or '(none)'}",
        "",
        f"**API routes (server.py):** {', '.join(routes) or '(none)'}",
        "",
        f"**Runtime dependencies (requirements.txt):**",
        "```",
        _requirements(),
        "```",
        "",
        f"**Test files ({len(test_files)}):** {', '.join(test_files) or '(none)'}",
        "",
        "**ccl/ source files:**",
        "```",
        _tree(),
        "```",
    ])


def main() -> int:
    text = HANDOFF.read_text()
    if START not in text or END not in text:
        raise SystemExit("HANDOFF.md is missing the AUTOGEN markers.")
    new = build_snapshot()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    updated = pattern.sub(f"{START}\n{new}\n{END}", text)
    if updated != text:
        HANDOFF.write_text(updated)
        print("HANDOFF.md snapshot updated.")
    else:
        print("HANDOFF.md snapshot already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
