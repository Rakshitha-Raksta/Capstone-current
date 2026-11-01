"""
3D command center ("/" and "/space/<station>"): one persistent WebGL world, eight stations, every
visible state compared with backend records. Runs on the throwaway e2e database (conftest.stack).
"""
import re

from test_workspace_ui import FINAL, STAGES, api, pick_case


def open_cc(page, stack, path="/", query=""):
    page.goto(f"{stack['web']}{path}{query}")
    page.locator(".space").wait_for()
    page.locator(".world[data-ready='1'], .list-deck").first.wait_for(timeout=60000)
    page.locator(".space-panel .panel-body").wait_for()


def world(page, expr):
    return page.evaluate(f"(() => {{ const w = window.__ccWorld; return {expr}; }})()")


def labels(page):
    return {n.get_attribute("data-stage"): (n.get_attribute("data-state"), n.get_attribute("data-mark"))
            for n in page.locator(".w-stage").all()}


def test_opens_on_the_top_blocker_and_the_world_shows_recorded_states(page, stack):
    open_cc(page, stack)
    page.locator(".card.blocker h2").wait_for(timeout=30000)
    hadm = re.search(r"encounter=(\d+)", page.url).group(1)
    case = api(stack, f"/api/v1/studio/encounters/{hadm}")["case"]
    d = api(stack, f"/api/v1/cases/{case['id']}")
    top = api(stack, "/api/v1/cases?status=BLOCKED,NEEDS_REVIEW&sort=priority&order=desc&page_size=12")["items"]
    assert int(hadm) == next(c for c in top if c["status"] == "BLOCKED")["hadm_id"]
    root = d["root_blocker"]["roots"][0]
    assert page.locator(".card.blocker h2").inner_text() == root["title"]
    # one 3D world with 11 stage nodes built from the real DAG
    assert world(page, "Object.keys(w.nodes).length") == 11
    edges = world(page, "w.edges.map(e => e.from + '>' + e.to).sort()")
    graph = api(stack, "/api/v1/workflow/graph")
    expected = sorted(f"{d_}>{s['id']}" for s in graph["stages"] for d_ in s["deps"])
    assert edges == expected
    lab = labels(page)
    assert len(lab) == 11
    for s in STAGES:
        assert lab[s][0] == FINAL[d["stages"][s]["stage_status"]], s
    assert lab[root["stage"]][1] == "root"
    assert not page.console_errors, page.console_errors


def test_stations_are_camera_flights_in_the_same_world(page, stack):
    open_cc(page, stack)
    page.locator(".card.blocker").wait_for(timeout=30000)
    w0 = page.evaluate("window.__ccWorld.renderer.domElement === document.querySelector('.world-canvas')")
    assert w0
    for key, view, title in [("4", "policy", "Policy intelligence"), ("5", "history", "Historical-case intelligence"),
                             ("6", "lab", "Execution laboratory"), ("8", "audit", "Audit history & research results"),
                             ("3", "departments", "Department workflow"), ("1", "overview", "Operations overview")]:
        page.locator("body").press(key)
        page.get_by_role("heading", name=title, level=1).wait_for()
        assert world(page, "w.currentView") == view
        assert (view == "overview" and not page.url.split("?")[0].endswith("/space/overview")) or view in page.url or view == "overview"
    # still the very same canvas: navigation never rebuilt the 3D world
    assert page.evaluate("window.__ccWorld.renderer.domElement === document.querySelector('.world-canvas')")
    assert page.locator(".world-canvas").count() == 1
    assert not page.console_errors, page.console_errors


def test_policy_wall_and_explorer_match_recorded_rule_results(page, stack):
    open_cc(page, stack, "/space/policy")
    page.locator(".space .rules li").first.wait_for(timeout=30000)
    hadm = re.search(r"encounter=(\d+)", page.url).group(1)
    cid = api(stack, f"/api/v1/studio/encounters/{hadm}")["case"]["id"]
    body = api(stack, f"/api/v1/intel/rules?case_id={cid}")
    assert world(page, "w.ruleTiles.length") == 59
    assert world(page, "w.ruleTiles.map(t => t.id)") == [r["rule_id"] for r in body["rules"]]
    # pick a rule with a recorded result and check the quote and outcome shown
    r = next(x for x in body["rules"] if x["outcome"]["status"] in ("PASS", "FAIL", "WARNING", "PARTIAL_DEDUCTION"))
    page.locator(".space .rules button", has_text=r["rule_id"]).click()
    card = page.locator(".rule-card")
    card.wait_for()
    assert r["source_text"][:40] in card.inner_text()
    assert f"page {r['source_page']}" in card.inner_text()
    assert {"PASS": "Pass", "FAIL": "Fail", "WARNING": "Warning", "PARTIAL_DEDUCTION": "Partial deduction"}[r["outcome"]["status"]] in card.locator(".outcome").inner_text()
    assert "not insurer adjudication" in page.locator(".space-panel").inner_text()


def test_history_shows_the_stage_method_and_values(page, stack):
    open_cc(page, stack, "/space/history")
    page.get_by_role("heading", name="Historical-case intelligence", level=1).wait_for()
    page.get_by_text("Limitations").wait_for(timeout=30000)
    hadm = re.search(r"encounter=(\d+)", page.url).group(1)
    cid = api(stack, f"/api/v1/studio/encounters/{hadm}")["case"]["id"]
    h = api(stack, f"/api/v1/intel/cases/{cid}/historical?sample=400")
    text = page.locator(".space-panel").inner_text()
    assert "SYNTHETIC" in text and "modified z-score" in text
    if h["status"] == "COMPARED":
        assert world(page, "w.peerPos.length") == len(h["peers"])
        assert f"{h['n_peers']}" in page.locator(".zline").inner_text()


def test_correction_needs_confirmation_then_reprocesses_and_verifies(page, stack):
    # codes no other e2e test corrects, so test order cannot exhaust a shared case
    case = pick_case(stack, "BILLED_PROCEDURE_NOT_DOCUMENTED") or pick_case(stack, "LOS_BILLING_MISMATCH")
    assert case
    before = api(stack, f"/api/v1/cases/{case['id']}")
    runs_before = len(api(stack, f"/api/v1/cases/{case['id']}/runs"))
    open_cc(page, stack, "/space/correct", f"?encounter={case['hadm_id']}")
    page.get_by_text("Proposed change").wait_for(timeout=30000)
    # preview marks on the 3D deck equal the orchestrator's plan, and previewing runs nothing
    root = before["root_blocker"]["roots"][0]
    op = {"BILLED_PROCEDURE_NOT_DOCUMENTED": "delete_bill_line", "LOS_BILLING_MISMATCH": "update_bill_line"}[root["code"]]
    pv = api(stack, f"/api/v1/workspace/correction-preview?op={op}")
    page.locator(".plan li").first.wait_for()
    lab = labels(page)
    assert {s for s, (_, m) in lab.items() if m == "rerun"} == set(pv["planned"])
    # no confirmation tick: refused in the form, nothing executed
    page.get_by_label("Reason (stored in the audit trail)").fill("e2e command center correction")
    page.get_by_role("button", name="Confirm and apply").click()
    assert "confirmation" in page.get_by_role("alert").first.inner_text().lower()
    assert len(api(stack, f"/api/v1/cases/{case['id']}/runs")) == runs_before
    page.get_by_label(re.compile("I confirm this changes")).check()
    page.get_by_role("button", name="Confirm and apply").click()
    page.get_by_text("Verified from the recorded run").wait_for(timeout=60000)
    after = api(stack, f"/api/v1/cases/{case['id']}")
    assert after["case"]["version"] == before["case"]["version"] + 1
    run = api(stack, f"/api/v1/cases/{case['id']}/runs")[0]
    assert run["trigger"] == "CORRECTION" and run["stages_executed"] < 11
    panel = page.locator(".space-panel").inner_text()
    executed = [r["stage"] for r in run["stages"] if r["status"] == "SUCCEEDED"]
    reused = [r["stage"] for r in run["stages"] if r["status"] == "REUSED"]
    kv = page.locator(".space-panel .kv").first.inner_text()
    from test_workspace_ui import STAGES as _S  # noqa: F401
    label = lambda s: {"ingestion": "Ingestion", "data_validation": "Data validation", "discharge_readiness": "Discharge readiness",  # noqa: E731
                       "billing": "Billing verification", "eligibility": "Insurance eligibility", "adjudication": "Insurance adjudication",
                       "historical": "Historical comparison", "reconciliation": "Reconciliation", "root_blocker": "Root-blocker analysis",
                       "review_routing": "Review routing", "claim_generation": "Claim preparation"}[s]
    for s in executed:
        assert label(s) in kv
    for s in reused:
        assert label(s) in kv
    assert "intact" in panel
    assert api(stack, "/api/v1/audit-events/verify")["ok"]
    assert "REPLAY of the recorded correction run" in page.locator(".space-timeline").inner_text() or \
        "final recorded state" in page.locator(".space-timeline").inner_text()
    assert not page.console_errors, page.console_errors


def test_lab_measures_both_modes_on_isolated_copies(page, stack):
    open_cc(page, stack, "/space/lab")
    page.get_by_role("button", name="Measure sequential vs parallel").wait_for(timeout=30000)
    hadm = re.search(r"encounter=(\d+)", page.url).group(1)
    cid = api(stack, f"/api/v1/studio/encounters/{hadm}")["case"]["id"]
    runs_before = len(api(stack, f"/api/v1/cases/{cid}/runs"))
    page.get_by_label("Repetitions").select_option("3")
    page.get_by_role("button", name="Measure sequential vs parallel").click()
    page.get_by_text("Median of measured runs").wait_for(timeout=120000)
    text = page.locator(".space-panel").inner_text()
    assert re.search(r"Outputs identical\s*yes", text) and re.search(r"Same input snapshot\s*yes", text)
    rec_id = int(re.search(r"Record #(\d+)", text, re.I).group(1))
    rec = api(stack, f"/api/v1/control-room/history/{rec_id}")
    assert rec["summary"]["outputs_match"] and rec["summary"]["same_input_snapshot"]
    # two lanes of 3D Gantt bars built from the recorded events
    assert world(page, "w.labLanes") == 2
    n_bars = sum(1 for r in rec["summary"]["runs"] if not r["warmup"])
    assert world(page, "w.bars.length") > 0 and n_bars == 6
    # comparisons ran on isolated copies: the live case gained no runs
    assert len(api(stack, f"/api/v1/cases/{cid}/runs")) == runs_before
    assert not page.console_errors, page.console_errors


def test_webgl_fallback_and_list_view(page, stack):
    open_cc(page, stack, "/", "?webgl=off")
    page.locator(".list-deck").wait_for(timeout=30000)
    assert "WebGL is not available" in page.locator(".list-deck").inner_text()
    assert page.locator(".ld-stage").count() == 11
    assert page.locator(".world-canvas").count() == 0
    page.locator(".ld-stage").nth(3).click()
    page.get_by_role("heading", name="Encounter & blocker investigation", level=1).wait_for()


def test_small_viewport_has_no_horizontal_scroll(page, stack):
    page.set_viewport_size({"width": 390, "height": 844})
    open_cc(page, stack)
    page.locator(".space-panel .panel-body > *").first.wait_for(timeout=30000)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.evaluate("getComputedStyle(document.querySelector('.space-panel')).top") != "68px"
    assert not page.console_errors, page.console_errors
