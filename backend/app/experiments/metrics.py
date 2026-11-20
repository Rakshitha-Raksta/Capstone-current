"""
Experiment metrics that are pure functions of per-case outputs and the answer key.

Root attribution (replaces the invalid root recall/F1 noted in the experiment audit, where the
root metric reused the all-findings false-negative count):

  Label set     the injected issue codes of a case (answer key). The answer key does not label
                causal roles separately; by construction of the generator every injected fault is
                an independent root (the generator excludes the fault pairs that would make one a
                consequence of another, synthetic.INCOMPATIBLE). This is a stated assumption.
  Prediction    registry codes the pipeline marked role=ROOT (codes outside the injectable
                registry are not part of the answer key and are excluded on both sides).

  root TP  = |injected ∩ predicted roots|
  root FP  = |predicted roots − injected|
  root FN  = |injected − predicted roots|        (independent of detection: an injected issue
                                                   found only as a CONSEQUENCE, or not at all,
                                                   is a root FN)
  precision = TP / (TP + FP), recall = TP / (TP + FN), F1 = harmonic mean;
  every ratio is None when its denominator is 0 (never 0 or 1 by convention).

  The FN is split into   found_as_consequence   (injected, found, but not as a root)
                         not_detected           (injected, not found at all)
  so the two failure modes are reported separately.

No top-1 / primary-root accuracy is computed: the per-case raw output does not record the
pipeline's primary root, and the answer key has no ranking of injected faults.
"""
from typing import Dict, Iterable, Optional


def ratio(num: int, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 4)


def prf(tp: int, fp: int, fn: int) -> dict:
    p, r = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = None if p is None or r is None else (0.0 if p + r == 0 else round(2 * p * r / (p + r), 4))
    return dict(tp=tp, fp=fp, fn=fn, precision=p, recall=r, f1=f1)


def root_attribution(cases: Iterable[dict], truth_by_case: Dict[str, set], registry: set) -> dict:
    """cases: iterable of dicts with hadm_id, findings, roots (code lists). truth_by_case: hadm_id -> injected codes."""
    tp = fp = fn = as_consequence = not_detected = 0
    exact = faulty = clean_with_root = 0
    for c in cases:
        truth = set(truth_by_case.get(str(c["hadm_id"]), set())) & registry
        roots = set(c["roots"]) & registry
        found = set(c["findings"]) & registry
        tp += len(truth & roots)
        fp += len(roots - truth)
        missed = truth - roots
        fn += len(missed)
        as_consequence += len(missed & found)
        not_detected += len(missed - found)
        if truth:
            faulty += 1
            exact += int(roots == truth)
        elif roots:
            clean_with_root += 1
    return dict(prf(tp, fp, fn), fn_found_as_consequence=as_consequence, fn_not_detected=not_detected,
                exact_set_match=dict(correct=exact, total=faulty, accuracy=ratio(exact, faulty)),
                clean_cases_with_a_root=clean_with_root,
                label_assumption="every injected fault is a root (answer key has no separate role labels)")
