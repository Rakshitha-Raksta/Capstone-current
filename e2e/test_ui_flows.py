"""
Critical user flows, driven in a real browser against the real backend.
No mocks: every assertion is checked against what the API returns.
"""
import json
import os
import re
import urllib.request

import pytest

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".artifacts")


def api(stack, path):
    with urllib.request.urlopen(stack["api"] + path, timeout=30) as r:
        return json.load(r)


def go(page, stack, path):
    page.goto(stack["web"] + path)
    page.wait_for_load_state("networkidle")


def test_claims_status_filter(page, stack):
    go(page, stack, "/claims")
    page.get_by_role("button", name="Blocked", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "status=BLOCKED" in page.url
    badges = page.locator("table.tbl tbody tr td:nth-child(2)").all_inner_texts()
    assert badges and all("Blocked" in b for b in badges)
    total = api(stack, "/api/v1/cases?status=BLOCKED&page_size=1")["total"]
    assert page.locator(f"text=of {total}").first.is_visible()


def test_investigation_correction_triggers_targeted_rerun(page, stack):
    case = api(stack, "/api/v1/cases?root=DUPLICATE_BILL_ITEM&page_size=1")["items"][0]
    go(page, stack, f"/claims/{case['id']}")
    assert page.locator(".blocker-title").first.inner_text() == "Duplicate bill line"
    for h in ["What is wrong?", "Evidence", "What to do next", "What happens when corrected?", "Technical details"]:
        assert page.get_by_role("heading", name=h, exact=True).count() == 1, h
    page.get_by_role("button", name="Apply correction…").first.click()
    page.get_by_label("Reason for correction (recorded in the audit trail)").fill("duplicate confirmed by billing desk")
    page.get_by_role("button", name="Apply and re-run affected stages").click()
    page.get_by_role("heading", name=re.compile("Result of the last correction")).wait_for(timeout=20000)
    changed = page.locator(".result-block").first
    reexec = changed.locator(".stage-chip.hot").all_inner_texts()
    assert "Billing verification" in reexec and "Insurance eligibility" not in reexec
    detail = api(stack, f"/api/v1/cases/{case['id']}")
    assert detail["corrections"][-1]["op"] == "delete_bill_line"
    run = api(stack, f"/api/v1/cases/{case['id']}/runs")[0]
    assert run["trigger"] == "CORRECTION" and run["stages_executed"] < 11
    assert not any(f["code"] == "DUPLICATE_BILL_ITEM" and f["evidence"].get("line_no") == detail["corrections"][-1]["payload"]["line_no"]
                   for f in detail["findings"])
    page.locator("summary", has_text="Audit history").click()
    entry = page.locator(".tl-correction", has_text="Removed a bill line").first
    entry.wait_for(timeout=10000)
    assert "duplicate confirmed by billing desk" in entry.inner_text()


def all_open_tasks(stack):
    out, pg = [], 1
    while True:
        body = api(stack, f"/api/v1/review-tasks?status=OPEN&page_size=100&page={pg}")
        out += body["items"]
        if len(out) >= body["total"]:
            return out
        pg += 1


def test_each_task_offers_only_its_allowed_decisions(page, stack):
    tasks = all_open_tasks(stack)
    t = next(x for x in tasks if x["allowed_decisions"] == ["CONFIRM_BLOCKED"])
    go(page, stack, f"/claims/{t['case_id']}")
    grp = page.get_by_role("group", name=t["title"]).first
    sel = grp.get_by_label(f"Decision for: {t['title']}")
    sel.wait_for(timeout=15000)                                   # the case page loads its tasks asynchronously
    opts = [o for o in sel.locator("option").evaluate_all("os => os.map(o => o.value)") if o]
    assert opts == ["CONFIRM_BLOCKED"]
    assert grp.get_by_role("button", name=re.compile("^Correct data")).count() == 0


def test_accept_with_justification_validates_persists_and_refreshes(page, stack):
    tasks = all_open_tasks(stack)
    t = next(x for x in tasks if x["code"] == "CODED_DX_NOT_IN_NOTE"
             and x["allowed_decisions"] == ["ACCEPT_WITH_JUSTIFICATION", "CORRECT_DATA"])
    go(page, stack, f"/claims/{t['case_id']}")
    grp = page.get_by_role("group", name=t["title"]).first
    grp.get_by_label(f"Decision for: {t['title']}").select_option("ACCEPT_WITH_JUSTIFICATION")
    note = grp.get_by_label(f"Justification (audited) for: {t['title']}")
    note.fill("no")                                         # too short -> inline, announced error
    grp.get_by_role("button", name=f"Record decision for: {t['title']}").click()
    alert = grp.get_by_role("alert")
    assert "at least 5 characters" in alert.inner_text()
    assert note.get_attribute("aria-invalid") == "true"
    still = next(x for x in api(stack, f"/api/v1/cases/{t['case_id']}")["tasks"] if x["id"] == t["id"])
    assert still["status"] == "OPEN"                         # nothing persisted on a validation error
    note.fill("Note amended by treating team; coder confirmed principal diagnosis.")
    grp.get_by_role("button", name=f"Record decision for: {t['title']}").click()
    page.locator(".toast", has_text="Decision recorded").wait_for(timeout=15000)
    # persisted: fresh API read (backed by the database) and an audit event
    after = next(x for x in api(stack, f"/api/v1/cases/{t['case_id']}")["tasks"] if x["id"] == t["id"])
    assert after["status"] == "ACCEPTED_WITH_JUSTIFICATION" and after["resolution_note"].startswith("Note amended")
    assert any(e["event_type"] == "REVIEW_DECISION" for e in api(stack, f"/api/v1/cases/{t['case_id']}/events"))
    # refreshed UI shows the persisted state
    page.reload()
    page.wait_for_load_state("networkidle")
    grp2 = page.get_by_role("group", name=t["title"]).first
    assert "Accepted with justification" in grp2.inner_text()
    assert grp2.get_by_label(f"Decision for: {t['title']}").count() == 0


def test_correct_data_from_task_persists_and_resolves_task(page, stack):
    tasks = all_open_tasks(stack)
    t = next(x for x in tasks if x["code"] == "MISSING_CLAIM_DOCUMENT" and x["allowed_decisions"] == ["CORRECT_DATA"])
    go(page, stack, f"/claims/{t['case_id']}")
    grp = page.get_by_role("group", name=t["title"]).first
    assert grp.get_by_label(re.compile("^Decision")).count() == 0          # BLOCKING data issue: no "accept"
    grp.get_by_role("button", name=f"Correct data for: {t['title']}").click()
    dlg = page.get_by_role("dialog")
    dlg.get_by_label("Document received").wait_for()
    assert dlg.get_by_label("Document received").input_value() == t["evidence"]["document"]
    dlg.get_by_label("Reason for correction (recorded in the audit trail)").fill("document received at discharge desk")
    dlg.get_by_role("button", name="Apply and re-run affected stages").click()
    page.get_by_role("heading", name=re.compile("Result of the last correction")).wait_for(timeout=20000)
    detail = api(stack, f"/api/v1/cases/{t['case_id']}")
    corr = detail["corrections"][-1]
    assert corr["op"] == "add_document" and corr["domain"] == "documents"
    assert t["evidence"]["document"] in corr["after"]["documents"]
    task = next(x for x in detail["tasks"] if x["id"] == t["id"])
    assert task["status"] == "RESOLVED_BY_CORRECTION"
    page.reload()
    page.wait_for_load_state("networkidle")
    assert "Resolved by correction" in page.get_by_role("group", name=t["title"]).first.inner_text()


def test_workflow_targeted_rerun_control_reuses_unchanged_stages(page, stack):
    cid = api(stack, "/api/v1/cases?page_size=1")["items"][0]["id"]
    go(page, stack, f"/workflow?case={cid}")
    page.get_by_role("button", name="Hospital bill").click()           # deselect default
    page.get_by_role("button", name="Documents").click()
    page.get_by_role("button", name="Run", exact=True).click()
    page.locator(".toast", has_text="reused").wait_for(timeout=15000)
    run = api(stack, f"/api/v1/cases/{cid}/runs")[0]
    assert run["changed_domains"] == ["documents"] and run["stages_reused"] > 0
    assert page.locator("text=hash chain verified").first.is_visible()


def test_policy_library_and_rule_citations(page, stack):
    go(page, stack, "/policies")
    page.locator("text=UIN conflict").first.wait_for(timeout=15000)
    page.get_by_role("link", name="Rules & citations →").first.click()
    page.wait_for_load_state("networkidle")
    body = page.locator("body").inner_text()
    assert re.search(r"(SA26|HDFC2[16]|IRDAI)-\d{3}", body)


def test_api_failure_shows_error_and_retry_recovers(page, stack):
    page.route("**/api/v1/overview", lambda route: route.abort())
    go(page, stack, "/about")
    err = page.get_by_role("alert").first
    err.wait_for()
    assert "Could not load" in err.inner_text()
    page.unroute("**/api/v1/overview")
    page.get_by_role("button", name="Retry").click()
    page.locator(".stat").first.wait_for(timeout=15000)


def test_correction_validation_error_is_shown(page, stack):
    case = api(stack, "/api/v1/cases?root=INVALID_ICD_CODE&page_size=1")["items"]
    if not case:
        pytest.skip("no invalid-code case in demo data")
    go(page, stack, f"/claims/{case[0]['id']}")
    page.get_by_role("button", name="Apply correction…").first.click()
    page.get_by_role("button", name="Apply and re-run affected stages").click()   # no reason given
    assert "reason" in page.get_by_role("alert").last.inner_text().lower()
