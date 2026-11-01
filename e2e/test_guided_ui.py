"""
Redesigned, guided UI: home page, guided demo, plain-language audit trail and the
full-vs-targeted experiment comparison. Driven in a real browser against the real backend;
the two forced-failure tests intercept only the correction request, to prove that a rejected
correction or a failed re-run is shown clearly and never reported as success.
"""
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def api(stack, path):
    with urllib.request.urlopen(stack["api"] + path, timeout=30) as r:
        return json.load(r)


def go(page, stack, path):
    page.goto(stack["web"] + path)
    page.wait_for_load_state("networkidle")


def test_home_explains_purpose_and_matches_backend_counts(page, stack):
    ov = api(stack, "/api/v1/overview")
    go(page, stack, "/about")          # the former home page; "/" is now the ClearClaim One workspace
    page.get_by_role("heading", name=re.compile("Find what is blocking a claim")).wait_for()
    assert page.get_by_role("link", name=re.compile("Try guided demo")).is_visible()
    assert page.locator("text=not insurer decisions").first.is_visible()
    for label, value in [("Cases", ov["total_cases"]), ("Blocked", ov["status_counts"].get("BLOCKED", 0)),
                         ("Needs review", ov["status_counts"].get("NEEDS_REVIEW", 0)),
                         ("Preparation complete", ov["status_counts"].get("PREPARATION_COMPLETE", 0))]:
        tile = page.locator(".stat", has=page.locator(".label", has_text=re.compile(f"^{label}$"))).first
        assert tile.locator(".value").inner_text().replace(",", "") == str(value), label
    plan = api(stack, "/api/v1/workflow/graph")["rerun_plans"]["bill"]
    assert page.locator("ol.stage-strip li.rechk").count() == len(plan)
    assert not page.console_errors, page.console_errors


def _start_demo(page, stack):
    go(page, stack, "/demo")
    page.get_by_role("heading", name="Meet the case").wait_for(timeout=30000)
    m = re.search(r"case=(\d+)", page.url)
    assert m, page.url
    return int(m.group(1))


def _to_apply_step(page):
    page.get_by_role("button", name=re.compile("Next: what would a fix re-run")).click()
    page.get_by_role("button", name="Next: apply the correction").click()


def test_guided_demo_completes_its_flow_with_live_data(page, stack):
    cid = _start_demo(page, stack)
    before = api(stack, f"/api/v1/cases/{cid}")
    assert before["case"]["status"] == "BLOCKED" and len(before["root_blocker"]["roots"]) == 1
    root = before["root_blocker"]["roots"][0]
    assert page.locator(".blocker-title").first.inner_text() == root["title"]
    page.get_by_role("button", name=re.compile("Next: what would a fix re-run")).click()
    assert page.locator("ol[aria-label='Planned re-run'] li.rechk").count() == len(root["rerun_stages"])
    page.get_by_role("button", name="Next: apply the correction").click()
    page.get_by_role("button", name="Apply correction and re-run").click()
    page.get_by_role("button", name="Next: before and after").wait_for(timeout=30000)
    run = api(stack, f"/api/v1/cases/{cid}/runs")[0]
    assert run["trigger"] == "CORRECTION" and run["status"] == "SUCCEEDED"
    strip = page.locator("ol[aria-label='Re-run progress']")
    assert strip.locator("li.exec").count() == run["stages_executed"]
    assert strip.locator("li.reuse").count() == run["stages_reused"]
    page.get_by_role("button", name="Next: before and after").click()
    after = api(stack, f"/api/v1/cases/{cid}")
    verdict = page.locator(".callout", has_text=re.compile("Claim preparation complete|Not complete yet")).first.inner_text()
    complete = (after["case"]["status"] == "PREPARATION_COMPLETE" and not after["root_blocker"]["roots"]
                and not [t for t in after["tasks"] if t["status"] == "OPEN"])
    assert ("Claim preparation complete" in verdict) == complete, verdict
    page.get_by_role("button", name="Next: see the audit trail").click()
    page.locator(".tl-correction").first.wait_for(timeout=10000)
    assert any(e["event_type"] == "CORRECTION_APPLIED" for e in api(stack, f"/api/v1/cases/{cid}/events"))
    assert not page.console_errors, page.console_errors


def test_guided_demo_shows_a_rejected_correction_and_changes_nothing(page, stack):
    cid = _start_demo(page, stack)
    page.route(f"**/api/v1/cases/{cid}/corrections",
               lambda route: route.fulfill(status=409, content_type="application/json",
                                           body=json.dumps({"detail": "case changed since you loaded it; reload"})))
    _to_apply_step(page)
    page.get_by_role("button", name="Apply correction and re-run").click()
    alert = page.get_by_role("alert").filter(has_text="not applied").first
    alert.wait_for(timeout=10000)
    assert "reload" in alert.inner_text().lower()
    assert alert.get_by_role("button", name="Reload the case").is_visible()
    assert api(stack, f"/api/v1/cases/{cid}")["case"]["status"] == "BLOCKED"      # nothing persisted
    assert page.locator("text=Claim preparation complete").count() == 0


def test_guided_demo_never_reports_success_for_a_failed_run(page, stack):
    cid = _start_demo(page, stack)
    detail = api(stack, f"/api/v1/cases/{cid}")
    failed = {"correction": {"id": 0, "op": "delete_bill_line", "domain": "bill",
                             "before": {"is_deleted": False}, "after": {"is_deleted": True}},
              "status_before": "BLOCKED", "case": dict(detail["case"], status="ERROR"),
              "run": {"id": 0, "status": "FAILED", "error": "simulated stage failure (test)", "stages_executed": 1,
                      "stages_reused": 0, "wall_ms": 3.0,
                      "stages": [{"id": 1, "stage": "ingestion", "status": "SUCCEEDED", "duration_ms": 1.0},
                                 {"id": 2, "stage": "billing", "status": "FAILED", "duration_ms": 1.0}]}}
    page.route(f"**/api/v1/cases/{cid}/corrections",
               lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(failed)))
    _to_apply_step(page)
    page.get_by_role("button", name="Apply correction and re-run").click()
    page.get_by_role("button", name="Next: before and after").wait_for(timeout=30000)
    assert page.locator("ol[aria-label='Re-run progress'] li.fail").count() == 1
    page.get_by_role("button", name="Next: before and after").click()
    assert page.get_by_role("alert").filter(has_text="FAILED").count() >= 1
    assert page.locator("text=Claim preparation complete").count() == 0
    assert page.locator(".callout", has_text="Not complete yet").count() == 1


def test_audit_trail_page_is_plain_language_and_verifies_chain(page, stack):
    go(page, stack, "/audit")
    page.get_by_role("heading", name="Audit trail", exact=True).wait_for()
    page.locator("text=Hash chain verified").first.wait_for(timeout=10000)
    page.get_by_label("Show routine events too").check()
    page.wait_for_load_state("networkidle")
    titles = page.locator(".tl-item .tl-body strong").all_inner_texts()
    assert titles
    assert not any(re.fullmatch(r"[A-Z_]+", t) for t in titles), titles[:5]       # no raw event codes as titles


def test_experiments_compare_full_and_targeted_from_recorded_run(page, stack):
    with open(os.path.join(ROOT, "results", "experiments", "main", "summary.json"), encoding="utf-8") as f:
        agg = json.load(f)["aggregate"]
    go(page, stack, "/experiments")
    page.get_by_label("Recorded run").select_option("main")
    page.get_by_role("button", name="Load", exact=True).click()
    page.get_by_role("heading", name="Full rerun vs targeted rerun").wait_for(timeout=15000)
    cells = page.locator("table.cmp tr", has_text="Stages executed while fixing").locator("td").all_inner_texts()
    num = lambda t: float(t.replace(",", "").split()[0])
    assert abs(num(cells[1]) - agg["B_sequential_full"]["loop_stage_executions"]["mean"]) < 0.06
    assert abs(num(cells[2]) - agg["C_parallel_targeted"]["loop_stage_executions"]["mean"]) < 0.06
