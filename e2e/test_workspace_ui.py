"""
ClearClaim One workspace ("/"): the investigate → understand → correct → reprocess → verify journey,
in a real browser against the real backend. Everything on screen is compared with backend records.
"""
import json
import re
import urllib.request

STAGES = ["ingestion", "data_validation", "discharge_readiness", "billing", "eligibility", "adjudication",
          "historical", "reconciliation", "root_blocker", "review_routing", "claim_generation"]
FINAL = {"OK": "completed", "WARN": "issue", "BLOCKED": "blocked"}


def api(stack, path):
    with urllib.request.urlopen(stack["api"] + path, timeout=30) as r:
        return json.load(r)


def descendants(graph, sid):
    out, todo = set(), [sid]
    while todo:
        s = todo.pop()
        for st in graph["stages"]:
            if s in st["deps"] and st["id"] not in out:
                out.add(st["id"]); todo.append(st["id"])
    return out


def marks(page):
    return {n.get_attribute("data-stage"): n.get_attribute("data-mark") for n in page.locator(".wg-label").all()}


def open_ws(page, stack, query=""):
    page.goto(f"{stack['web']}/one{query}")
    page.locator(".one").wait_for()
    page.locator(".wg[data-ready='1'], .one-listview").first.wait_for(timeout=30000)


def pick_case(stack, code):
    items = api(stack, f"/api/v1/cases?status=BLOCKED&root={code}&page_size=50")["items"]
    return next((c for c in items if c["open_tasks"] == 1), None)


def test_workspace_opens_on_the_most_important_blocker_with_the_real_graph(page, stack):
    open_ws(page, stack)
    page.locator(".blocker-title").wait_for(timeout=30000)
    hadm = re.search(r"encounter=(\d+)", page.url).group(1)
    enc = api(stack, f"/api/v1/studio/encounters/{hadm}")
    d = api(stack, f"/api/v1/cases/{enc['case']['id']}")
    root = d["root_blocker"]["roots"][0]
    assert page.locator(".blocker-title").inner_text() == root["title"]
    assert page.locator(".wg-label").count() == 11
    m = marks(page)
    graph = api(stack, "/api/v1/workflow/graph")
    assert m[root["stage"]] == "root"
    cons = {c["stage"] for c in d["root_blocker"]["consequences"] if c["caused_by"] == root["code"]}
    for s in descendants(graph, root["stage"]):
        assert m[s] in ("downstream", "consequence"), (s, m[s])
    assert {s for s, v in m.items() if v == "consequence"} <= cons | {root["stage"]}
    # recorded states equal the case's current stage executions
    for s in STAGES:
        st = page.locator(f".wg-label[data-stage='{s}']").get_attribute("data-state")
        assert st == FINAL[d["stages"][s]["stage_status"]], s
    assert page.get_by_text("Internal preparation status, not an insurer decision.").first.is_visible()
    assert not page.console_errors, page.console_errors


def test_preview_marks_equal_the_dependency_model_and_change_nothing(page, stack):
    case = pick_case(stack, "READINESS_ITEM_INCOMPLETE") or pick_case(stack, "BILL_ARITHMETIC_ERROR")
    assert case, "no suitable blocked case in the e2e dataset"
    runs_before = len(api(stack, f"/api/v1/cases/{case['id']}/runs"))
    version = api(stack, f"/api/v1/cases/{case['id']}")["case"]["version"]
    open_ws(page, stack, f"?encounter={case['hadm_id']}")
    page.get_by_role("button", name="Preview correction").click()
    page.get_by_role("heading", name=case["root_blocker_title"]).wait_for()
    detail = api(stack, f"/api/v1/cases/{case['id']}")
    op = {"READINESS_ITEM_INCOMPLETE": "complete_checklist_item", "BILL_ARITHMETIC_ERROR": "update_bill_line"}[case["root_blocker_code"]]
    pv = api(stack, f"/api/v1/workspace/correction-preview?op={op}")
    m = marks(page)
    assert {s for s, v in m.items() if v == "rerun"} == set(pv["planned"])
    assert {s for s, v in m.items() if v == "kept"} == set(pv["kept"])
    # previewing executes nothing
    assert len(api(stack, f"/api/v1/cases/{case['id']}/runs")) == runs_before
    assert detail["case"]["version"] == version
    # applying without a reason is refused in the form, and still nothing runs
    page.get_by_role("button", name="Apply correction and re-run affected stages").click()
    assert "reason" in page.get_by_role("alert").first.inner_text().lower()
    assert len(api(stack, f"/api/v1/cases/{case['id']}/runs")) == runs_before


def test_apply_reprocesses_through_the_backend_and_ui_matches_the_record(page, stack):
    case = pick_case(stack, "DUPLICATE_BILL_ITEM") or pick_case(stack, "MISSING_CLAIM_DOCUMENT")
    assert case
    before = api(stack, f"/api/v1/cases/{case['id']}")
    open_ws(page, stack, f"?encounter={case['hadm_id']}")
    page.get_by_role("button", name="Preview correction").click()
    page.get_by_label("Reason (recorded in the audit trail)").fill("e2e workspace correction")
    page.get_by_role("button", name="Apply correction and re-run affected stages").click()
    page.get_by_role("heading", name="Correction applied").wait_for(timeout=60000)
    after = api(stack, f"/api/v1/cases/{case['id']}")
    assert after["case"]["version"] == before["case"]["version"] + 1
    run = api(stack, f"/api/v1/cases/{case['id']}/runs")[0]
    assert run["trigger"] == "CORRECTION" and run["rerun_policy"] == "targeted"
    # the animation is labelled as a replay of recorded events
    assert "REPLAY of recorded events" in page.locator(".one-panel .replay").inner_text()
    # final recorded state on screen == persisted stage rows of that run
    page.get_by_role("button", name="Show final state").click()
    rows = {r["stage"]: r for r in run["stages"]}
    for s in STAGES:
        ui = page.locator(f".wg-label[data-stage='{s}']").get_attribute("data-state")
        r = rows.get(s)
        exp = "notrun" if r is None else "reused" if r["status"] == "REUSED" else FINAL[r["stage_status"]]
        assert ui == exp, (s, ui, exp)
    assert run["stages_executed"] < 11
    assert f"{len([r for r in run['stages'] if r['status'] == 'SUCCEEDED'])} executed" in page.locator(".one-say").inner_text()
    cells = page.locator(".one-ba tr", has_text="Status").first.locator("td").all_inner_texts()
    human = lambda s: s.replace("_", " ").lower()                                    # noqa: E731
    assert [c.strip().lower() for c in cells] == [human(before["case"]["status"]), human(after["case"]["status"])]
    # audit evidence in place, chain intact
    page.get_by_role("button", name="Show audit records").click()
    page.locator(".one-audit li").first.wait_for()
    assert "correction applied" in page.locator(".one-audit").inner_text().lower()
    assert "intact" in page.locator(".one-panel").inner_text()
    assert api(stack, "/api/v1/audit-events/verify")["ok"]
    assert not page.console_errors, page.console_errors


def test_list_view_commands_and_keyboard(page, stack):
    open_ws(page, stack, "?view=list")
    page.locator(".one-lrow").first.wait_for(timeout=30000)
    assert page.locator(".one-lrow").count() == 11
    assert page.locator(".one-wave.par .one-lrow").count() == 2          # billing ∥ eligibility, from the real waves
    cmd = page.get_by_label("Command")
    cmd.fill("stage billing"); cmd.press("Enter")
    page.get_by_role("heading", name="Billing verification").wait_for()
    assert "not an ai agent" in page.locator(".one-stagecard").inner_text().lower()
    cmd.press("Escape")                     # first Escape leaves the command field
    page.keyboard.press("Escape")           # second Escape closes the stage details
    page.locator(".one-stagecard").wait_for(state="detached")
    cmd.fill("help"); cmd.press("Enter")
    assert "no AI" in page.locator(".one-msg").inner_text()
    cmd.fill("make it faster"); cmd.press("Enter")
    assert "Not a supported command" in page.locator(".one-msg").inner_text()
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "one-cmd"
    assert not page.console_errors, page.console_errors
