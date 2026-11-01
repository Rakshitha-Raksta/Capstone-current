"""
Policy and historical-case intelligence (read-only).

Rule explorer: the 59 cited rules from data/structured/policy_rule_candidates.csv (the same file the
seeder loads), joined with source-document metadata and, for a case, the rule results that the
eligibility and adjudication stages actually recorded in their current outputs. Nothing is
re-evaluated here: a rule with no recorded result is reported as such, never as a PASS.

Historical comparison: the peer group the historical stage uses (same MS-DRG, cc_historical_claims),
the modified z-scores recomputed with the stage's own function, and the stage's recorded output for
comparison. Historical claims are synthetic; similarity is advisory and is not a prediction.
"""
import csv
import os
import statistics
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.clearclaim.context import get_context
from app.clearclaim.models import ClaimCase, HistoricalClaim, MemberPolicy, StageExecution
from app.database.db import get_db
from app.clearclaim.stages.analysis import _modified_z

router = APIRouter(prefix="/api/v1/intel", tags=["clearclaim"])

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STRUCTURED = os.path.join(REPO_ROOT, "data", "structured")
DOC_FIELDS = ("document_id", "insurer", "product", "uin", "document_type", "version", "effective_from",
              "source_url", "source_domain", "official_source", "sha256", "page_count", "status")


@lru_cache(maxsize=1)
def _rules():
    with open(os.path.join(STRUCTURED, "policy_rule_candidates.csv"), encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


@lru_cache(maxsize=1)
def _documents():
    with open(os.path.join(STRUCTURED, "source_documents.csv"), encoding="utf-8") as fh:
        return {r["document_id"]: {k: r.get(k) for k in DOC_FIELDS} for r in csv.DictReader(fh)}


def _current_output(db, case_id, stage):
    x = db.query(StageExecution).filter_by(case_id=case_id, stage=stage, is_current=True).first()
    return ((x.output or {}).get("data") or {}) if x else None


def rule_catalog(db=None, case_id=None):
    docs = _documents()
    recorded, policy_version, stage_of = {}, None, {}
    if case_id is not None:
        c = db.get(ClaimCase, case_id)
        if c is None:
            raise KeyError(case_id)
        mp = db.query(MemberPolicy).filter_by(subject_id=c.encounter.subject_id).first()
        policy_version = mp and mp.policy_version_id
        for stage in ("eligibility", "adjudication"):
            for r in (_current_output(db, c.id, stage) or {}).get("rule_results", []):
                recorded.setdefault(r["rule_id"], []).append(r)
                stage_of[r["rule_id"]] = stage
    rows = []
    for r in _rules():
        rid = r["candidate_id"]
        item = dict(
            rule_id=rid, title=r["rule_name"], rule_type=r["rule_type"], insurer=r["insurer"], product=r["product"],
            uin=r["uin"], policy_version=r["policy_version"], condition=r["condition"], value=r["value"],
            unit=r["unit"], applies_to=r["applies_to"], exception=r["exception"],
            source_document=r["source_document"], source_page=r["source_page"], source_section=r["source_section"],
            source_text=r["source_text"], extraction_method=r["extraction_method"], confidence=r["confidence"],
            review_status=r["review_status"], provenance=r["provenance"],
            document=docs.get(r["source_document"]),
        )
        if case_id is not None:
            res = recorded.get(rid)
            if res:
                worst = max(res, key=lambda x: ["PASS", "WARNING", "PARTIAL_DEDUCTION", "FAIL"].index(x["severity"])
                            if x["severity"] in ("PASS", "WARNING", "PARTIAL_DEDUCTION", "FAIL") else 0)
                item["outcome"] = dict(status=worst["severity"], stage=stage_of[rid], results=res)
            elif r["policy_version"] != policy_version:
                item["outcome"] = dict(status="NOT_APPLICABLE",
                                       explanation=f"Rule belongs to policy version {r['policy_version']}; "
                                                   f"this encounter's policy is {policy_version or 'not linked'}.")
            elif r["review_status"] == "NEEDS_REVIEW":
                item["outcome"] = dict(status="QUARANTINED",
                                       explanation="Rule is flagged NEEDS_REVIEW and is not evaluated automatically.")
            else:
                item["outcome"] = dict(status="NO_RESULT",
                                       explanation="The evaluators recorded no result for this rule on this encounter "
                                                   "(its trigger condition did not apply). This is not a PASS.")
        rows.append(item)
    return dict(rules=rows, total=len(rows), policy_version=policy_version, case_id=case_id,
                documents=list(docs.values()),
                notes=["Outcomes are internal rule-based checks recorded by the eligibility and adjudication stages; "
                       "they are not insurer adjudication.",
                       "Citations come from data/structured/policy_rule_candidates.csv (document id, page, section "
                       "and the extracted source text)."])


def _pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))] if s else None


def historical_view(db, case_id, sample=40):
    c = db.get(ClaimCase, case_id)
    if c is None:
        raise KeyError(case_id)
    e = c.encounter
    ctx = get_context(db)
    recorded = _current_output(db, c.id, "historical")
    billing = _current_output(db, c.id, "billing") or {}
    dv = _current_output(db, c.id, "data_validation") or {}
    total, los = billing.get("lines_total"), dv.get("los_days")
    thr, min_peers = float(ctx.settings["historical_z_threshold"]), int(ctx.settings["historical_min_peers"])
    current = dict(hadm_id=e.hadm_id, drg_code=e.drg_code, drg_type=e.drg_type, drg_description=e.drg_description,
                   los_days=los, total_billed=total, claim_type=e.claim_admin and e.claim_admin.claim_type,
                   room_category=e.claim_admin and e.claim_admin.room_category, data_source=e.data_source)
    method = dict(
        name="Peer-group outlier screen (modified z-score, Iglewicz & Hoaglin 1993)",
        matching="Exact match on MS-DRG (HCFA) code. APR-DRG encounters are not compared because the numbering "
                 "schemes overlap with different meanings.",
        attributes=["drg_code (match key)", "total_billed (scored)", "los_days (scored)"],
        formula="z = 0.6745 * (x - median) / MAD",
        z_threshold=thr, min_peers=min_peers,
        limitations=["Historical claims are SYNTHETIC (generated by the project's seeded generator), not real "
                     "insurer outcomes.",
                     "Similarity is advisory: an unusual bill is not a wrong bill, and the screen predicts neither "
                     "approval nor rejection.",
                     "Only DRG, total and length of stay are used; diagnoses, procedures and severity are not.",
                     "There is no trained model; this is a robust statistical outlier test."])
    base = dict(case_id=c.id, current=current, method=method, recorded=recorded, peers=[], n_peers=0)
    if e.drg_type and e.drg_type != "HCFA":
        return dict(base, status="NOT_COMPARED", reason=f"Encounter has only an {e.drg_type} DRG.")
    rows = db.query(HistoricalClaim).filter_by(drg_code=e.drg_code).order_by(HistoricalClaim.id).all() if e.drg_code else []
    if len(rows) != sum(1 for h in ctx.historical if h["drg_code"] == e.drg_code):
        base["warning"] = "Historical table changed since the reference context was loaded."
    totals, stays = [r.total_billed for r in rows], [r.los_days for r in rows if r.los_days is not None]
    provenance = {}
    for r in rows:
        provenance[r.provenance] = provenance.get(r.provenance, 0) + 1
    out = dict(base, n_peers=len(rows), provenance=provenance)
    if len(rows) < min_peers:
        return dict(out, status="INSUFFICIENT_PEERS",
                    reason=f"Only {len(rows)} peer claim(s) for DRG {e.drg_code}; at least {min_peers} are required.")
    z_t, med_t, mad_t = _modified_z(total, totals) if total is not None else (None, None, None)
    z_l, med_l, mad_l = _modified_z(los, stays) if los is not None and stays else (None, None, None)

    def dist(v):
        return dict(min=min(v), p25=_pct(v, 0.25), median=statistics.median(v), p75=_pct(v, 0.75), max=max(v))

    # peers closest to the current case first (normalised distance on the two scored attributes)
    def gap(r):
        dt = abs(r.total_billed - total) / (mad_t or 1) if total is not None else 0
        dl = abs((r.los_days or 0) - los) / (mad_l or 1) if los is not None else 0
        return dt + dl
    near = sorted(rows, key=gap)[:sample]
    out.update(
        status="COMPARED",
        z_total=None if z_t is None else round(z_t, 3), z_los=None if z_l is None else round(z_l, 3),
        median_total=med_t, mad_total=mad_t, median_los=med_l, mad_los=mad_l,
        outlier=bool((z_t is not None and abs(z_t) > thr) or (z_l is not None and abs(z_l) > thr)),
        distribution=dict(total_billed=dist(totals), los_days=dist(stays) if stays else None),
        peers=[dict(hist_claim_id=r.hist_claim_id, drg_code=r.drg_code, claim_type=r.claim_type, los_days=r.los_days,
                    room_category=r.room_category, total_billed=r.total_billed, provenance=r.provenance,
                    diff_total=None if total is None else round(r.total_billed - total, 2),
                    diff_los=None if los is None or r.los_days is None else r.los_days - los,
                    same_claim_type=r.claim_type == current["claim_type"],
                    same_room=r.room_category == current["room_category"]) for r in near],
        matches_recorded=bool(recorded) and recorded.get("z_total") == (None if z_t is None else round(z_t, 3)),
        review_contribution=("An outlier raises the advisory HISTORICAL_OUTLIER finding. Reconciliation turns it into "
                             "CONFLICTING_ASSESSMENT only when billing found no line-level discrepancy. Advisory "
                             "findings never block a claim on their own."),
    )
    return out


RESEARCH_RUNS = [("main", "Synthetic · development (seeds 1-3)"), ("main_heldout", "Synthetic · held-out (seeds 4-6)"),
                 ("mimic_demo", "MIMIC-IV demo encounters · development"),
                 ("mimic_demo_heldout", "MIMIC-IV demo encounters · held-out")]
RESEARCH_METRICS = ["loop_stage_executions", "loop_unnecessary_executions", "loop_stage_reused", "tasks_created_total",
                    "issue_coverage_by_tasks.recall", "detection_all_findings.recall", "detection_all_findings.precision",
                    "root_blocker_exact_match.accuracy", "false_clear_rate", "first_pass_latency_ms.median",
                    "rerun_latency_ms.median"]


def research_summary():
    """Headline metrics of the recorded experiment runs, read from results/experiments/<run>/summary.json and
    verification.json exactly as written by scripts/run_experiments.py and scripts/verify_results.py."""
    import json
    base = os.path.join(REPO_ROOT, "results", "experiments")
    out = []
    for run, label in RESEARCH_RUNS:
        sp, vp = os.path.join(base, run, "summary.json"), os.path.join(base, run, "verification.json")
        if not os.path.exists(sp):
            out.append(dict(run=run, label=label, available=False))
            continue
        with open(sp, encoding="utf-8") as fh:
            s = json.load(fh)
        ver = None
        if os.path.exists(vp):
            with open(vp, encoding="utf-8") as fh:
                v = json.load(fh)
            ver = dict(mismatches=len(v.get("mismatches") or []), completeness_problems=len(v.get("completeness_problems") or []),
                       documented_source_anomalies=len(v.get("documented_source_anomalies") or []))
        agg = s.get("aggregate", {})
        arms = {arm: {m: agg[arm][m] for m in RESEARCH_METRICS if m in agg.get(arm, {})}
                for arm in ("B_sequential_full", "C_parallel_targeted", "C_minus_targeted", "C_minus_root_routing") if arm in agg}
        out.append(dict(run=run, label=label, available=True, clinical_source=s["config"].get("clinical_source"),
                        seeds=s["config"].get("seeds"), n_encounters=s["config"].get("n_encounters"), arms=arms,
                        verification=ver, path=f"results/experiments/{run}/summary.json"))
    return dict(runs=out, arms_legend=dict(
        B_sequential_full="Baseline: sequential execution, full re-run after every correction, root routing",
        C_parallel_targeted="Treatment: dependency-aware (parallel) execution, targeted re-run, root routing",
        C_minus_targeted="Ablation: treatment without targeted re-run",
        C_minus_root_routing="Ablation: treatment routing every finding instead of root blockers only"),
        notes=["Values are means (sd) over seeds as recorded; nothing is recomputed here.",
               "Latencies are local software execution times, not hospital turnaround times."])


@router.get("/research")
def research():
    return research_summary()


@router.get("/rules")
def rules(case_id: int = None, db: Session = Depends(get_db)):
    try:
        return rule_catalog(db, case_id)
    except KeyError:
        raise HTTPException(404, "case not found")


@router.get("/cases/{case_id}/historical")
def historical(case_id: int, sample: int = 40, db: Session = Depends(get_db)):
    try:
        return historical_view(db, case_id, sample=max(1, min(sample, 1000)))
    except KeyError:
        raise HTTPException(404, "case not found")
