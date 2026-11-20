"""
Full output equivalence between experiment arms (e.g. full vs targeted revalidation).

Per case the harness captures, at the end of pass 1 ("first_pass") and at the end of the
correction loop ("final"):
  status         case preparation status
  findings       every current finding: stage, code, severity, rule_id, title, explanation
                 (message), expected, actual, amount, evidence (all values), role, caused_by,
                 source_document, source_page
  tasks          every review task row of the case: task_key, code, severity, owner_stage,
                 input_domain, title, reason, evidence, recommended_action, required_reruns,
                 allowed_decisions, status, assignee, resolution_note, resolved_by
  stage_outputs  output hash of the CURRENT execution of every stage (hash of the complete stage
                 output: data + findings), i.e. all final pipeline outputs incl. the claim packet

Compared as multisets (order is not semantically meaningful for findings or tasks, so it is
ignored); multiplicity matters. Volatile fields are dropped before comparison (VOLATILE).

NOT compared (reported explicitly in every result):
  database IDs and timestamps            volatile by design
  intermediate states inside the loop    only end of pass 1 and end of loop are captured
  issue-memory rows (cc_issues/events)   detection counters depend on run history by design
  audit events                           contain timestamps and run ids
  latency                                measurement, not output
"""
import json
from collections import Counter, defaultdict
from typing import Dict, List

VOLATILE = {"id", "case_id", "stage_execution_id", "created_at", "updated_at", "resolved_at", "created_run_id",
            "run_id", "issue_id", "ts", "started_at", "finished_at"}
FINDING_FIELDS = ("stage", "code", "severity", "rule_id", "title", "message", "expected", "actual", "amount",
                  "evidence", "role", "caused_by", "source_document", "source_page")
TASK_FIELDS = ("task_key", "code", "severity", "owner_stage", "input_domain", "title", "reason", "evidence",
               "recommended_action", "required_reruns", "allowed_decisions", "status", "assignee",
               "resolution_note", "resolved_by")
IDENTITY_KEYS = ("line_no", "seq_num", "item", "document", "phrase", "field")
COMPARED = ["status (case preparation status)",
            "findings: code multiplicity, severity, rule_id, title, explanation, expected, actual, amount, "
            "evidence values, root/consequence role, caused_by, source citation",
            "review tasks: multiplicity, contents (reason, evidence, recommended action, reruns, allowed "
            "decisions) and status, assignee, resolution note, resolved_by",
            "stage_outputs: hash of every stage's complete current output (data + findings)"]
NOT_COMPARED = ["database IDs and timestamps (volatile by design)",
                "intermediate states between loop actions (only end of pass 1 and end of loop are captured)",
                "issue-memory rows (detection counters legitimately depend on run history)",
                "audit events (timestamps, run ids)", "latency (a measurement, not an output)"]


def canon(v) -> str:
    return json.dumps(v, sort_keys=True, default=str, separators=(",", ":"))


def strip_volatile(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if k in VOLATILE:
            continue
        out[k] = strip_volatile(v) if isinstance(v, dict) else v
    return out


# ---------------------------------------------------------------------------
# capture (database -> plain dicts)
# ---------------------------------------------------------------------------

def capture_case(db, case) -> dict:
    from app.clearclaim import dag
    from app.clearclaim.models import Finding, ReviewTask, StageExecution
    findings = [strip_volatile({f: getattr(x, f) for f in FINDING_FIELDS})
                for x in db.query(Finding).filter_by(case_id=case.id, is_current=True)]
    tasks = [strip_volatile({f: getattr(x, f) for f in TASK_FIELDS})
             for x in db.query(ReviewTask).filter_by(case_id=case.id)]
    cur = {s.stage: s.output_hash for s in db.query(StageExecution).filter_by(case_id=case.id, is_current=True)}
    return dict(status=case.status, findings=sorted(findings, key=canon), tasks=sorted(tasks, key=canon),
                stage_outputs={s: cur.get(s) for s in dag.STAGE_ORDER})


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------

def _finding_key(f):
    ev = f.get("evidence") or {}
    return (f.get("stage"), f.get("code")) + tuple(str(ev.get(k, "")) for k in IDENTITY_KEYS)


def _compare_items(ref, other, key_fn, fields, label):
    """Pair items by identity key; report multiplicity differences and per-field differences."""
    out = []
    ga, gb = defaultdict(list), defaultdict(list)
    for x in ref:
        ga[key_fn(x)].append(strip_volatile(x))
    for x in other:
        gb[key_fn(x)].append(strip_volatile(x))
    for k in sorted(set(ga) | set(gb), key=canon):
        a, b = sorted(ga.get(k, []), key=canon), sorted(gb.get(k, []), key=canon)
        if len(a) != len(b):
            out.append(dict(field=f"{label}.multiplicity", key=list(k), ref=len(a), other=len(b)))
            continue
        for xa, xb in zip(a, b):
            for f in fields:
                if canon(xa.get(f)) != canon(xb.get(f)):
                    out.append(dict(field=f"{label}.{f}", key=list(k), ref=xa.get(f), other=xb.get(f)))
    return out


def compare_phase(ref: dict, other: dict) -> List[dict]:
    diffs = []
    if ref.get("status") != other.get("status"):
        diffs.append(dict(field="status", ref=ref.get("status"), other=other.get("status")))
    diffs += _compare_items(ref.get("findings", []), other.get("findings", []), _finding_key, FINDING_FIELDS, "finding")
    diffs += _compare_items(ref.get("tasks", []), other.get("tasks", []), lambda t: (t.get("task_key"),),
                            TASK_FIELDS, "task")
    sa, sb = ref.get("stage_outputs") or {}, other.get("stage_outputs") or {}
    for s in sorted(set(sa) | set(sb)):
        if sa.get(s) != sb.get(s):
            diffs.append(dict(field=f"stage_output.{s}", ref=sa.get(s), other=sb.get(s)))
    return diffs


def compare_arms(raw_ref: Dict, raw_other: Dict, ref_name: str, other_name: str, phases=("first_pass", "final"),
                 keep=200) -> dict:
    mismatches, by_field = [], Counter()
    cases_diff = {p: 0 for p in phases}
    unavailable = 0
    only_ref = sorted(set(map(str, raw_ref)) - set(map(str, raw_other)))
    only_other = sorted(set(map(str, raw_other)) - set(map(str, raw_ref)))
    other_by = {str(k): v for k, v in raw_other.items()}
    for h, ra in sorted(((str(k), v) for k, v in raw_ref.items())):
        rb = other_by.get(h)
        if rb is None:
            continue
        oa, ob = ra.get("outputs"), rb.get("outputs")
        if not oa or not ob:
            unavailable += 1
            continue
        for p in phases:
            d = compare_phase(oa.get(p) or {}, ob.get(p) or {})
            if d:
                cases_diff[p] += 1
            for x in d:
                by_field[x["field"].split(".")[0] if x["field"].startswith("stage_output") else x["field"]] += 1
                mismatches.append(dict(hadm_id=h, phase=p, **x))
    n = len(set(map(str, raw_ref)) & set(map(str, raw_other))) - unavailable
    return dict(reference_arm=ref_name, other_arm=other_name, n_cases_compared=n, cases_without_outputs=unavailable,
                cases_only_in_reference=only_ref, cases_only_in_other=only_other,
                cases_with_differences=cases_diff, identical_all_phases=n - len({m["hadm_id"] for m in mismatches}),
                n_mismatches=len(mismatches), mismatches_by_field=dict(sorted(by_field.items())),
                mismatches=mismatches[:keep], mismatches_truncated=len(mismatches) > keep,
                equivalent=(n > 0 and not mismatches and not only_ref and not only_other),
                compared=COMPARED, not_compared=NOT_COMPARED)
