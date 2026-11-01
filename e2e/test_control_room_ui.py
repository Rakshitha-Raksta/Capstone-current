"""
Rebuilt Agent Control Room (3D scene) and Present Workflow, in a real browser against the real
backend. On-screen states and numbers are compared with the records the backend persisted.
"""
import copy
import json
import re
import urllib.request

STAGES = ["ingestion", "data_validation", "discharge_readiness", "billing", "eligibility", "adjudication",
          "historical", "reconciliation", "root_blocker", "review_routing", "claim_generation"]
FINAL = {"OK": "completed", "WARN": "issue", "BLOCKED": "blocked"}


def api(stack, path):
    with urllib.request.urlopen(stack["api"] + path, timeout=30) as r:
        return json.load(r)


def open_cr(page, stack, hadm):
    page.goto(f"{stack['web']}/control-room?encounter={hadm}")
    page.wait_for_load_state("networkidle")


def correctable(stack):
    cases = api(stack, "/api/v1/cases?status=BLOCKED&root=DUPLICATE_BILL_ITEM&page_size=50")["items"]
    return next(c for c in cases if c["open_tasks"] == 1)


def states(page, sel=".wg-label, .s3d-node"):
    return {n.get_attribute("data-stage"): n.get_attribute("data-state") for n in page.locator(sel).all()}


def expected_states(run):
    out = {}
    for e in run["events"]:
        if e["type"] == "stage_finished":
            out[e["stage"]] = "failed" if e["status"] == "FAILED" else FINAL[e["stage_status"]]
        elif e["type"] == "stage_reused":
            out[e["stage"]] = "reused"
        elif e["type"] == "stage_not_rerun":
            out[e["stage"]] = "notrun"
    return out


def test_idle_scene_shows_the_real_graph_and_nothing_running(page, stack):
    open_cr(page, stack, correctable(stack)["hadm_id"])
    page.locator("text=not independent autonomous AI models").first.wait_for(timeout=15000)
    assert page.locator("text=ClearClaim is a research prototype").first.is_visible()
    assert page.locator("text=Synthetic demo data").first.is_visible()
    s = states(page)
    assert list(s) == STAGES and set(s.values()) == {"waiting"}
    assert "nothing is running" in page.locator(".hud").inner_text()
    for title in ("SEQUENTIAL MODE", "PARALLEL MODE", "TARGETED REPROCESSING MODE"):
        assert page.get_by_role("tab", name=re.compile(title)).is_visible()
    assert not page.console_errors, page.console_errors


def test_invalid_encounter_is_rejected_and_launch_disabled(page, stack):
    open_cr(page, stack, "12345")
    assert "No encounter with ID 12345" in page.get_by_role("alert").first.inner_text()
    assert page.get_by_role("button", name="Launch Workflow Comparison").is_disabled()


def test_launch_runs_all_three_modes_and_ui_matches_records(page, stack):
    case = correctable(stack)
    open_cr(page, stack, case["hadm_id"])
    page.locator(".cr-corr-prev", has_text="Stages invalidated by this change").wait_for(timeout=15000)
    page.get_by_label("Repetitions").fill("2")
    page.get_by_role("button", name="Launch Workflow Comparison").click()
    hud = page.locator(".hud")
    hud.filter(has_text="slower than real").wait_for(timeout=120000)          # playback is labelled as such
    hist = api(stack, "/api/v1/control-room/history")
    cmp = api(stack, f"/api/v1/control-room/history/{next(h['id'] for h in hist if h['kind'] == 'cr_comparison')}")
    cor = api(stack, f"/api/v1/control-room/history/{next(h['id'] for h in hist if h['kind'] == 'cr_correction')}")
    assert cmp["summary"]["outputs_match"] and cmp["summary"]["same_input_snapshot"]
    assert cor["summary"]["outputs_match"] and cor["summary"]["executions_avoided"] > 0
    assert api(stack, f"/api/v1/cases/{case['id']}")["case"]["status"] == "BLOCKED"   # live data untouched

    def final_matches(run):
        page.get_by_role("button", name="Show final state").click()
        assert states(page) == expected_states(run)

    seq = [r for r in cmp["summary"]["runs"] if r["mode"] == "sequential" and not r["warmup"]][0]
    final_matches(seq)
    assert seq["metrics"]["max_concurrency"] == 1
    page.get_by_role("tab", name=re.compile("PARALLEL MODE")).click()
    par = [r for r in cmp["summary"]["runs"] if r["mode"] == "parallel" and not r["warmup"]][0]
    final_matches(par)
    page.get_by_role("tab", name=re.compile("TARGETED REPROCESSING")).click()
    tgt = next(r for r in cor["summary"]["runs"] if r["rerun_policy"] == "targeted")
    final_matches(tgt)
    assert "notrun" in states(page).values()
    assert f"{cor['summary']['executions_avoided']} stage executions avoided" in page.get_by_role("tab", name=re.compile("TARGETED")).inner_text()

    # inspector, list and 2D views agree with the 3D scene
    page.locator(".s3d-node[data-stage='billing'] .s3d-bb").click()
    insp = page.locator(".insp-float").inner_text()
    assert "rule-based python component" in insp.lower() and "Reconciles every bill line" in insp
    scene = states(page)
    page.get_by_role("button", name="List", exact=True).click()
    assert {r.get_attribute("data-stage"): r.get_attribute("data-state") for r in page.locator("tr[data-stage]").all()} == scene
    page.get_by_role("button", name="2D graph").click()
    assert states(page, ".ac-node") == scene
    page.get_by_role("button", name="3D", exact=True).click()

    page.get_by_role("tab", name=re.compile("SEQUENTIAL MODE")).click()
    page.get_by_role("button", name="Compare Timelines").click()
    assert page.locator(".tl-pair .gantt").count() == 2
    assert "Same horizontal scale" in page.locator(".tl-pair").inner_text()
    assert not page.console_errors, page.console_errors


def test_failed_run_is_shown_as_failure(page, stack):
    hist = api(stack, "/api/v1/control-room/history")
    rid = next((h["id"] for h in hist if h["kind"] == "cr_comparison"), None)
    if rid is None:
        case = correctable(stack)
        req = urllib.request.Request(stack["api"] + "/api/v1/control-room/comparisons", method="POST",
                                     data=json.dumps({"hadm_id": str(case["hadm_id"]), "repetitions": 1}).encode(),
                                     headers={"Content-Type": "application/json"})
        token = json.load(urllib.request.urlopen(req))["token"]
        while api(stack, f"/api/v1/control-room/jobs/{token}")["status"] in ("queued", "running"):
            pass
        rid = next(h["id"] for h in api(stack, "/api/v1/control-room/history") if h["kind"] == "cr_comparison")
    rec = api(stack, f"/api/v1/control-room/history/{rid}")
    bad = copy.deepcopy(rec)
    run = next(r for r in bad["summary"]["runs"] if r["mode"] == "sequential" and not r["warmup"])
    run["metrics"]["status"] = "FAILED"
    run["metrics"]["stages_failed"] = 1
    for e in run["events"]:
        if e["type"] == "stage_finished" and e["stage"] == "billing":
            e.update(status="FAILED", error="RuntimeError: simulated (test)")
    page.route(f"**/api/v1/control-room/history/{rid}", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(bad)))
    open_cr(page, stack, correctable(stack)["hadm_id"])
    page.locator("tr", has=page.get_by_label(f"Select record {rid} for comparison")).get_by_role("button", name="Open").click()
    page.get_by_role("alert").filter(has_text="did not succeed").first.wait_for(timeout=15000)
    page.get_by_role("button", name="Show final state").click()
    assert page.locator(".s3d-node[data-stage='billing']").get_attribute("data-state") == "failed"


def test_present_workflow_has_ten_scenes_and_runs_nothing_on_open(page, stack):
    before = len(api(stack, "/api/v1/control-room/history"))
    page.goto(stack["web"] + "/present")
    page.get_by_role("heading", name="The problem").wait_for()
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    page.get_by_role("heading", name="Sequential execution").wait_for()
    assert page.locator(".pr-dots button").count() == 10
    assert len(api(stack, "/api/v1/control-room/history")) == before                # opening scenes runs nothing
    if page.get_by_role("button", name="Replay recorded sequential run").count():
        page.get_by_role("button", name="Replay recorded sequential run").click()
        assert page.locator(".hud", has_text="slower than real").first.is_visible()
    page.keyboard.press("Escape")
    page.get_by_role("heading", name="Agent Control Room").wait_for()


def _camera(page):
    return [float(x) for x in page.locator(".wg").get_attribute("data-camera").split(",")]


def _close(a, b, tol=0.6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_webgl_scene_renders_real_3d_and_controls_work(page, stack):
    open_cr(page, stack, correctable(stack)["hadm_id"])
    scene = page.locator(".wg[data-renderer='webgl']")
    scene.wait_for(timeout=30000)
    page.wait_for_function("document.querySelector('.wg')?.dataset.ready === '1'", timeout=30000)
    box = scene.bounding_box()
    assert box["y"] < 768 and box["height"] >= 400                                      # visible without scrolling at 1366x768-class sizes
    # the canvas really drew a lit scene (not a blank or single-colour area)
    colours = page.evaluate("""() => { const c = document.querySelector('.wg canvas'); const o = document.createElement('canvas');
        o.width = 48; o.height = 48; const x = o.getContext('2d'); x.drawImage(c, 0, 0, 48, 48);
        const d = x.getImageData(0, 0, 48, 48).data; const s = new Set();
        for (let i = 0; i < d.length; i += 4) s.add((d[i] >> 4) + ',' + (d[i + 1] >> 4) + ',' + (d[i + 2] >> 4)); return s.size; }""")
    assert colours >= 8, colours
    assert page.locator(".wg-label").count() == 11
    page.wait_for_timeout(800)
    home = _camera(page)
    canvas = page.locator(".wg canvas").bounding_box()
    cx, cy = canvas["x"] + canvas["width"] * 0.5, canvas["y"] + canvas["height"] * 0.9
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 200, cy - 30, steps=10); page.mouse.up()
    page.wait_for_timeout(900)
    assert not _close(_camera(page), home)                                               # drag orbits the camera
    page.get_by_role("button", name="Reset view").click()
    page.wait_for_timeout(2500)
    assert _close(_camera(page), home)                                                   # reset returns home
    page.get_by_role("button", name="Zoom in").click()
    page.wait_for_timeout(1500)
    assert not _close(_camera(page), home)
    page.get_by_role("button", name="Reset view").click()
    page.wait_for_timeout(2500)
    page.get_by_role("button", name="Tilt camera").click()
    page.wait_for_timeout(1500)
    assert not _close(_camera(page), home)
    assert not page.console_errors, page.console_errors
