"""
Arm-order balancing, warm-up isolation, timing fields and the read-only statistical analysis.
Integration tests run the real harness on tiny fresh cohorts in pytest temporary directories only.
"""
import hashlib
import importlib.util
import json
import os
from collections import Counter

import pytest

from app.experiments import analysis as A
from app.experiments import harness
from app.experiments.arm_order import canonical, order_for_seed, position_balance, williams_rows

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UNIVERSE = list(harness.ALL_ARMS)
FIVE = ["B_sequential_full", "C_parallel_targeted", "C_minus_targeted", "C_minus_root_routing", "C_decision_aware_full"]


# ---------------------------------------------------------------------------
# Latin-square arm order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("k", [1, 2, 3, 4, 5, 6])
def test_williams_rows_are_latin_and_position_balanced(k):
    rows = williams_rows(k)
    assert len(rows) == (2 * k if k % 2 and k > 1 else k)
    for r in rows:
        assert sorted(r) == list(range(k))                       # each row is a permutation
    for pos in range(k):                                          # each arm equally often in each position
        assert len(set(Counter(r[pos] for r in rows).values())) == 1


def test_five_arms_over_ten_consecutive_seeds_are_exactly_balanced():
    orders = {s: order_for_seed(s, FIVE, UNIVERSE) for s in range(201, 211)}
    bal = position_balance(orders)
    assert bal["balanced"] and all(row == [2] * 5 for row in bal["counts"].values())
    assert len({tuple(o) for o in orders.values()}) == 10


def test_order_is_reproducible_and_independent_of_input_order_and_other_seeds():
    a = order_for_seed(205, FIVE, UNIVERSE)
    assert a == order_for_seed(205, list(reversed(FIVE)), UNIVERSE)
    assert a == {s: order_for_seed(s, FIVE, UNIVERSE) for s in (201, 205, 209)}[205]
    assert sorted(a) == sorted(FIVE)


def test_subsets_and_invalid_arms():
    sub = ["C_decision_aware_full", "C_parallel_targeted"]
    assert canonical(sub, UNIVERSE) == ["C_parallel_targeted", "C_decision_aware_full"]
    assert {tuple(order_for_seed(s, sub, UNIVERSE)) for s in (1, 2)} == {
        ("C_parallel_targeted", "C_decision_aware_full"), ("C_decision_aware_full", "C_parallel_targeted")}
    assert order_for_seed(7, ["C_parallel_targeted"], UNIVERSE) == ["C_parallel_targeted"]
    with pytest.raises(ValueError):
        canonical(["NOT_AN_ARM"], UNIVERSE)
    with pytest.raises(ValueError):
        canonical(["C_parallel_targeted", "C_parallel_targeted"], UNIVERSE)
    assert position_balance({1: ["a", "b"]})["balanced"] is False     # an unbalanced seed set is reported


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def test_holm_edge_cases():
    assert A.holm({}) == {}
    assert A.holm({"a": None, "b": None}) == {"a": None, "b": None}
    adj = A.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert adj["d"] is None
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
    assert A.holm({"a": 0.6, "b": 0.7}) == {"a": 1.0, "b": 1.0}                    # capped at 1
    tie = A.holm({"x": 0.02, "y": 0.02})
    assert tie["x"] == tie["y"] == pytest.approx(0.04)                             # monotone with ties


def test_wilcoxon_all_zero_ties_and_signal():
    z = A.wilcoxon_paired([0, 0, 0])
    assert z["status"] == "N/A" and z["p_value"] is None and z["n_zero"] == 3
    t = A.wilcoxon_paired([0, 2, 2, 2, 3, 0])
    assert t["status"] == "tested" and t["n_nonzero"] == 4 and t["n_zero"] == 2 and t["ties_among_nonzero"] == 2
    assert 0 < t["p_value"] <= 1
    s = A.wilcoxon_paired(list(range(1, 21)))
    assert s["p_value"] < 0.001


def test_bootstrap_is_reproducible_and_seed_sensitive():
    t = {f"p{i}": float(i % 3) for i in range(30)}
    b = {f"p{i}": 4.0 for i in range(30)}
    r1, r2 = A.cluster_bootstrap_reduction(t, b, B=2000, seed=7), A.cluster_bootstrap_reduction(t, b, B=2000, seed=7)
    assert r1 == r2 and r1["ci95"][0] <= r1["estimate"] <= r1["ci95"][1]
    assert A.cluster_bootstrap_reduction(t, b, B=2000, seed=8)["ci95"] != r1["ci95"]


def test_bootstrap_resamples_patients_not_encounters():
    # one patient with two encounters: a single cluster gives no interval, whatever the number of encounters
    one = A.cluster_bootstrap_reduction({"p1": 0.0 + 5.0}, {"p1": 10.0 + 10.0}, B=500)
    assert one["n_clusters"] == 1 and one["ci95"] is None and one["estimate"] == pytest.approx(0.75)
    # two patients: every resample is AA, AB or BB, so the interval ends are among {0, 0.5, 1}
    two = A.cluster_bootstrap_reduction({"A": 0.0, "B": 10.0}, {"A": 10.0, "B": 10.0}, B=4000, seed=1)
    assert two["n_clusters"] == 2 and set(two["ci95"]) <= {0.0, 0.5, 1.0}


def test_bootstrap_zero_baseline_and_mean_sd():
    r = A.cluster_bootstrap_reduction({"a": 0.0, "b": 0.0}, {"a": 0.0, "b": 0.0}, B=100)
    assert r["estimate"] is None and r["ci95"] is None
    assert A.mean_sd([0.5])["sd"] is None and A.mean_sd([0.4, 0.6])["sd"] == pytest.approx(0.141421, abs=1e-5)


def _case(subject, corr, dec, initial=11):
    runs = [dict(action="INITIAL", stages_executed=["s"] * initial)]
    runs += [dict(action="CORRECTION", stages_executed=["s"] * n) for n in corr]
    runs += [dict(action="REVIEW_DECISION", stages_executed=["s"] * n) for n in dec]
    return dict(subject_id=subject, runs=runs, loop_stage_executions=sum(corr) + sum(dec))


def test_patient_cluster_keys_synthetic_vs_mimic():
    raw = {"7": {"1": _case("90000001", [5], [2]), "2": _case("90000001", [6], [])}}
    for source, expected in (("SYNTHETIC", {"7:90000001"}), ("MIMIC_IV_DEMO_2.2", {"90000001"})):
        run = dict(summary=dict(config=dict(clinical_source=source)), dir="nonexistent",
                   raws={"C_parallel_targeted": raw, "C_decision_aware_full": raw})
        units, why = A.paired_units(run, "C_parallel_targeted", "C_decision_aware_full")
        assert why is None and {u[2] for u in units} == expected


def test_compare_reports_na_without_run_records_or_arm():
    old = {"7": {"1": dict(subject_id="1", loop_stage_executions=10), "2": dict(subject_id="2", loop_stage_executions=4)}}
    base = {"7": {"1": dict(subject_id="1", loop_stage_executions=20), "2": dict(subject_id="2", loop_stage_executions=8)}}
    run = dict(summary=dict(config={}), dir="nonexistent", raws={"C_parallel_targeted": old, "B_sequential_full": base})
    r = A.compare(run, "C_parallel_targeted", "B_sequential_full", B=200)
    assert r["outcomes"]["overall"]["pooled"]["estimate"] == pytest.approx(0.5)
    assert r["outcomes"]["CORRECTION"]["status"] == "N/A" and r["outcomes"]["REVIEW_DECISION"]["status"] == "N/A"
    assert A.compare(run, "C_parallel_targeted", "C_decision_aware_full")["status"] == "N/A"


# ---------------------------------------------------------------------------
# integration: real harness, tiny cohorts, temporary directories
# ---------------------------------------------------------------------------

TIMING_KEYS = {"wall_ms", "harness_action_ms", "first_wall_ms", "loop_wall_ms", "arm_execution"}


def _strip_timing(v):
    if isinstance(v, dict):
        return {k: _strip_timing(x) for k, x in v.items() if k not in TIMING_KEYS}
    if isinstance(v, list):
        return [_strip_timing(x) for x in v]
    return v


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = {}
    for name, kw in (("balanced_warm", dict(warmup=True, balance_order=True, arms=FIVE)),
                     ("fixed_cold", dict(warmup=False, balance_order=False, arms=list(reversed(FIVE))))):
        d = str(tmp_path_factory.mktemp(name))
        res = harness.run_experiment(d, seeds=(12,), n_encounters=14, error_rate=0.9, log=lambda *_: None, **kw)
        raws = {a: json.load(open(os.path.join(d, f"raw_{a}_seed12.json"), encoding="utf-8")) for a in FIVE}
        out[name] = (d, res, raws)
    return out


def test_arm_order_recorded_and_consistent(runs):
    d, res, raws = runs["balanced_warm"]
    order = res["seeds"][12]["arm_order"]
    assert order == order_for_seed(12, FIVE, UNIVERSE) and res["arm_order"]["per_seed"]["12"] == order
    for pos, arm in enumerate(order):
        for r in raws[arm].values():
            assert r["arm_execution"]["position"] == pos and r["arm_execution"]["arm_order"] == order
    assert res["config"]["arms"] == canonical(FIVE, UNIVERSE)
    assert runs["fixed_cold"][1]["seeds"][12]["arm_order"] == canonical(FIVE, UNIVERSE)


def test_warmup_is_reported_separately_and_restores_the_database(runs):
    _, res, raws = runs["balanced_warm"]
    for arm in FIVE:
        w = res["seeds"][12]["warmup"][arm]
        assert w["db_restored_from_base"] and w["restored_sha256"] == res["seeds"][12]["base_db_sha256"]
        assert w["wall_ms"] > 0 and w["harness_action_ms"] >= w["wall_ms"]
        first = raws[arm][str(w["hadm_id"])]
        assert sum(x["action"] == "INITIAL" for x in first["runs"]) == 1     # warm-up run not in the counted record
    assert runs["fixed_cold"][1]["seeds"][12]["warmup"] == {a: None for a in FIVE}


def test_order_and_warmup_change_nothing_but_timing(runs):
    _, res_a, raws_a = runs["balanced_warm"]
    _, res_b, raws_b = runs["fixed_cold"]
    for arm in FIVE:
        assert _strip_timing(raws_a[arm]) == _strip_timing(raws_b[arm])
        ma, mb = res_a["seeds"][12]["arms"][arm], res_b["seeds"][12]["arms"][arm]
        for k in ("loop_stage_executions", "loop_runs", "review_actions", "corrections", "decisions", "first_pass_tasks",
                  "tasks_created_total", "root_attribution", "detection_all_findings", "false_clear_rate"):
            assert ma[k] == mb[k], (arm, k)
        assert _strip_timing(ma["loop_by_trigger"]) == _strip_timing(mb["loop_by_trigger"])
    for k, e in res_a["seeds"][12]["equivalence"].items():
        assert e["cases_with_differences"] == res_b["seeds"][12]["equivalence"][k]["cases_with_differences"]


def test_timing_fields(runs):
    _, res, raws = runs["balanced_warm"]
    assert set(res["timing_definitions"]) >= {"wall_ms", "harness_action_ms", "warmup", "status"}
    m = res["seeds"][12]["arms"]["C_parallel_targeted"]
    assert m["first_pass_harness_ms"]["n"] == 14 and m["rerun_harness_ms"]["n"] == m["loop_runs"]
    assert m["first_pass_harness_ms"]["median"] >= m["first_pass_latency_ms"]["median"]
    for r in raws["C_parallel_targeted"].values():
        for x in r["runs"]:
            assert x["harness_action_ms"] >= x["wall_ms"] > 0


def test_verifier_accepts_balanced_run(runs):
    spec = importlib.util.spec_from_file_location("vr_mod", os.path.join(ROOT, "scripts", "verify_results.py"))
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    d = runs["balanced_warm"][0]
    assert vr.main([d]) == 0
    v = json.load(open(os.path.join(d, "verification.json"), encoding="utf-8"))
    assert v["arm_order"]["12"]["inconsistent_case_records"] == 0


def test_analysis_script_is_read_only_and_reports_paired_results(runs, tmp_path):
    d = runs["balanced_warm"][0]
    before = {f: hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest()
              for f in os.listdir(d) if f.endswith(".json")}
    spec = importlib.util.spec_from_file_location("an_mod", os.path.join(ROOT, "scripts", "analyze_experiment.py"))
    an = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(an)
    out = tmp_path / "analysis_out"
    assert an.main([d, "--out-dir", str(out), "--bootstrap", "500"]) == 0
    after = {f: hashlib.sha256(open(os.path.join(d, f), "rb").read()).hexdigest() for f in before}
    assert before == after                                                       # inputs untouched
    files = sorted(os.listdir(out))
    assert len(files) == 2 and files[0].endswith(".json") and files[1].endswith(".md")
    res = json.load(open(out / files[0], encoding="utf-8"))
    p = res["primary"]
    assert p["treatment"] == "C_parallel_targeted" and p["baseline"] == "C_decision_aware_full"
    raws = runs["balanced_warm"][2]
    t = sum(r["loop_stage_executions"] for r in raws["C_parallel_targeted"].values())
    b = sum(r["loop_stage_executions"] for r in raws["C_decision_aware_full"].values())
    assert p["outcomes"]["overall"]["treatment_total"] == t and p["outcomes"]["overall"]["baseline_total"] == b
    assert p["outcomes"]["overall"]["pooled"]["estimate"] == pytest.approx(1 - t / b, abs=1e-6)
    assert p["outcomes"]["overall"]["treatment_never_worse_units"] == p["outcomes"]["overall"]["n_units"]
    assert set(res["holm"]["primary"]["adjusted"]) == {f"C_parallel_targeted_vs_C_decision_aware_full.{o}" for o in A.OUTCOMES}
    assert res["exploratory_timing"]["C_decision_aware_full"]["status"].startswith("exploratory")
    assert res["method"]["bootstrap"]["seed"] == A.BOOTSTRAP_SEED


# ---------------------------------------------------------------------------
# strict alignment (audit B1 / I1 / patient ids)
# ---------------------------------------------------------------------------

def _loop_case(subject, actions):
    runs = [dict(action="INITIAL", stages_executed=["s"] * 11, correction_op=None, decision=None)]
    runs += [dict(action=a, stages_executed=["s"] * n, correction_op=op, decision=dec) for a, n, op, dec in actions]
    return dict(subject_id=subject, runs=runs, loop_stage_executions=sum(x[1] for x in actions))


def _aligned_run(tmp_path, t_raw, b_raw, seeds=("7",), labels=None):
    for s in seeds:
        gt = tmp_path / f"data_seed{s}" / "ground_truth"
        gt.mkdir(parents=True, exist_ok=True)
        rows = labels if labels is not None else {"1": "90000001", "2": "90000002"}
        (gt / "encounter_labels.csv").write_text("hadm_id,subject_id\n" + "".join(f"{h},{p}\n" for h, p in rows.items()))
    return dict(summary=dict(config=dict(seeds=[int(s) for s in seeds], clinical_source="SYNTHETIC")), dir=str(tmp_path),
                raws={"C_parallel_targeted": t_raw, "C_decision_aware_full": b_raw})


ACT = [("CORRECTION", 8, "update_bill_line", None), ("REVIEW_DECISION", 2, None, "ACCEPT_WITH_JUSTIFICATION")]
ACT_FULL = [("CORRECTION", 11, "update_bill_line", None), ("REVIEW_DECISION", 2, None, "ACCEPT_WITH_JUSTIFICATION")]


def _pair():
    t = {"7": {"1": _loop_case("90000001", ACT), "2": _loop_case("90000002", [])}}
    b = {"7": {"1": _loop_case("90000001", ACT_FULL), "2": _loop_case("90000002", [])}}
    return t, b


def test_complete_aligned_data_is_accepted_with_counts(tmp_path):
    t, b = _pair()
    run = _aligned_run(tmp_path, t, b)
    counts = A.validate_alignment(run, "C_parallel_targeted", "C_decision_aware_full")
    assert counts["7"]["expected"] == 2 and counts["7"]["C_parallel_targeted"] == 2 and counts["7"]["C_decision_aware_full"] == 2
    r = A.compare(run, "C_parallel_targeted", "C_decision_aware_full", B=200)
    assert r["outcomes"]["overall"]["treatment_total"] == 10 and r["outcomes"]["overall"]["baseline_total"] == 13
    assert r["completeness"]["7"]["expected"] == 2 and "verified identical" in r["alignment"]


def test_missing_encounter_fails_instead_of_being_dropped(tmp_path):
    t, b = _pair()
    del b["7"]["2"]
    with pytest.raises(A.AlignmentError, match=r"seed 7: encounter sets differ.*missing in C_decision_aware_full: \['2'\]"):
        A.compare(_aligned_run(tmp_path, t, b), "C_parallel_targeted", "C_decision_aware_full")


def test_encounter_missing_from_both_arms_but_in_cohort_fails(tmp_path):
    t, b = _pair()
    run = _aligned_run(tmp_path, t, b, labels={"1": "90000001", "2": "90000002", "3": "90000003"})
    with pytest.raises(A.AlignmentError, match=r"expected 3"):
        A.validate_alignment(run, "C_parallel_targeted", "C_decision_aware_full")


def test_missing_seed_fails(tmp_path):
    t, b = _pair()
    run = _aligned_run(tmp_path, t, b, seeds=("7", "8"))
    run["raws"]["C_parallel_targeted"]["8"] = t["7"]                 # seed 8 only in the treatment arm
    with pytest.raises(A.AlignmentError, match="seed sets differ"):
        A.validate_alignment(run, "C_parallel_targeted", "C_decision_aware_full")


def test_action_sequence_mismatch_fails_with_location(tmp_path):
    t, b = _pair()
    b["7"]["1"]["runs"][1]["correction_op"] = "delete_bill_line"
    with pytest.raises(A.AlignmentError, match=r"seed 7, encounter 1: oracle action sequences differ between "
                                               r"C_parallel_targeted and C_decision_aware_full"):
        A.validate_alignment(_aligned_run(tmp_path, t, b), "C_parallel_targeted", "C_decision_aware_full")
    t2, b2 = _pair()
    b2["7"]["1"]["runs"] = [b2["7"]["1"]["runs"][0], b2["7"]["1"]["runs"][2], b2["7"]["1"]["runs"][1]]   # order swapped
    with pytest.raises(A.AlignmentError, match="oracle action sequences differ"):
        A.validate_alignment(_aligned_run(tmp_path, t2, b2), "C_parallel_targeted", "C_decision_aware_full")


def test_patient_id_mismatch_fails(tmp_path):
    t, b = _pair()
    b["7"]["1"]["subject_id"] = "90000099"
    with pytest.raises(A.AlignmentError, match="subject_id disagrees"):
        A.validate_alignment(_aligned_run(tmp_path, t, b), "C_parallel_targeted", "C_decision_aware_full")
    t2, b2 = _pair()
    with pytest.raises(A.AlignmentError, match="subject_id disagrees"):           # arms agree, ground truth differs
        A.validate_alignment(_aligned_run(tmp_path, t2, b2, labels={"1": "1234", "2": "90000002"}),
                             "C_parallel_targeted", "C_decision_aware_full")


def test_duplicate_records_are_rejected(tmp_path):
    (tmp_path / "summary.json").write_text('{"config": {"seeds": [7]}}')
    (tmp_path / "raw_C_parallel_targeted_seed7.json").write_text('{"1": {"a": 1}, "1": {"a": 2}}')
    with pytest.raises(A.AlignmentError, match="duplicate key '1'"):
        A.load_run(str(tmp_path))


def test_wilcoxon_records_the_method_used():
    assert A.wilcoxon_paired([1, 2, 3, 4, 5, 6])["method_used"] == "exact"
    assert A.wilcoxon_paired([1, 2, 2, 3])["method_used"] == "approx"               # ties -> normal approximation
    assert A.wilcoxon_paired(list(range(1, 60)))["method_used"] == "approx"         # n > 50


def test_latency_reductions_are_labelled_exploratory(runs):
    d = runs["balanced_warm"][1]["derived"]
    assert not any("latency" in k for k in d if k != "EXPLORATORY_latency")
    assert d["EXPLORATORY_latency"]["status"].startswith("EXPLORATORY")


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_refuses_incomplete_run_and_writes_nothing(runs, tmp_path):
    import shutil
    d = str(tmp_path / "copy")
    shutil.copytree(runs["balanced_warm"][0], d)
    p = os.path.join(d, "raw_C_decision_aware_full_seed12.json")
    raw = json.load(open(p, encoding="utf-8"))
    raw.pop(next(iter(raw)))
    json.dump(raw, open(p, "w", encoding="utf-8"))
    out = tmp_path / "out"
    assert _load_script("analyze_experiment").main([d, "--out-dir", str(out), "--bootstrap", "100"]) == 2
    assert not out.exists() or not os.listdir(out)


def test_verifier_detects_cross_arm_action_mismatch(runs, tmp_path):
    import shutil
    d = str(tmp_path / "copy")
    shutil.copytree(runs["balanced_warm"][0], d)
    vr = _load_script("verify_results")
    assert vr.main([d]) == 0
    p = os.path.join(d, "raw_C_decision_aware_full_seed12.json")
    raw = json.load(open(p, encoding="utf-8"))
    case = next(r for r in raw.values() if any(x["action"] == "CORRECTION" for x in r["runs"]))
    next(x for x in case["runs"] if x["action"] == "CORRECTION")["correction_op"] = "tampered_op"
    json.dump(raw, open(p, "w", encoding="utf-8"))
    assert vr.main([d]) == 1
    v = json.load(open(os.path.join(d, "verification.json"), encoding="utf-8"))
    assert any("oracle action sequence differs" in x for x in v["completeness_problems"])
