"""
Agent Execution Studio, driven in a real browser against the real backend. Stage states, order,
timings and reasons on screen are compared with what the backend persisted. One test intercepts
the progress endpoint to prove a failed run is displayed as a failure.
"""
import json
import re
import urllib.request

STAGES = ["ingestion", "data_validation", "discharge_readiness", "billing", "eligibility", "adjudication",
          "historical", "reconciliation", "root_blocker", "review_routing", "claim_generation"]


def api(stack, path):
    with urllib.request.urlopen(stack["api"] + path, timeout=30) as r:
        return json.load(r)


def lookup(page, stack, hadm):
    page.goto(stack["web"] + "/studio")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Encounter ID").fill(str(hadm))
    page.get_by_role("button", name="Look up").click()


def run_and_wait(page, mode_label):
    page.get_by_label(re.compile(mode_label)).check()
    page.get_by_role("button", name="Run workflow").click()
    page.locator("text=/Run #\\d+ finished|Run rejected|Run error/").first.wait_for(timeout=30000)


def test_studio_validates_encounter_ids(page, stack):
    lookup(page, stack, "abc")
    assert "numeric Encounter ID" in page.get_by_role("alert").first.inner_text()
    page.get_by_label("Encounter ID").fill("12345")
    page.get_by_role("button", name="Look up").click()
    msg = page.get_by_role("alert").filter(has_text="No encounter with ID 12345").first
    msg.wait_for(timeout=10000)
    assert "for example" in msg.inner_text()
    assert page.locator("text=0 use an LLM").first.is_visible()
    assert page.locator("text=Synthetic demo data").first.is_visible()


def test_studio_full_run_shows_every_stage_in_order_with_observed_data(page, stack):
    case = api(stack, "/api/v1/cases?status=BLOCKED&root=DUPLICATE_BILL_ITEM&page_size=1")["items"][0]
    lookup(page, stack, case["hadm_id"])
    page.get_by_role("button", name="Run workflow").wait_for()
    run_and_wait(page, "Full execution")
    rid = int(re.search(r"Run #(\d+) finished", page.locator("text=/Run #\\d+ finished/").first.inner_text()).group(1))
    rec = api(stack, f"/api/v1/studio/pipeline-runs/{rid}")
    assert [s["stage"] for s in rec["stages"]] == STAGES
    cards = page.locator("[data-stage]")
    assert [c.get_attribute("data-stage") for c in cards.all()] == STAGES          # drawn in execution order
    expected = {"OK": "done", "WARN": "issue", "BLOCKED": "blocked"}
    for s in rec["stages"]:
        x = s["execution"]
        assert x["status"] == "SUCCEEDED"                                             # full policy executes all
        card = page.locator(f"[data-stage='{s['stage']}']")
        assert card.get_attribute("data-state") == expected[x["stage_status"]], s["stage"]
    billing = page.locator("[data-stage='billing']")
    assert billing.get_attribute("data-state") == "blocked"                          # the duplicate line blocks billing
    billing.click()
    detail = page.locator("#stage-detail")
    assert "Checks implemented" in detail.inner_text() and "Duplicate bill line" in detail.inner_text()
    assert "Deterministic rules" in detail.inner_text()
    page.locator("summary", has_text="Stage output").click()
    assert "lines_total" in detail.inner_text()
    page.locator("summary", has_text="Event log").click()
    assert page.locator("text=run_finished").first.is_visible()
    assert page.locator(".callout", has_text=re.compile("Blocked|Needs review|Preparation complete|Not complete")).count() >= 1
    assert not page.console_errors, page.console_errors


def test_studio_correction_rerun_explains_executed_vs_reused(page, stack):
    cases = api(stack, "/api/v1/cases?status=BLOCKED&root=DUPLICATE_BILL_ITEM&page_size=50")["items"]
    case = next(c for c in cases if c["open_tasks"] == 1)
    lookup(page, stack, case["hadm_id"])
    page.get_by_role("button", name="Run workflow").wait_for()
    run_and_wait(page, "Correct and re-run")
    rid = int(re.search(r"Run #(\d+)", page.locator("text=/Run #\\d+ finished/").first.inner_text()).group(1))
    run = next(r for r in api(stack, f"/api/v1/cases/{case['id']}/runs") if r["id"] == rid)
    assert run["trigger"] == "CORRECTION" and run["changed_domains"] == ["bill"]
    why = page.locator("table.why")
    rows = {r.locator("td").nth(0).inner_text(): r for r in why.locator("tbody tr").all()}
    assert rows["Insurance eligibility"].locator("td").nth(1).inner_text() == "Not re-run"
    assert "reads changed input: bill" in rows["Billing verification"].inner_text()
    summary = page.locator("text=/\\d+ executed · \\d+ reused/").first.inner_text()
    assert summary.startswith(f"{run['stages_executed']} executed · {run['stages_reused']} reused")
    labels = {"ingestion": "Ingestion", "data_validation": "Data validation", "discharge_readiness": "Discharge readiness",
              "billing": "Billing verification", "eligibility": "Insurance eligibility", "adjudication": "Insurance adjudication",
              "historical": "Historical comparison", "reconciliation": "Reconciliation", "root_blocker": "Root-blocker analysis",
              "review_routing": "Review routing", "claim_generation": "Claim preparation"}
    for st in [s["stage"] for s in run["stages"] if s["status"] == "REUSED"]:   # every reused stage explains the hash match
        assert rows[labels[st]].locator("td").nth(1).inner_text() == "Reused"
        assert "input hash identical" in rows[labels[st]].inner_text()


def test_studio_teaching_mode_steps_through_recorded_events(page, stack):
    case = api(stack, "/api/v1/cases?status=NEEDS_REVIEW&page_size=1")["items"][0]
    lookup(page, stack, case["hadm_id"])
    page.get_by_role("button", name="Run workflow").wait_for()
    run_and_wait(page, "Full execution")
    page.get_by_label("Teaching mode").check()
    assert page.locator("text=Event 1 of").first.is_visible()
    assert "started" in page.locator(".teach .plain").inner_text()
    assert page.locator("[data-state='pending']").count() == len(STAGES)   # nothing has run at event 1
    page.get_by_role("button", name="Next step →").click()
    page.get_by_role("button", name="Next step →").click()
    assert page.locator("[data-stage='ingestion']").get_attribute("data-state") != "pending"
    assert "not paused" in page.locator(".teach").inner_text()


def test_studio_shows_a_failed_run_as_failure(page, stack):
    case = api(stack, "/api/v1/cases?page_size=1")["items"][0]
    failed = {"status": "finished", "case_id": case["id"], "hadm_id": case["hadm_id"], "mode": "full", "run_id": None,
              "run_status": "FAILED", "case_status": "ERROR", "error": None, "http_status": None, "next_after": 4,
              "events": [{"seq": 0, "type": "run_started", "t_ms": 0.0, "run_id": 0, "trigger": "MANUAL_RERUN",
                          "execution_mode": "sequential", "rerun_policy": "full", "changed_domains": [], "planned_stages": STAGES,
                          "force": True, "first_run": False},
                         {"seq": 1, "type": "stage_started", "t_ms": 0.1, "stage": "ingestion", "reason": "full rerun policy: every stage executes"},
                         {"seq": 2, "type": "stage_finished", "t_ms": 0.5, "stage": "ingestion", "status": "FAILED",
                          "duration_ms": 0.4, "attempts": 3, "error": "RuntimeError: simulated (test)"},
                         {"seq": 3, "type": "run_finished", "t_ms": 0.9, "run_id": 0, "status": "FAILED", "outcome_status": "ERROR",
                          "stages_executed": 0, "stages_reused": 0, "wall_ms": 0.9, "error": "failed stages: ['ingestion']"}]}
    page.route("**/api/v1/studio/runs/*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(failed)))
    lookup(page, stack, case["hadm_id"])
    page.get_by_role("button", name="Run workflow").wait_for()
    page.get_by_role("button", name="Run workflow").click()
    alert = page.get_by_role("alert").filter(has_text="This run FAILED").first
    alert.wait_for(timeout=15000)
    assert page.locator("[data-stage='ingestion']").get_attribute("data-state") == "failed"
    page.locator("text=The last run failed").first.wait_for(timeout=15000)


def test_studio_graph_and_list_views_show_the_same_observed_states(page, stack):
    case = api(stack, "/api/v1/cases?status=NEEDS_REVIEW&page_size=1")["items"][0]
    lookup(page, stack, case["hadm_id"])
    page.get_by_role("button", name="Run workflow").wait_for()
    run_and_wait(page, "Full execution")
    graph = {n.get_attribute("data-stage"): n.get_attribute("data-state") for n in page.locator(".stage-node").all()}
    assert list(graph) == STAGES and "pending" not in graph.values()
    node = page.get_by_role("button", name=re.compile("^Billing verification:"))
    node.focus()
    page.keyboard.press("Enter")                                           # keyboard-operable nodes
    assert "Checks implemented" in page.locator("#stage-detail").inner_text()
    page.get_by_role("button", name="List", exact=True).click()
    cards = {c.get_attribute("data-stage"): c.get_attribute("data-state") for c in page.locator(".stage-card").all()}
    assert cards == graph
