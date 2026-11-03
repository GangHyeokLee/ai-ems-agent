"""Static checks for the Artifact console (ui/console) and its /console routes.

app.css, models.js and app.js are byte-identical slices of the original Artifact HTML; their SHA-256
are pinned here so the check also runs where the untracked original (local-reference/) is absent.
Behaviour (parity with the original, EMS API grid separation) is covered by test_console_browser.py.
"""
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "ui" / "console"
ORIGINAL = ROOT / "local-reference" / "계통 관제 의사결정지원 테스트베드 (데모).html"
# SHA-256 of the original Artifact slices: CSS (lines 11-795), MODELS (line 1033), script (lines 1034-3121),
# body markup (lines 799-1031), each with a trailing newline.
ORIGINAL_SLICES = {
    "app.css": "aedadcf1aaa7426031e233053ce0cdae18f764128e0592c2a0006c720ceb664b",
    "models.js": "d609f8cea79a363ac37066d836fa2351362f6bd0b001811678a40759ecea5fda",
    "app.js": "943e325e0cea568b3af6c97d54506e9566265f6a9df762e0eaa6ed61560bd21b",
}
ORIGINAL_MARKUP_SHA256 = "9168ee3e6e9671d709042991e04fa6127e5754f63171c258f3af4940f379c38c"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_html() -> str:
    return (CONSOLE / "index.html").read_text(encoding="utf-8")


def body_markup(html: str) -> str:
    start = html.index("<body data-mode=\"pub\">\n") + len("<body data-mode=\"pub\">\n")
    return html[start:html.index('<script src="models.js">')]


@pytest.mark.parametrize("name", sorted(ORIGINAL_SLICES))
def test_artifact_sources_are_byte_identical(name):
    assert sha256((CONSOLE / name).read_bytes()) == ORIGINAL_SLICES[name]


def test_artifact_markup_is_unchanged():
    assert sha256(body_markup(index_html()).encode("utf-8")) == ORIGINAL_MARKUP_SHA256


@pytest.mark.skipif(not ORIGINAL.is_file(), reason="original Artifact HTML not present in local-reference/")
def test_slices_match_the_original_file():
    lines = ORIGINAL.read_bytes().split(b"\n")
    assert sha256(lines[1032] + b"\n") == ORIGINAL_SLICES["models.js"]
    assert sha256(b"\n".join(lines[10:795]) + b"\n") == ORIGINAL_SLICES["app.css"]
    assert sha256(b"\n".join(lines[1033:3121]) + b"\n") == ORIGINAL_SLICES["app.js"]
    assert sha256(b"\n".join(lines[798:1031]) + b"\n") == ORIGINAL_MARKUP_SHA256


def test_classic_scripts_keep_the_original_order():
    html = index_html()
    # One global scope as in the single inline script: ES modules would reject app.js's 20 duplicate
    # function declarations and change hoisting.
    assert re.findall(r"<script[^>]*>", html) == ['<script src="models.js">', '<script src="app.js">', '<script src="api-map.js">',
                                                  '<script src="api-analysis.js">', '<script src="api-bridge.js">']
    assert re.findall(r'<link rel="stylesheet" href="([^"]+)">', html) == ["app.css", "api-bridge.css"]


def test_google_fonts_never_block_rendering():
    fonts = re.findall(r"<link[^>]*fonts\.googleapis\.com/css2[^>]*>", index_html())
    assert len(fonts) == 1 and 'media="print"' in fonts[0] and "onload=\"this.media='all'\"" in fonts[0]
    assert '"Malgun Gothic","Noto Sans KR",system-ui' in (CONSOLE / "app.css").read_text(encoding="utf-8")


def test_bridge_is_isolated_and_writes_text_only():
    source = (CONSOLE / "api-bridge.js").read_text(encoding="utf-8")
    code = source[source.index("*/") + 2:].strip()
    # A single IIFE: no global lexical name can collide with app.js.
    assert code.startswith("(() => {\n  'use strict';") and code.endswith("})();")
    # API values reach the page as text nodes only.
    assert not re.search(r"\.(innerHTML|outerHTML)\b|insertAdjacentHTML\(|document\.write", code)
    assert "'/api/grids'" in code and "MODELS" not in code
    # The Artifact DC engine and demo state are never read for an EMS API grid (comments may name them).
    statements = re.sub(r"/\*.*?\*/|//[^\n]*", "", code, flags=re.S)
    assert not re.search(r"\b(dcpf|n1fast|n1full|redispatch|twin|BASE|lastScen)\b", statements)
    # Analysis requests are built only by the per-grid adapters of api-analysis.js.
    assert not re.search(r"security-analysis|sensitivity-analysis", code)


ANALYSIS_ENDPOINTS = {"/api/security-analysis", "/api/sensitivity-analysis",
                      "/api/grids/mn-osm-35k/security-analysis", "/api/grids/mn-osm-35k/sensitivity-analysis"}


def test_api_analysis_module_is_isolated():
    source = (CONSOLE / "api-analysis.js").read_text(encoding="utf-8")
    code = source[source.index("*/") + 2:].strip()
    # One global name only, absent from app.js; text nodes only; requests are sent by the bridge, never here.
    assert code.startswith("const KemsApiAnalysis = (() => {\n  'use strict';") and code.endswith("})();")
    assert "KemsApiAnalysis" not in (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\.(innerHTML|outerHTML)\b|insertAdjacentHTML\(|document\.write|\bfetch\(", code)
    statements = re.sub(r"/\*.*?\*/|//[^\n]*", "", code, flags=re.S)
    assert not re.search(r"\b(dcpf|n1fast|n1full|redispatch|twin|BASE|lastScen|MODELS)\b|M\.buses", statements)
    # Exactly the two backends that run the analyses; the map-only Korean OSM grid has no adapter.
    assert set(re.findall(r"'(/api/[\w/.-]+)'", code)) == ANALYSIS_ENDPOINTS
    assert "kr-osm-transmission" not in code


def test_api_map_renderer_is_isolated():
    source = (CONSOLE / "api-map.js").read_text(encoding="utf-8")
    code = source[source.index("*/") + 2:].strip()
    # One global name only, absent from app.js; no HTML injection; no power-flow computation.
    assert code.startswith("const KemsApiMap = (() => {\n  'use strict';") and code.endswith("})();")
    assert "KemsApiMap" not in (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\.(innerHTML|outerHTML)\b|insertAdjacentHTML\(|document\.write|\bfetch\(", code)
    assert not re.search(r"\b(dcpf|M\.buses|MODELS|twin)\b", code)


@pytest.fixture(scope="module")
def client():
    import web_app
    return TestClient(web_app.app)


def test_console_routes(client):
    redirect = client.get("/console", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/console/"
    page = client.get("/console/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.content == (CONSOLE / "index.html").read_bytes()
    import web_app
    types = {".css": "text/css", ".js": "text/javascript"}
    for name in sorted(web_app.CONSOLE_ASSETS):
        response = client.get(f"/console/{name}")
        assert response.status_code == 200 and response.content == (CONSOLE / name).read_bytes(), name
        assert response.headers["content-type"].startswith(types[Path(name).suffix]), name
    referenced = set(re.findall(r'(?:src|href)="([\w.-]+\.(?:js|css))"', index_html()))
    assert referenced == set(web_app.CONSOLE_ASSETS)


@pytest.mark.parametrize("path", ["/console/index.html", "/console/missing.js", "/console/..%2Fweb_app.py",
                                  "/console/%2e%2e%2fweb_app.py", "/console/.env", "/console/sub/app.js"])
def test_console_serves_only_listed_assets(client, path):
    assert client.get(path).status_code == 404


def test_existing_ui_is_still_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200 and response.content == (ROOT / "ui" / "index.html").read_bytes()
