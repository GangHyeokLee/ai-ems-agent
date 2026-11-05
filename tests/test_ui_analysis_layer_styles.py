"""Static checks for how the analysis layer toggle interacts with PF line colors in ui/index.html.

applyPfDisplayMode() colors lines through inline ``style.stroke``. Any ``!important``
stylesheet stroke that still matches while the analysis layer is hidden overrides that
inline color, leaving outage/violation lines stuck (e.g. grey) instead of returning to
the PF loading or base voltage color.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "ui" / "index.html"


def css_rules():
    html = INDEX_HTML.read_text(encoding="utf-8")
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        (selector.strip(), body.strip())
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]


def selectors_for(class_name):
    return [
        (sel.strip(), body)
        for selector, body in css_rules()
        for sel in selector.split(",")
        if re.search(rf"\.network-line\.{class_name}\b", sel)
    ]


def test_hidden_analysis_layer_does_not_force_line_stroke():
    for selector, body in css_rules():
        if "analysis-hidden" in selector and ":not(.analysis-hidden)" not in selector:
            assert "stroke" not in body, (
                f"'{selector}' overrides the inline PF/base stroke while analysis is hidden"
            )


def test_outage_and_violation_highlights_apply_only_when_analysis_visible():
    for class_name in ("outage-line", "violation-line"):
        rules = selectors_for(class_name)
        assert rules, f"missing highlight rule for .{class_name}"
        for selector, body in rules:
            if "stroke" in body:
                assert ":where(#map:not(.analysis-hidden))" in selector, (
                    f"'{selector}' would keep .{class_name} highlighted after the analysis layer is hidden"
                )


def test_selected_and_hover_line_rules_still_follow_highlight_rules():
    """Keep equal specificity so hover/selected styling still wins by source order."""
    rules = css_rules()
    order = {sel.strip(): idx for idx, (selector, _) in enumerate(rules) for sel in selector.split(",")}
    outage = next(sel for sel, _ in selectors_for("outage-line"))
    assert order[".network-line.selected-line"] > order[outage]


def test_pf_display_mode_colors_lines_inline():
    html = INDEX_HTML.read_text(encoding="utf-8")
    body = html.split("function applyPfDisplayMode()", 1)[1].split("\n    function ", 1)[0]
    assert "element.style.stroke" in body
    assert "lineLoadingColor(" in body
