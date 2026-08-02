"""Mechanical enforcement of 'no remote frontend dependencies' — the
panel redesign explicitly forbids CDN scripts, remote fonts, or any
external asset the browser would need network access to load. Same
scope-guard spirit as tests/test_scope_guard.py, applied to the panel
frontend instead of the attacker tooling."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_FRONTEND_FILES = [
    *(REPO_ROOT / "panel" / "templates").glob("*.html"),
    *(REPO_ROOT / "panel" / "static").glob("*.js"),
    *(REPO_ROOT / "panel" / "static").glob("*.css"),
]

# Any of these appearing means something is being fetched from off
# this machine — a CDN script, a remote font, a tracking pixel, etc.
FORBIDDEN_PATTERNS = [
    r'src\s*=\s*["\']https?://',
    r'href\s*=\s*["\']https?://.*\.(css|js)["\']',
    r"@import\s+url\(https?://",
    r"fonts\.googleapis\.com",
    r"fonts\.gstatic\.com",
    r"cdn\.jsdelivr\.net",
    r"unpkg\.com",
    r"cdnjs\.cloudflare\.com",
]

# Plain-text https:// links (SECURITY.md, GitHub, the local-services
# "open" buttons) are fine — those are for a human to click, not
# resources the page itself loads. Only src=/href=.css|.js/@import/
# known-CDN patterns above are checked.


def test_panel_frontend_files_exist():
    assert len(PANEL_FRONTEND_FILES) >= 3, "expected index.html + at least app.js and styles.css"


def test_no_forbidden_remote_dependency_patterns():
    violations = []
    for path in PANEL_FRONTEND_FILES:
        text = path.read_text()
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append((path.name, pattern))
    assert not violations, f"remote dependency patterns found: {violations}"


def test_no_script_type_module_points_at_a_remote_url():
    for path in PANEL_FRONTEND_FILES:
        if path.suffix != ".html":
            continue
        for match in re.finditer(r'<script[^>]*src\s*=\s*"([^"]+)"', path.read_text()):
            src = match.group(1)
            assert src.startswith(("/", "{{")), f"{path.name}: script src looks remote: {src}"
