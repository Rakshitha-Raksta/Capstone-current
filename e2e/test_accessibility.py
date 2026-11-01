"""
Automated accessibility audit with axe-core (WCAG 2.0/2.1 A and AA rules) on
every ClearClaim screen and the correction dialog. Fails on serious/critical
violations; the full report (all impacts) is saved to e2e/.artifacts/axe-report.json.
"""
import json
import os
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AXE = os.path.join(ROOT, "frontend", "node_modules", "axe-core", "axe.min.js")
ART = os.path.join(ROOT, "e2e", ".artifacts")
REPORT = {}


def _audit(page, name):
    page.add_script_tag(path=AXE)
    res = page.evaluate("""async () => {
        const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } });
        return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
                                        sample: v.nodes.slice(0, 3).map(n => n.target.join(' ')) }));
    }""")
    REPORT[name] = res
    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "axe-report.json"), "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2)
    return [v for v in res if v["impact"] in ("serious", "critical")]


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe-core not installed (npm install -D axe-core in frontend)")
@pytest.mark.parametrize("path", ["/", "/?mode=list", "/space/policy", "/space/history", "/space/lab", "/space/audit", "/one", "/one?view=list", "/about", "/demo", "/claims", "/review", "/audit", "/studio", "/control-room", "/present", "/operations", "/workflow?case=1", "/policies", "/data",
                                  "/experiments", "/settings", "/claims/1"])
def test_no_serious_accessibility_violations(page, stack, path):
    page.goto(stack["web"] + path)
    page.wait_for_load_state("networkidle")
    bad = _audit(page, path)
    assert not bad, json.dumps(bad, indent=1)


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe-core not installed")
def test_correction_dialog_accessible_and_keyboard_operable(page, stack):
    with urllib.request.urlopen(stack["api"] + "/api/v1/cases?status=BLOCKED&page_size=50") as r:
        items = json.load(r)["items"]
    case = next(c for c in items if c["root_blocker_code"] in ("DUPLICATE_BILL_ITEM", "BILL_ARITHMETIC_ERROR",
                                                               "MISSING_CLAIM_DOCUMENT", "LOS_BILLING_MISMATCH"))
    page.goto(f"{stack['web']}/claims/{case['id']}")
    page.wait_for_load_state("networkidle")
    btn = page.get_by_role("button", name="Apply correction…").first
    btn.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    focused_inside = page.evaluate("document.querySelector('[role=dialog]').contains(document.activeElement)")
    assert focused_inside, "focus did not move into the dialog"
    bad = _audit(page, "correction-dialog")
    assert not bad, json.dumps(bad, indent=1)
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
    assert page.evaluate("document.activeElement.textContent").startswith("Apply correction"), "focus not restored"


@pytest.mark.skipif(not os.path.exists(AXE), reason="axe-core not installed")
@pytest.mark.parametrize("path", ["/", "/space/policy", "/one", "/demo", "/studio", "/control-room", "/present", "/claims", "/claims/1", "/audit", "/experiments"])
def test_dark_theme_has_no_serious_accessibility_violations(page, stack, path):
    page.emulate_media(color_scheme="dark")                                # "System" theme follows the OS setting
    page.goto(stack["web"] + path)
    page.wait_for_load_state("networkidle")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), bg                 # the page really is dark
    bad = _audit(page, f"dark:{path}")
    assert not bad, json.dumps(bad, indent=1)
