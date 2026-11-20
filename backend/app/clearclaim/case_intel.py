"""
Historical-case intelligence (read-only, case-based retrieval; NOT a trained model).

For an encounter, retrieve the most similar previously processed encounters stored in this database,
show which features matched, what issues the deterministic pipeline found on them, and what reviewers
actually recorded about those issues (corrections, decisions). From that evidence it reports
descriptive frequencies and, where outcomes were recorded, previously successful resolution actions.

Safety contract
  * Outside the DAG. Nothing here writes findings, review tasks, issue records or case statuses, and
    nothing here is read by any stage. Deterministic findings stay authoritative.
  * Every output is labelled PREDICTION / HISTORICAL EVIDENCE. No insurer decision is produced.
  * No fabricated numbers: counts come from rows in the database; a likelihood is reported only with
    a valid denominator (>= MIN_COMPARABLE processed neighbours) and with its Wilson interval and the
    base rate; otherwise the item is INSUFFICIENT_DATA. Unreviewed issues are never counted as
    resolved or as failed.

Similarity
  Weighted feature similarity with explicit, documented per-feature functions. Weights are fixed
  design choices, not learned: for each kind of issue they put weight on the input domains that the
  deterministic check for that kind of issue reads (dag.py), e.g. billing checks read the bill,
  the procedures and the length of stay, so the "billing" profile weights those. Features that are
  undefined for a pair (e.g. neither encounter has procedures) are left out of that pair's
  denominator instead of counting as a match.

Prediction features use only the encounter's input data (diagnoses, procedures, DRG, stay, bill
structure, policy, administrative attributes), never its own findings, so a prediction does not
restate what the rules already found.
"""
import math
import threading
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.clearclaim.findings import TITLES
from app.clearclaim.models import (
    BillLine, ClaimAdmin, ClaimCase, Correction, Encounter, EncounterDiagnosis, EncounterProcedure, Finding,
    IssueEvent, MemberPolicy, PipelineRun, ReviewDecision, ReviewTask, TrackedIssue,
)
from app.database.db import get_db

MIN_CORPUS = 10          # fewer processed historical encounters than this -> INSUFFICIENT_DATA overall
MIN_COMPARABLE = 5       # neighbours needed before any frequency is reported as a likelihood
MIN_REVIEWED = 3         # reviewed historical outcomes needed before an outcome-based recommendation
ACTIONABLE = ("BLOCKING", "REVIEW")

FEATURES = ("diagnoses", "procedures", "drg", "length_of_stay", "bill", "policy", "admin")
FEATURE_LABELS = {"diagnoses": "Diagnoses (ICD-10 codes and categories)", "procedures": "Procedures (ICD codes)",
                  "drg": "MS-DRG", "length_of_stay": "Length of stay", "bill": "Bill items and category mix",
                  "policy": "Insurance policy version", "admin": "Claim type, room category, admission type"}

# Weight profiles. Each mirrors the input domains read by the deterministic stage that raises that kind
# of issue (dag.py): data_validation reads encounter/diagnoses/procedures/note; billing reads bill,
# procedures and LOS; discharge_readiness reads documents/checklist (administrative process);
# eligibility/adjudication read policy, claim_admin, encounter, diagnoses.
PROFILES: Dict[str, Dict[str, float]] = {
    "general":   dict(diagnoses=.25, procedures=.15, drg=.15, length_of_stay=.10, bill=.15, policy=.10, admin=.10),
    "coding":    dict(diagnoses=.40, procedures=.25, drg=.20, length_of_stay=.05, bill=.00, policy=.00, admin=.10),
    "billing":   dict(diagnoses=.05, procedures=.20, drg=.15, length_of_stay=.20, bill=.35, policy=.00, admin=.05),
    "readiness": dict(diagnoses=.05, procedures=.05, drg=.05, length_of_stay=.05, bill=.10, policy=.20, admin=.50),
    "insurance": dict(diagnoses=.15, procedures=.05, drg=.05, length_of_stay=.05, bill=.05, policy=.45, admin=.20),
}
DOMAIN_PROFILE = {"encounter": "coding", "diagnoses": "coding", "procedures": "coding", "discharge_note": "coding",
                  "documents": "readiness", "checklist": "readiness", "bill": "billing",
                  "claim_admin": "insurance", "policy": "insurance"}


def profile_for_code(code: str) -> str:
    from app.clearclaim.stages.analysis import owner_domain
    return DOMAIN_PROFILE.get(owner_domain(code), "general")


# ---------------------------------------------------------------------------
# feature extraction (input data only)
# ---------------------------------------------------------------------------

def _los(admit, disch):
    if admit is None or disch is None or disch < admit:
        return None
    return max(1, (disch.date() - admit.date()).days)        # same definition as the data_validation stage


def _features(enc, dx, px, lines, ca, pol):
    cat_amt = defaultdict(float)
    for l in lines:
        cat_amt[l.category or "OTHER"] += max(0.0, float(l.amount or 0))
    tot = sum(cat_amt.values())
    return dict(
        hadm_id=enc.hadm_id, encounter_id=enc.id, subject_id=enc.subject_id, data_source=enc.data_source,
        dx_codes=frozenset(d.icd_code for d in dx), dx_cats=frozenset(d.icd_code[:3] for d in dx),
        principal_cat=dx[0].icd_code[:3] if dx else None, principal=dx[0].icd_code if dx else None,
        px_codes=frozenset(p.icd_code for p in px),
        drg_code=enc.drg_code, drg_type=enc.drg_type, drg_description=enc.drg_description,
        los=_los(enc.admittime, enc.dischtime),
        bill_items=frozenset(l.item_code for l in lines),
        bill_mix={k: v / tot for k, v in cat_amt.items()} if tot > 0 else {},
        bill_total=round(sum(float(l.amount or 0) for l in lines), 2),
        policy_version=pol.policy_version_id if pol else None, has_policy=pol is not None,
        claim_type=ca.claim_type if ca else None, room_category=ca.room_category if ca else None,
        admission_type=enc.admission_type,
    )


def encounter_features(db, enc: Encounter) -> dict:
    pol = db.query(MemberPolicy).filter_by(subject_id=enc.subject_id).first()
    return _features(enc, list(enc.diagnoses), list(enc.procedures), [l for l in enc.bill_lines if not l.is_deleted],
                     enc.claim_admin, pol)


# ---------------------------------------------------------------------------
# per-feature similarity: (score in [0,1] or None when undefined for the pair, explanation)
# ---------------------------------------------------------------------------

def _jac(a, b):
    u = a | b
    return len(a & b) / len(u) if u else None


def _cos(a: dict, b: dict):
    if not a or not b:
        return None
    dot = sum(a[k] * b.get(k, 0.0) for k in a)
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else None


def feature_similarity(q: dict, h: dict) -> Dict[str, tuple]:
    out = {}
    if q["dx_codes"] and h["dx_codes"]:
        jc, jcat = _jac(q["dx_codes"], h["dx_codes"]), _jac(q["dx_cats"], h["dx_cats"])
        same_p = q["principal_cat"] == h["principal_cat"]
        s = 0.5 * jcat + 0.3 * jc + 0.2 * (1.0 if same_p else 0.0)
        shared = sorted(q["dx_codes"] & h["dx_codes"])
        shared_cat = sorted((q["dx_cats"] & h["dx_cats"]) - {c[:3] for c in shared})
        why = []
        if same_p:
            why.append(f"same principal diagnosis category {q['principal_cat']}")
        if shared:
            why.append(f"shared codes {', '.join(shared[:5])}")
        if shared_cat:
            why.append(f"shared categories {', '.join(shared_cat[:5])}")
        out["diagnoses"] = (s, "; ".join(why) or "no shared diagnosis codes or categories")
    else:
        out["diagnoses"] = (None, "diagnoses missing on one side")
    if not q["px_codes"] and not h["px_codes"]:
        out["procedures"] = (None, "neither encounter has coded procedures (not compared)")
    else:
        s = _jac(q["px_codes"], h["px_codes"])
        shared = sorted(q["px_codes"] & h["px_codes"])
        out["procedures"] = (s, f"shared procedures {', '.join(shared[:4])}" if shared else "no shared procedures")
    if q["drg_code"] and h["drg_code"] and q["drg_type"] == h["drg_type"]:
        same = q["drg_code"] == h["drg_code"]
        out["drg"] = (1.0 if same else 0.0, f"same {q['drg_type']} DRG {q['drg_code']}" if same else
                      f"different DRG ({q['drg_code']} vs {h['drg_code']})")
    else:
        out["drg"] = (None, "DRG missing or different DRG scheme (not compared)")
    if q["los"] is not None and h["los"] is not None:
        s = 1.0 - abs(q["los"] - h["los"]) / max(q["los"], h["los"], 1)
        out["length_of_stay"] = (s, f"{q['los']} vs {h['los']} day(s)")
    else:
        out["length_of_stay"] = (None, "length of stay unknown on one side")
    if q["bill_items"] and h["bill_items"]:
        j, c = _jac(q["bill_items"], h["bill_items"]), _cos(q["bill_mix"], h["bill_mix"])
        s = 0.5 * j + 0.5 * (c if c is not None else j)
        out["bill"] = (s, f"{len(q['bill_items'] & h['bill_items'])} of {len(q['bill_items'] | h['bill_items'])} "
                          f"item codes shared; category-mix cosine {c:.2f}" if c is not None else "item overlap only")
    else:
        out["bill"] = (None, "no bill lines on one side")
    if q["has_policy"] and h["has_policy"]:
        same = q["policy_version"] == h["policy_version"]
        out["policy"] = (1.0 if same else 0.0, f"same policy version {q['policy_version']}" if same else
                         f"different policy versions ({q['policy_version']} vs {h['policy_version']})")
    elif q["has_policy"] != h["has_policy"]:
        out["policy"] = (0.0, "policy linked on one side only")
    else:
        out["policy"] = (1.0, "no policy linked on either side")
    eq = [(k, q[k] == h[k]) for k in ("claim_type", "room_category", "admission_type") if q[k] and h[k]]
    if eq:
        out["admin"] = (sum(1 for _, e in eq if e) / len(eq),
                        ", ".join(f"{k.replace('_', ' ')} {'same' if e else 'differs'}" for k, e in eq))
    else:
        out["admin"] = (None, "administrative attributes missing")
    return out


def similarity(q: dict, h: dict, profile: str = "general") -> dict:
    w = PROFILES[profile]
    fs = feature_similarity(q, h)
    num = den = 0.0
    parts = []
    for f in FEATURES:
        s, why = fs[f]
        if w[f] <= 0:
            continue
        if s is None:
            parts.append(dict(feature=f, label=FEATURE_LABELS[f], weight=w[f], similarity=None, used=False, why=why))
            continue
        num += w[f] * s
        den += w[f]
        parts.append(dict(feature=f, label=FEATURE_LABELS[f], weight=w[f], similarity=round(s, 3), used=True, why=why))
    score = num / den if den else 0.0
    for p in parts:
        p["contribution"] = round(p["weight"] * p["similarity"] / den, 3) if p["used"] and den else 0.0
    ranked = sorted(parts, key=lambda p: -p["contribution"])
    matched = [p["why"] for p in ranked if p["used"] and (p["similarity"] or 0) > 0]
    differences = [p["why"] for p in ranked if p["used"] and (p["similarity"] or 0) == 0]
    return dict(score=round(score, 4), coverage=round(den / sum(w.values()), 3), features=parts, matched=matched,
                differences=differences)


# ---------------------------------------------------------------------------
# historical corpus: features + what was found + what reviewers recorded
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict = {}


def _corpus_version(db):
    return (db.query(func.count(ClaimCase.id), func.max(PipelineRun.id)).outerjoin(
        PipelineRun, PipelineRun.case_id == ClaimCase.id).one(),
        db.query(func.max(Correction.id)).scalar(), db.query(func.max(ReviewDecision.id)).scalar(),
        db.query(func.max(IssueEvent.id)).scalar(), db.query(func.max(ReviewTask.id)).scalar())


def build_corpus(db, use_cache=True) -> dict:
    ver = _corpus_version(db) if use_cache else None
    key = (str(db.get_bind().url), ver)
    if use_cache:
        with _cache_lock:
            if key in _cache:
                return _cache[key]
    cases = {c.encounter_id: c for c in db.query(ClaimCase)}
    processed = {cid for (cid,) in db.query(PipelineRun.case_id).filter(PipelineRun.status == "SUCCEEDED").distinct()}
    dx, px, lines = defaultdict(list), defaultdict(list), defaultdict(list)
    for d in db.query(EncounterDiagnosis).order_by(EncounterDiagnosis.encounter_id, EncounterDiagnosis.seq_num):
        dx[d.encounter_id].append(d)
    for p in db.query(EncounterProcedure).order_by(EncounterProcedure.encounter_id, EncounterProcedure.seq_num):
        px[p.encounter_id].append(p)
    for l in db.query(BillLine).filter(BillLine.is_deleted.is_(False)).order_by(BillLine.encounter_id, BillLine.line_no):
        lines[l.encounter_id].append(l)
    cas = {a.encounter_id: a for a in db.query(ClaimAdmin)}
    pols = {p.subject_id: p for p in db.query(MemberPolicy)}

    # issues observed: any actionable finding the deterministic pipeline ever recorded on the case
    observed = defaultdict(set)
    for cid, code in db.query(Finding.case_id, Finding.code).filter(Finding.severity.in_(ACTIONABLE)).distinct():
        observed[cid].add(code)

    # outcome per occurrence: tracked issues where they exist, else review tasks (pre-issue-memory rows)
    corr_by_id = {c.id: c for c in db.query(Correction)}
    corr_by_task = {}
    for c in corr_by_id.values():
        if c.review_task_id:
            corr_by_task.setdefault(c.review_task_id, c)
    occ = defaultdict(list)            # case_id -> [occurrence dicts]
    tasks_with_issue = set()
    for i in db.query(TrackedIssue):
        if i.task_id:
            tasks_with_issue.add(i.task_id)
        occ[i.case_id].append(_issue_outcome(i, corr_by_id))
    decisions = {d.task_id: d for d in db.query(ReviewDecision)}
    for t in db.query(ReviewTask):
        if t.issue_id is not None or t.id in tasks_with_issue:
            continue
        occ[t.case_id].append(_task_outcome(t, decisions.get(t.id), corr_by_task.get(t.id)))

    encs = {}
    for e in db.query(Encounter):
        c = cases.get(e.id)
        if c is None:
            continue
        f = _features(e, dx[e.id], px[e.id], lines[e.id], cas.get(e.id), pols.get(e.subject_id))
        f.update(case_id=c.id, case_ref=c.case_ref, processed=c.id in processed, status=c.status,
                 observed=frozenset(observed.get(c.id, ())), occurrences=occ.get(c.id, []))
        encs[c.id] = f
    corpus = dict(encounters=encs, n_corrections=len(corr_by_id), n_decisions=len(decisions),
                  data_sources=dict(Counter(f["data_source"] for f in encs.values())))
    if use_cache:
        with _cache_lock:
            _cache.clear()
            _cache[key] = corpus
    return corpus


def _issue_outcome(i: TrackedIssue, corr_by_id) -> dict:
    ra = i.resolution_action or {}
    if i.status == "RESOLVED" and ra.get("kind") == "CORRECTION":
        corr = corr_by_id.get(ra.get("correction_id"))
        return dict(code=i.code, outcome="RESOLVED", action=dict(kind="CORRECTION", op=ra.get("op") or (corr and corr.op),
                    domain=ra.get("domain") or (corr and corr.domain), correction_id=ra.get("correction_id")),
                    reopened=i.reopen_count or 0)
    if i.status == "DISMISSED":
        return dict(code=i.code, outcome="DISMISSED", action=dict(kind="DECISION", decision="ACCEPT_WITH_JUSTIFICATION"),
                    reopened=i.reopen_count or 0)
    if i.review_outcome == "CONFIRMED_BLOCKED":
        return dict(code=i.code, outcome="CONFIRMED_BLOCKED", action=dict(kind="DECISION", decision="CONFIRM_BLOCKED"),
                    reopened=i.reopen_count or 0)
    if i.status == "PENDING_REVIEW":
        return dict(code=i.code, outcome="UNREVIEWED", action=None, reopened=i.reopen_count or 0)
    if i.status == "NO_LONGER_DETECTED":
        return dict(code=i.code, outcome="UNKNOWN", action=None, reopened=i.reopen_count or 0)
    return dict(code=i.code, outcome="UNREVIEWED", action=None, reopened=i.reopen_count or 0)


def _task_outcome(t: ReviewTask, dec, corr) -> dict:
    if t.status == "RESOLVED_BY_CORRECTION":
        return dict(code=t.code, outcome="RESOLVED", reopened=0,
                    action=dict(kind="CORRECTION", op=corr.op if corr else None, domain=corr.domain if corr else None,
                                correction_id=corr.id if corr else None))
    if t.status == "ACCEPTED_WITH_JUSTIFICATION":
        return dict(code=t.code, outcome="DISMISSED", reopened=0, action=dict(kind="DECISION", decision="ACCEPT_WITH_JUSTIFICATION"))
    if t.status == "CONFIRMED_BLOCKED":
        return dict(code=t.code, outcome="CONFIRMED_BLOCKED", reopened=0, action=dict(kind="DECISION", decision="CONFIRM_BLOCKED"))
    if t.status == "SUPERSEDED":
        return dict(code=t.code, outcome="UNKNOWN", reopened=0, action=None)
    return dict(code=t.code, outcome="UNREVIEWED", reopened=0, action=None)


# ---------------------------------------------------------------------------
# retrieval, prediction, recommendation
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)


def neighbours(corpus, q: dict, profile="general", k=10, exclude=(), pool=None) -> List[dict]:
    excl = set(exclude)
    rows = []
    for cid, h in (pool if pool is not None else corpus["encounters"]).items():
        if cid in excl or not h["processed"]:
            continue
        rows.append((similarity(q, h, profile), h))
    rows.sort(key=lambda x: (-x[0]["score"], x[1]["case_id"]))
    return [dict(sim=s, h=h) for s, h in rows[:k]]


def _outcome_counts(hs, code):
    c = Counter()
    actions = Counter()
    for h in hs:
        for o in h["occurrences"]:
            if o["code"] != code:
                continue
            c[o["outcome"]] += 1
            if o["outcome"] == "RESOLVED" and o["action"] and o["action"].get("op"):
                actions[(o["action"]["op"], o["action"].get("domain"))] += 1
    return c, actions


def predict(corpus, q: dict, exclude=(), k_pred=25, current_codes=frozenset()) -> dict:
    eligible = [h for cid, h in corpus["encounters"].items() if h["processed"] and cid not in set(exclude)]
    n_hist = len(eligible)
    if n_hist < MIN_CORPUS:
        return dict(status="INSUFFICIENT_DATA", reason=f"only {n_hist} processed historical encounter(s); at least "
                    f"{MIN_CORPUS} are needed", items=[], n_historical=n_hist)
    prevalence = Counter()
    for h in eligible:
        prevalence.update(h["observed"])
    nb_by_profile = {p: neighbours(corpus, q, p, k_pred, exclude) for p in PROFILES if p != "general"}
    candidates = set()
    for nbs in nb_by_profile.values():
        for x in nbs:
            candidates |= x["h"]["observed"]
    items = []
    for code in sorted(candidates):
        prof = profile_for_code(code)
        nbs = nb_by_profile.get(prof) or neighbours(corpus, q, prof, k_pred, exclude)
        n = len(nbs)
        hits = [x for x in nbs if code in x["h"]["observed"]]
        base = prevalence[code] / n_hist
        item = dict(code=code, title=TITLES.get(code, code), profile=prof, n_comparable=n, n_observed=len(hits),
                    base_rate=round(base, 4), base_rate_n=n_hist,
                    deterministic_status="CONFIRMED_BY_RULE" if code in current_codes else "PREDICTION_ONLY",
                    label="Prediction only - not a verified finding" if code not in current_codes else
                          "Already found by a deterministic rule (the rule result is authoritative)")
        oc, _ = _outcome_counts([x["h"] for x in hits], code)
        item["historical_outcomes"] = dict(resolved=oc["RESOLVED"], dismissed=oc["DISMISSED"],
                                           confirmed_blocked=oc["CONFIRMED_BLOCKED"], unreviewed=oc["UNREVIEWED"],
                                           unknown=oc["UNKNOWN"])
        item["evidence"] = [dict(case_id=x["h"]["case_id"], case_ref=x["h"]["case_ref"], similarity=x["sim"]["score"])
                            for x in hits[:8]]
        item["mean_similarity_of_observed"] = round(sum(x["sim"]["score"] for x in hits) / len(hits), 3) if hits else None
        if n < MIN_COMPARABLE:
            item.update(status="INSUFFICIENT_DATA", likelihood=None, interval=None, signal=None,
                        reason=f"only {n} comparable processed encounter(s); {MIN_COMPARABLE} needed for a frequency")
        else:
            lo, hi = wilson(len(hits), n)
            signal = ("ELEVATED_VS_BASE_RATE" if lo > base else "LOWER_THAN_BASE_RATE" if hi < base
                      else "NOT_DISTINGUISHABLE_FROM_BASE_RATE")
            wsum = sum(x["sim"]["score"] for x in nbs)
            item.update(status="ESTIMATED", likelihood=round(len(hits) / n, 4), interval=[lo, hi], signal=signal,
                        similarity_weighted_frequency=round(sum(x["sim"]["score"] for x in hits) / wsum, 4) if wsum else None,
                        meaning=f"observed in {len(hits)} of the {n} most similar processed encounters "
                                f"(base rate {base:.1%} across {n_hist}); 95% Wilson interval {lo:.0%}-{hi:.0%}. "
                                f"Descriptive frequency, not a calibrated probability.")
        items.append(item)
    order = {"ELEVATED_VS_BASE_RATE": 0, "NOT_DISTINGUISHABLE_FROM_BASE_RATE": 1, "LOWER_THAN_BASE_RATE": 2, None: 3}
    items.sort(key=lambda i: (order[i.get("signal")], -(i.get("likelihood") or 0), i["code"]))
    predicted = [i["code"] for i in items if i.get("signal") == "ELEVATED_VS_BASE_RATE"]
    return dict(status="OK", items=items, predicted_codes=predicted, n_historical=n_hist, k=k_pred,
                rule="An issue category is 'predicted' only when the lower bound of its 95% Wilson interval among "
                     "the k most similar encounters exceeds its base rate across all processed encounters.")


def recommend(corpus, q: dict, codes, exclude=(), k_rec=10) -> List[dict]:
    from app.clearclaim.stages.analysis import NEXT_ACTIONS
    out = []
    for code in codes:
        prof = profile_for_code(code)
        pool = {cid: h for cid, h in corpus["encounters"].items() if code in h["observed"]}
        nbs = neighbours(corpus, q, prof, k_rec, exclude, pool=pool)
        hs = [x["h"] for x in nbs]
        oc, actions = _outcome_counts(hs, code)
        reviewed = oc["RESOLVED"] + oc["DISMISSED"] + oc["CONFIRMED_BLOCKED"]
        rec = dict(code=code, title=TITLES.get(code, code), profile=prof, n_similar_with_issue=len(nbs),
                   outcomes=dict(resolved=oc["RESOLVED"], dismissed=oc["DISMISSED"],
                                 confirmed_blocked=oc["CONFIRMED_BLOCKED"], unreviewed=oc["UNREVIEWED"],
                                 unknown=oc["UNKNOWN"]),
                   n_reviewed=reviewed, rule_guidance=NEXT_ACTIONS.get(code, "Review the evidence."),
                   rule_guidance_label="Deterministic rule guidance (not learned from history)",
                   evidence=[dict(case_id=x["h"]["case_id"], case_ref=x["h"]["case_ref"], similarity=x["sim"]["score"],
                                  outcomes=[o["outcome"] for o in x["h"]["occurrences"] if o["code"] == code],
                                  actions=[o["action"] for o in x["h"]["occurrences"] if o["code"] == code and o["action"]])
                             for x in nbs])
        if not nbs:
            rec.update(status="INSUFFICIENT_DATA", reason="no processed historical encounter has this issue")
        elif oc["RESOLVED"] == 0 or reviewed < MIN_REVIEWED:
            rec.update(status="INSUFFICIENT_DATA",
                       reason=(f"{len(nbs)} similar encounter(s) had this issue but only {reviewed} have a recorded review "
                               f"outcome ({oc['RESOLVED']} resolved by a correction); {oc['UNREVIEWED']} were never "
                               f"reviewed and {oc['UNKNOWN']} closed without a recorded correction. Unreviewed cases are "
                               f"not counted as resolved or as failed."))
        else:
            (op, dom), n_op = actions.most_common(1)[0]
            rec.update(status="RECOMMENDED", action=dict(kind="CORRECTION", op=op, domain=dom),
                       support=dict(resolved_with_action=n_op, resolved_total=oc["RESOLVED"], reviewed=reviewed),
                       explanation=(f"In {n_op} of {reviewed} reviewed similar encounters with this issue, the recorded "
                                    f"correction '{op}' on {dom} was followed by the issue no longer being detected. "
                                    f"{oc['UNREVIEWED']} other similar case(s) are unreviewed and not counted."),
                       all_actions=[dict(op=o, domain=d, resolved=n) for (o, d), n in actions.most_common()])
        out.append(rec)
    return out


def case_intelligence(db, case_id: int, k=10, k_pred=25, k_rec=10, corpus=None) -> dict:
    c = db.get(ClaimCase, case_id)
    if c is None:
        raise KeyError(case_id)
    corpus = corpus or build_corpus(db)
    q = corpus["encounters"].get(c.id) or encounter_features(db, c.encounter)
    current = db.query(Finding).filter_by(case_id=c.id, is_current=True).filter(Finding.severity.in_(ACTIONABLE)).all()
    current_codes = frozenset(f.code for f in current)
    active_issue_codes = sorted({i.code for i in db.query(TrackedIssue).filter_by(case_id=c.id, is_present=True)
                                 .filter(TrackedIssue.status.in_(("OPEN", "PENDING_REVIEW")))}
                                | {t.code for t in db.query(ReviewTask).filter_by(case_id=c.id, status="OPEN")})
    nbs = neighbours(corpus, q, "general", k, exclude=[c.id])
    eligible = sum(1 for cid, h in corpus["encounters"].items() if h["processed"] and cid != c.id)
    n_occ = Counter(o["outcome"] for cid, h in corpus["encounters"].items() if cid != c.id for o in h["occurrences"])
    similar = []
    for x in nbs:
        h = x["h"]
        similar.append(dict(case_id=h["case_id"], case_ref=h["case_ref"], hadm_id=h["hadm_id"], status=h["status"],
                            similarity=x["sim"]["score"], coverage=x["sim"]["coverage"], matched=x["sim"]["matched"],
                            differences=x["sim"]["differences"],
                            features=x["sim"]["features"], drg_code=h["drg_code"], drg_description=h["drg_description"],
                            los=h["los"], bill_total=h["bill_total"], policy_version=h["policy_version"],
                            issues_observed=sorted(h["observed"]),
                            issue_outcomes=[dict(code=o["code"], outcome=o["outcome"], action=o["action"])
                                            for o in h["occurrences"]]))
    pattern = Counter()
    for x in nbs:
        pattern.update(x["h"]["observed"])
    base = dict(
        case_id=c.id, kind="PREDICTION_AND_HISTORICAL_EVIDENCE",
        notice=("Historical intelligence is advisory. It never creates findings, review tasks or insurer decisions; "
                "deterministic rule results on this case are authoritative."),
        current=dict(case_ref=c.case_ref, hadm_id=q["hadm_id"], status=c.status, drg_code=q["drg_code"],
                     drg_description=q["drg_description"], los=q["los"], bill_total=q["bill_total"],
                     principal=q["principal"], n_diagnoses=len(q["dx_codes"]), n_procedures=len(q["px_codes"]),
                     policy_version=q["policy_version"], claim_type=q["claim_type"], room_category=q["room_category"],
                     data_source=q["data_source"], deterministic_issues=sorted(current_codes)),
        data_basis=dict(historical_encounters=eligible, data_sources=corpus["data_sources"],
                        recorded_corrections=corpus["n_corrections"], recorded_decisions=corpus["n_decisions"],
                        issue_occurrences_by_outcome=dict(n_occ),
                        reviewed_share=round((n_occ["RESOLVED"] + n_occ["DISMISSED"] + n_occ["CONFIRMED_BLOCKED"])
                                             / sum(n_occ.values()), 4) if n_occ else None),
        method=method_description(),
    )
    if eligible < MIN_CORPUS:
        return dict(base, status="INSUFFICIENT_DATA", similar=similar, predictions=None, recommendations=[],
                    reason=f"only {eligible} processed historical encounter(s); at least {MIN_CORPUS} are needed",
                    limitations=limitations(corpus, n_occ))
    pred = predict(corpus, q, exclude=[c.id], k_pred=k_pred, current_codes=current_codes)
    recs = recommend(corpus, q, active_issue_codes, exclude=[c.id], k_rec=k_rec)
    return dict(base, status="OK", similar=similar,
                issue_patterns=[dict(code=k_, title=TITLES.get(k_, k_), n_similar=n_, of=len(nbs)) for k_, n_ in pattern.most_common()],
                predictions=pred, recommendations=recs, limitations=limitations(corpus, n_occ))


def method_description() -> dict:
    return dict(
        name="Weighted structured nearest-neighbour retrieval (case-based reasoning; no trained model)",
        features={f: FEATURE_LABELS[f] for f in FEATURES},
        feature_similarity=dict(
            diagnoses="0.5 x Jaccard(3-character categories) + 0.3 x Jaccard(full codes) + 0.2 x [same principal category]",
            procedures="Jaccard(procedure codes); not compared when neither encounter has procedures",
            drg="1 if same DRG code within the same scheme, else 0; not compared across schemes",
            length_of_stay="1 - |a - b| / max(a, b)",
            bill="0.5 x Jaccard(item codes) + 0.5 x cosine(category share of billed amount)",
            policy="1 if same policy version (or both unlinked), 0 otherwise",
            admin="share of equal values among claim type, room category, admission type"),
        profiles=PROFILES,
        profile_basis="weights put on the input domains that the deterministic check for each kind of issue reads "
                      "(dag.py); fixed by design, not fitted to data",
        score="sum(weight x feature similarity) / sum(weights of features defined for the pair)",
        likelihood="observed frequency among the k most similar processed encounters with a 95% Wilson interval, "
                   f"reported only when k >= {MIN_COMPARABLE}; compared against the base rate",
        recommendation=f"most frequent recorded correction among similar encounters where the same issue was resolved, "
                       f"only when >= {MIN_REVIEWED} similar encounters have a recorded review outcome",
    )


def limitations(corpus, n_occ) -> List[str]:
    src = ", ".join(f"{k}: {v}" for k, v in corpus["data_sources"].items())
    unrev = n_occ["UNREVIEWED"] + n_occ["UNKNOWN"]
    total = sum(n_occ.values())
    return [
        f"Historical encounters are the ones stored in this database ({src}). In demo mode they are SYNTHETIC with "
        f"generator-injected errors, so similarity reflects the generator, not real hospital outcomes.",
        "This is retrieval over recorded cases, not a trained or learned model; weights are fixed design choices.",
        f"Outcomes are only what was recorded here: {corpus['n_corrections']} correction(s) and {corpus['n_decisions']} "
        f"decision(s). {unrev} of {total} historical issue occurrence(s) were never reviewed or closed without a "
        f"correction; they are excluded from success counts, never treated as resolved or failed.",
        "Frequencies describe the retrieved neighbours; the Wilson interval covers sampling uncertainty only and is "
        "not a calibrated probability. Different k gives different neighbours.",
        "Predictions never create findings or tasks and never change a case status or any insurer-facing output.",
    ]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/intel", tags=["clearclaim"])


@router.get("/cases/{case_id}/similar-cases")
def similar_cases(case_id: int, k: int = Query(10, ge=1, le=50), k_pred: int = Query(25, ge=5, le=100),
                  k_rec: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    try:
        return case_intelligence(db, case_id, k=k, k_pred=k_pred, k_rec=k_rec)
    except KeyError:
        raise HTTPException(404, "case not found")


@router.get("/similarity-method")
def similarity_method():
    return method_description()


EXTENSION_RESULTS = "results/experiments/intel_memory_v1/summary.json"


@router.get("/extension-results")
def extension_results():
    """Recorded evaluation of historical intelligence + issue memory, read exactly as written by
    scripts/run_intel_memory_experiments.py (nothing recomputed)."""
    import json
    import os
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    p = os.path.join(root, *EXTENSION_RESULTS.split("/"))
    if not os.path.exists(p):
        return dict(available=False, path=EXTENSION_RESULTS,
                    reason="Run scripts/run_intel_memory_experiments.py to produce this evaluation.")
    with open(p, encoding="utf-8") as fh:
        s = json.load(fh)
    return dict(available=True, path=EXTENSION_RESULTS, aggregate=s.get("aggregate"), config=s.get("config"),
                notes=s.get("notes"), elapsed_s=s.get("elapsed_s"))
