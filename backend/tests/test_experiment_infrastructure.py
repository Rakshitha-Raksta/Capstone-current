"""
Experiment infrastructure: per-run trigger records, the decision-aware baseline, corrected root
attribution, full output equivalence and the independent verifier.

Unit tests use hand-built inputs. The integration tests run the real harness on a tiny fresh cohort in a
pytest temporary directory (never on recorded results, datasets or the application database).
"""
import copy
import importlib.util
import json
import os
import shutil

import pytest

from app.clearclaim import dag
from app.experiments import harness
from app.experiments.equivalence import compare_arms, compare_phase
from app.experiments.metrics import prf, root_attribution

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("verify_results_mod", os.path.join(ROOT, "scripts", "verify_results.py"))
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)

REG = {"A", "B", "C", "D"}


# ---------------------------------------------------------------------------
# root attribution
# ---------------------------------------------------------------------------

def _case(h, findings, roots):
    return dict(hadm_id=h, findings=findings, roots=roots)


def test_root_metrics_are_independent_of_detection():
    # A injected and DETECTED, but only as a consequence: detection TP, root FN
    cases = [_case("1", ["A"], [])]
    m = root_attribution(cases, {"1": {"A"}}, REG)
    assert (m["tp"], m["fp"], m["fn"]) == (0, 0, 1)
    assert m["fn_found_as_consequence"] == 1 and m["fn_not_detected"] == 0
    assert m["precision"] is None and m["recall"] == 0.0 and m["f1"] is None


def test_root_metrics_missing_extra_and_multiple_roots():
    cases = [
        _case("1", ["A", "B"], ["A", "B"]),     # two injected roots, both found as roots
        _case("2", ["A", "C"], ["A", "C"]),     # C is an extra (spurious) root
        _case("3", [], []),                     # D injected, not detected at all
        _case("4", ["B"], ["B"]),               # clean case with a spurious root
        _case("5", ["A", "X"], ["A", "X"]),     # X is outside the registry: ignored on both sides
    ]
    truth = {"1": {"A", "B"}, "2": {"A"}, "3": {"D"}, "5": {"A"}}
    m = root_attribution(cases, truth, REG)
    assert (m["tp"], m["fp"], m["fn"]) == (4, 2, 1)
    assert m["fn_not_detected"] == 1 and m["fn_found_as_consequence"] == 0
    assert m["precision"] == round(4 / 6, 4) and m["recall"] == 0.8
    assert m["exact_set_match"] == dict(correct=2, total=4, accuracy=0.5)
    assert m["clean_cases_with_a_root"] == 1


def test_root_metrics_zero_denominators_are_none():
    m = root_attribution([_case("1", [], [])], {}, REG)
    assert (m["tp"], m["fp"], m["fn"]) == (0, 0, 0)
    assert m["precision"] is None and m["recall"] is None and m["f1"] is None
    assert m["exact_set_match"]["accuracy"] is None
    assert prf(0, 3, 0) == dict(tp=0, fp=3, fn=0, precision=0.0, recall=None, f1=None)
    # precision = recall = 0 with non-zero denominators: F1 is defined and equals 0 (not None)
    assert prf(0, 2, 2)["f1"] == 0.0 and prf(0, 2, 2)["precision"] == 0.0 and prf(0, 2, 2)["recall"] == 0.0


# ---------------------------------------------------------------------------
# full output equivalence
# ---------------------------------------------------------------------------

def _finding(**kw):
    f = dict(stage="billing", code="DUPLICATE_BILL_ITEM", severity="BLOCKING", rule_id=None, title="Duplicate bill line",
             message="Line 4 duplicates line 2", expected="1 line", actual="2 identical lines", amount=500.0,
             evidence={"line_no": 4, "duplicate_of": 2}, role="ROOT", caused_by=None, source_document=None, source_page=None)
    f.update(kw)
    return f


def _task(**kw):
    t = dict(task_key="k1:DUPLICATE_BILL_ITEM", code="DUPLICATE_BILL_ITEM", severity="BLOCKING", owner_stage="billing",
             input_domain="bill", title="Duplicate bill line", reason="Line 4 duplicates line 2", evidence={"line_no": 4},
             recommended_action="Remove the duplicate bill line.", required_reruns=["billing"],
             allowed_decisions=["CORRECT_DATA"], status="OPEN", assignee=None, resolution_note=None, resolved_by=None)
    t.update(kw)
    return t


def _phase(**kw):
    p = dict(status="BLOCKED", findings=[_finding()], tasks=[_task()], stage_outputs={s: f"h-{s}" for s in dag.STAGE_ORDER})
    p.update(kw)
    return p


def _raw(phase_final=None, phase_first=None):
    return {"98000001": dict(outputs=dict(first_pass=phase_first or _phase(), final=phase_final or _phase()))}


def test_equivalence_identical():
    r = compare_arms(_raw(), _raw(), "full", "targeted")
    assert r["equivalent"] is True and r["n_mismatches"] == 0 and r["identical_all_phases"] == 1
    assert r["not_compared"] and r["compared"]


def test_equivalence_detects_multiplicity_difference():
    two = _phase(findings=[_finding(), _finding()])
    r = compare_arms(_raw(), _raw(phase_final=two), "full", "targeted")
    assert not r["equivalent"]
    assert r["mismatches"][0]["field"] == "finding.multiplicity" and r["mismatches"][0]["phase"] == "final"
    assert r["mismatches"][0]["hadm_id"] == "98000001"


def test_equivalence_detects_evidence_and_severity_difference():
    d = compare_phase(_phase(), _phase(findings=[_finding(severity="REVIEW", evidence={"line_no": 4, "duplicate_of": 3})]))
    assert {x["field"] for x in d} == {"finding.severity", "finding.evidence"}


def test_equivalence_detects_task_status_and_final_status_difference():
    d = compare_phase(_phase(), _phase(status="PREPARATION_COMPLETE", tasks=[_task(status="RESOLVED_BY_CORRECTION")]))
    assert {x["field"] for x in d} == {"status", "task.status"}
    d2 = compare_phase(_phase(), _phase(stage_outputs={**_phase()["stage_outputs"], "claim_generation": "other"}))
    assert [x["field"] for x in d2] == ["stage_output.claim_generation"]


def test_equivalence_ignores_volatile_fields_and_order():
    noisy = _phase(findings=[dict(_finding(), id=77, stage_execution_id=9, created_at="2026-01-01T00:00:00")],
                   tasks=[dict(_task(), id=5, created_at="x", resolved_at="y", created_run_id=3, issue_id=8)])
    assert compare_phase(_phase(), noisy) == []
    a = _phase(findings=[_finding(), _finding(evidence={"line_no": 9, "duplicate_of": 2})])
    b = _phase(findings=list(reversed(a["findings"])))
    assert compare_phase(a, b) == []


def test_equivalence_without_outputs_is_not_claimed():
    r = compare_arms({"1": dict(findings=[])}, {"1": dict(findings=[])}, "full", "targeted")
    assert r["equivalent"] is False and r["cases_without_outputs"] == 1 and r["n_cases_compared"] == 0


def test_verifier_phase_comparison_agrees_with_harness_comparator():
    base = _raw()
    variants = [_raw(phase_final=_phase(findings=[_finding(), _finding()])),
                _raw(phase_final=_phase(tasks=[_task(status="SUPERSEDED")])),
                _raw(phase_final=_phase(findings=[dict(_finding(), id=1, created_at="t")]))]   # volatile only
    for v in variants:
        n, diff = vr.phase_differences(base, v)
        assert bool(diff["final"]) == (compare_arms(base, v, "a", "b")["n_mismatches"] > 0)


# ---------------------------------------------------------------------------
# integration: real harness on a tiny fresh cohort (temporary directory only)
# ---------------------------------------------------------------------------

ARMS_UNDER_TEST = ["B_sequential_full", "C_parallel_targeted", "C_minus_targeted", "C_decision_aware_full"]


@pytest.fixture(scope="module")
def experiment(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("infra_exp"))
    res = harness.run_experiment(out, seeds=(11,), n_encounters=16, error_rate=0.9, arms=ARMS_UNDER_TEST,
                                 log=lambda *_: None)
    raws = {a: json.load(open(os.path.join(out, f"raw_{a}_seed11.json"), encoding="utf-8")) for a in ARMS_UNDER_TEST}
    return out, res, raws


def test_default_arms_unchanged_and_research_arm_opt_in():
    assert list(harness.ARMS) == ["B_sequential_full", "C_parallel_targeted", "C_minus_targeted", "C_minus_root_routing"]
    assert "C_decision_aware_full" in harness.ALL_ARMS and "C_decision_aware_full" not in harness.ARMS
    with pytest.raises(ValueError):
        harness.run_experiment("unused", arms=["NOT_AN_ARM"])


def test_every_run_records_its_trigger(experiment):
    _, _, raws = experiment
    seen = set()
    for arm, raw in raws.items():
        for h, r in raw.items():
            assert r["runs"][0]["action"] == "INITIAL" and sum(x["action"] == "INITIAL" for x in r["runs"]) == 1
            for x in r["runs"]:
                assert x["action"] == x["trigger"] and x["arm"] == arm and str(x["hadm_id"]) == h
                parts = (x["stages_executed"] + x["stages_reused"] + x["stages_failed"]
                         + x["stages_skipped_dependency_failed"] + x["stages_not_rerun"])
                assert sorted(parts) == sorted(dag.STAGE_ORDER)
                assert x["wall_ms"] is not None and x["harness_action_ms"] is not None
                assert x["harness_action_ms"] >= x["wall_ms"]
                if x["action"] == "CORRECTION":
                    assert x["correction_op"] and x["decision"] is None and x["changed_domains"]
                if x["action"] == "REVIEW_DECISION":
                    assert x["decision"] and x["correction_op"] is None and x["changed_domains"] == ["reviews"]
                seen.add(x["action"])
            loop = r["runs"][1:]
            assert r["loop_stage_executions"] == sum(x["n_executed"] for x in loop)
            assert r["corrections"] == sum(x["action"] == "CORRECTION" for x in loop)
            assert r["decisions"] == sum(x["action"] == "REVIEW_DECISION" for x in loop)
    assert {"CORRECTION", "REVIEW_DECISION"} <= seen, "cohort must exercise both triggers"


def test_decision_aware_baseline_behaviour(experiment):
    _, res, raws = experiment
    plan = dag.invalidated_by(["reviews"])               # derived from the dependency graph, not assumed
    da = [x for r in raws["C_decision_aware_full"].values() for x in r["runs"]]
    for x in da:
        if x["action"] == "REVIEW_DECISION":
            assert x["stages_executed"] == plan and x["stages_reused"] == [] and x["rerun_policy"] == "targeted"
        else:
            assert x["stages_executed"] == dag.STAGE_ORDER and x["rerun_policy"] == "full"
    for x in (x for r in raws["C_parallel_targeted"].values() for x in r["runs"] if x["action"] == "REVIEW_DECISION"):
        assert set(x["stages_executed"]) | set(x["stages_reused"]) <= set(plan)
    for x in (x for r in raws["B_sequential_full"].values() for x in r["runs"]):
        assert x["stages_executed"] == dag.STAGE_ORDER
    arms = res["seeds"][11]["arms"]
    for action in ("CORRECTION", "REVIEW_DECISION"):
        c = arms["C_parallel_targeted"]["loop_by_trigger"][action]["stage_executions"]
        assert c <= arms["C_decision_aware_full"]["loop_by_trigger"][action]["stage_executions"]
        assert arms["C_decision_aware_full"]["loop_by_trigger"][action]["stage_executions"] <= \
            arms["B_sequential_full"]["loop_by_trigger"][action]["stage_executions"]
    # same oracle actions, same detection in every arm (the baseline changes execution, not semantics)
    ref = raws["C_parallel_targeted"]
    for arm in ARMS_UNDER_TEST:
        for h, r in raws[arm].items():
            assert (r["findings"], r["roots"], r["first_status"], r["corrections"], r["decisions"]) == \
                (ref[h]["findings"], ref[h]["roots"], ref[h]["first_status"], ref[h]["corrections"], ref[h]["decisions"])


def test_root_attribution_and_legacy_field_in_summary(experiment):
    _, res, raws = experiment
    m = res["seeds"][11]["arms"]["C_parallel_targeted"]
    assert "legacy_note" in m["detection_root_findings"]
    gt = vr.gt_for(experiment[0], "11")
    mine = vr.root_counts(raws["C_parallel_targeted"], gt)
    assert (m["root_attribution"]["tp"], m["root_attribution"]["fp"], m["root_attribution"]["fn"]) == \
        (mine["tp"], mine["fp"], mine["fn"])
    assert "root_attribution.recall" in res["aggregate"]["C_parallel_targeted"]


def test_equivalence_results_are_structured(experiment):
    _, res, _ = experiment
    eq = res["seeds"][11]["equivalence"]
    assert set(eq) == {f"{a}__vs__{b}" for a, b in harness.EQUIVALENCE_PAIRS}
    for e in eq.values():
        assert e["n_cases_compared"] == 16 and e["not_compared"]
        assert e["cases_with_differences"]["first_pass"] == 0          # pass 1 identical by construction
        assert e["equivalent"] == (e["n_mismatches"] == 0)


def test_verifier_passes_on_consistent_results(experiment):
    out, res, _ = experiment
    assert vr.main([out]) == 0
    v = json.load(open(os.path.join(out, "verification.json"), encoding="utf-8"))
    assert v["mismatches"] == [] and v["completeness_problems"] == []
    assert all(isinstance(x, dict) for x in v["run_records"].values())
    assert v["derived_from_run_records"]["loop_execution_reduction_C_vs_C_decision_aware_full"] == \
        res["derived"]["loop_execution_reduction_C_vs_C_decision_aware_full"]


def _tampered(experiment, tmp_path, mutate_summary=None, mutate_raw=None):
    out = str(tmp_path / "copy")
    shutil.copytree(experiment[0], out)
    if mutate_summary:
        p = os.path.join(out, "summary.json")
        s = json.load(open(p, encoding="utf-8"))
        mutate_summary(s)
        json.dump(s, open(p, "w", encoding="utf-8"))
    if mutate_raw:
        p = os.path.join(out, "raw_C_parallel_targeted_seed11.json")
        r = json.load(open(p, encoding="utf-8"))
        mutate_raw(r)
        json.dump(r, open(p, "w", encoding="utf-8"))
    return vr.main([out])


def test_verifier_detects_inconsistent_root_metric(experiment, tmp_path):
    def m(s):
        s["seeds"]["11"]["arms"]["C_parallel_targeted"]["root_attribution"]["fn"] += 1
    assert _tampered(experiment, tmp_path, mutate_summary=m) == 1


def test_verifier_detects_inconsistent_trigger_totals(experiment, tmp_path):
    def m(s):
        s["seeds"]["11"]["arms"]["C_decision_aware_full"]["loop_by_trigger"]["REVIEW_DECISION"]["stage_executions"] += 1
    assert _tampered(experiment, tmp_path, mutate_summary=m) == 1


def test_verifier_detects_relabelled_run_record(experiment, tmp_path):
    def m(raw):
        for r in raw.values():
            for x in r["runs"]:
                if x["action"] == "REVIEW_DECISION":
                    x["action"] = "CORRECTION"
                    return
    assert _tampered(experiment, tmp_path, mutate_raw=m) == 1


def test_verifier_detects_inconsistent_equivalence_count(experiment, tmp_path):
    def m(s):
        e = s["seeds"]["11"]["equivalence"]["B_sequential_full__vs__C_parallel_targeted"]
        e["cases_with_differences"]["final"] += 1
    assert _tampered(experiment, tmp_path, mutate_summary=m) == 1


def test_verifier_detects_tampered_derived_reduction(experiment, tmp_path):
    def m(s):
        s["derived"]["loop_execution_reduction_C_vs_B_sequential_full"] = 0.99
    assert _tampered(experiment, tmp_path, mutate_summary=m) == 1


def test_verifier_requires_none_for_zero_denominator(experiment, tmp_path):
    def m(s):
        ra = s["seeds"]["11"]["arms"]["C_parallel_targeted"]["root_attribution"]
        ra["recall"] = 0.0 if ra["recall"] is None else None
    assert _tampered(experiment, tmp_path, mutate_summary=m) == 1
