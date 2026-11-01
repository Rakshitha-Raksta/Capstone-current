"""
Stages: historical comparison, reconciliation, root-blocker analysis,
review routing and claim preparation.
"""
import hashlib
import json
import statistics

from app.clearclaim.dag import STAGES, STAGE_INDEX, DOMAIN_OWNER, rerun_plan_for_domain
from app.clearclaim.findings import finding, SEVERITY_RANK
from app.datasets.error_types import ERROR_TYPES

# ---------------------------------------------------------------------------
# historical comparison (advisory only)
# ---------------------------------------------------------------------------


def _modified_z(x, values):
    """Iglewicz & Hoaglin (1993) modified z-score: 0.6745 (x - median) / MAD."""
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    if mad == 0:
        return 0.0, med, mad
    return 0.6745 * (x - med) / mad, med, mad


def run_historical(inp, up, ctx):
    enc = inp["encounter"]
    total = up["billing"]["lines_total"]
    los = up["data_validation"]["los_days"]
    thr = float(ctx.settings["historical_z_threshold"])
    min_peers = int(ctx.settings["historical_min_peers"])
    if enc.get("drg_type") and enc["drg_type"] != "HCFA":
        # APR-DRG and MS-DRG numbering overlap but mean different things; peers are MS-DRG only.
        return dict(data=dict(peer_group=None, n_peers=0, outlier=False, z_total=None, z_los=None),
                    findings=[finding("NO_COMPARABLE_DRG", "INFO",
                                      f"Encounter has only an {enc['drg_type']} DRG; historical peers are grouped by "
                                      f"MS-DRG (HCFA), so no comparison was made.", drg_type=enc["drg_type"])])
    peers = [h for h in ctx.historical if enc.get("drg_code") and h["drg_code"] == enc["drg_code"]]
    group = f"DRG {enc.get('drg_code')}"
    if len(peers) < min_peers:
        f = [finding("INSUFFICIENT_PEERS", "INFO",
                     f"Only {len(peers)} historical peer claim(s) for {group}; comparison not performed.",
                     peers=len(peers), min_peers=min_peers)]
        return dict(data=dict(peer_group=group, n_peers=len(peers), outlier=False, z_total=None, z_los=None), findings=f)
    z_t, med_t, mad_t = _modified_z(total, [p["total_billed"] for p in peers])
    z_l, med_l, mad_l = (None, None, None)
    if los is not None:
        z_l, med_l, mad_l = _modified_z(los, [p["los_days"] for p in peers])
    outlier = abs(z_t) > thr or (z_l is not None and abs(z_l) > thr)
    f = []
    if outlier:
        f.append(finding("HISTORICAL_OUTLIER", "ADVISORY",
                         f"Bill total {total:.0f} (modified z {z_t:+.1f}) and length of stay {los} "
                         f"(modified z {z_l:+.1f}) compared with {len(peers)} historical {group} claims "
                         f"(median total {med_t:.0f}, median LOS {med_l}). Advisory only - unusual is not wrong."
                         if z_l is not None else
                         f"Bill total {total:.0f} has modified z {z_t:+.1f} against {len(peers)} {group} claims.",
                         expected=f"|z| <= {thr}", actual=f"z_total={z_t:.2f}, z_los={z_l if z_l is None else round(z_l, 2)}",
                         peer_group=group, n_peers=len(peers), median_total=med_t, mad_total=mad_t,
                         z_total=round(z_t, 3), z_los=None if z_l is None else round(z_l, 3), z_threshold=thr,
                         median_los=med_l, method="modified z-score (Iglewicz & Hoaglin 1993)"))
    return dict(data=dict(peer_group=group, n_peers=len(peers), outlier=outlier, z_total=round(z_t, 3),
                          z_los=None if z_l is None else round(z_l, 3), median_total=med_t, median_los=med_l),
                findings=f)


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------

def run_reconciliation(inp, up, ctx):
    f = []
    bill = up["billing"]
    adj = up["adjudication"]
    header = inp["bill"]["header_total"]
    lines_total = bill["lines_total"]
    if header is not None and abs(float(header) - lines_total) > 1.0:
        diff = round(float(header) - lines_total, 2)
        f.append(finding("RECONCILIATION_RESIDUAL", "BLOCKING",
                         f"Bill header total {float(header):.2f} differs from the sum of bill lines "
                         f"{lines_total:.2f} by {diff:+.2f}.", expected=lines_total, actual=header, amount=abs(diff)))
    steps = adj["waterfall"]
    explained = sum(s["amount"] for s in steps if s["step"] not in ("gross_bill", "estimated_eligible"))
    residual = round(steps[0]["amount"] + explained - adj["payable_if_eligible"], 2)
    if abs(residual) > 0.5:
        f.append(finding("WATERFALL_RESIDUAL", "BLOCKING",
                         f"Financial waterfall does not balance: unexplained residual {residual:+.2f}.",
                         expected=0, actual=residual, amount=abs(residual)))
    billing_blockers = [x for x in up["billing#findings"] if x["severity"] == "BLOCKING"]
    if billing_blockers:
        f.append(finding("ADJUDICATION_ON_DISPUTED_BILL", "REVIEW",
                         f"The coverage estimate was computed on a bill with {len(billing_blockers)} unresolved "
                         f"billing discrepanc{'y' if len(billing_blockers) == 1 else 'ies'}; it will change after "
                         f"billing is corrected.", amount=bill["disputed_amount"]))
    if up["historical"]["outlier"] and not billing_blockers:
        f.append(finding("CONFLICTING_ASSESSMENT", "ADVISORY",
                         "Billing verification found no line-level discrepancy, but the total is unusual versus "
                         "historical peers. This can be a legitimate complex case; check the documented hospital course."))
    return dict(data=dict(header_total=header, lines_total=lines_total, waterfall_residual=residual,
                          waterfall=steps, balanced=abs(residual) <= 0.5), findings=f)


# ---------------------------------------------------------------------------
# root-blocker analysis
# ---------------------------------------------------------------------------

BILLING_ROOTS = ["LOS_BILLING_MISMATCH", "DUPLICATE_BILL_ITEM", "BILL_ARITHMETIC_ERROR", "UNIT_PRICE_ABOVE_TARIFF",
                 "BILLED_PROCEDURE_NOT_DOCUMENTED"]

# ---------------------------------------------------------------------------
# Explicit causal rules: (consequence code, root code, condition, why)
# A finding is a CONSEQUENCE only if a present root finding satisfies a rule's
# condition on the two findings' evidence. Each rule is unit-tested
# (tests/test_root_blocker.py) and listed in docs/ROOT_BLOCKER_RULES.md.
# ---------------------------------------------------------------------------

def _ev(x, k, default=None):
    return (x.get("evidence") or {}).get(k, default)


def _always(cons, root):
    return True


CAUSAL_RULES = [
    ("LOS_NOT_VERIFIABLE", "MISSING_DISCHARGE_TIME", _always,
     "length of stay needs the discharge time"),
    ("LOS_NOT_VERIFIABLE", "DISCHARGE_BEFORE_ADMISSION", _always,
     "length of stay is undefined when discharge precedes admission"),
    ("LOS_NOT_VERIFIABLE", "MISSING_REQUIRED_FIELD", lambda c, r: _ev(r, "field") == "admittime",
     "length of stay needs the admission time"),
    ("RULE_NOT_EVALUABLE", "MISSING_DISCHARGE_TIME", lambda c, r: _ev(c, "rule_category") == "CLAIM_FILING_DEADLINE",
     "the filing-deadline rule is measured from the discharge date"),
    ("RULE_NOT_EVALUABLE", "MISSING_REQUIRED_FIELD", lambda c, r: _ev(r, "field") == "admittime",
     "date-based rules need the admission date"),
    ("RULE_NOT_EVALUABLE", "MISSING_SOURCE_RECORD", lambda c, r: _ev(r, "record") == "claim_admin",
     "claim administration dates were never imported"),
    ("NOTE_DX_NOT_CODED", "INVALID_ICD_CODE",
     lambda c, r: _ev(r, "icd_code") in (_ev(c, "possible_cause_codes") or []),
     "the note phrase matches a valid code in the invalid code's own ICD-10-CM category"),
    ("CODED_DX_NOT_IN_NOTE", "INVALID_ICD_CODE", lambda c, r: _ev(c, "seq_num") == _ev(r, "seq_num"),
     "the principal diagnosis code itself is invalid"),
    ("ADJUDICATION_ON_DISPUTED_BILL", "*BILLING_ROOT", lambda c, r: r.get("severity") == "BLOCKING",
     "the coverage estimate used a bill with an unresolved blocking discrepancy"),
    ("HISTORICAL_OUTLIER", "*BILLING_ROOT",
     lambda c, r: r.get("severity") == "BLOCKING" and _ev(c, "z_total") is not None
     and abs(_ev(c, "z_total")) > _ev(c, "z_threshold", 3.5),
     "the bill total is inflated by a blocking billing discrepancy"),
    ("HISTORICAL_OUTLIER", "MISSING_DISCHARGE_TIME", lambda c, r: True,
     "comparison used an unverifiable stay"),
    ("MISSING_KYC_DOCUMENT", "*BILLING_ROOT",
     lambda c, r: r.get("severity") == "BLOCKING" and _ev(c, "kyc_threshold") is not None
     and _ev(c, "verified_total") is not None and _ev(c, "verified_total") <= _ev(c, "kyc_threshold"),
     "the KYC threshold is crossed only because of over-billing; the verified bill is below it"),
]


def _root_matches(rule_root, code):
    return code in BILLING_ROOTS if rule_root == "*BILLING_ROOT" else code == rule_root


def explain(cons, candidates):
    """Return (root finding, reason) for the first rule that explains `cons`, or (None, None)."""
    for c_code, r_code, cond, why in CAUSAL_RULES:
        if c_code != cons["code"]:
            continue
        for r in candidates:
            if r is cons or not _root_matches(r_code, r["code"]):
                continue
            if cond(cons, r):
                return r, why
    return None, None


# owner input domain for codes not in the injectable registry
EXTRA_DOMAINS = {
    "MISSING_SOURCE_RECORD": "bill", "MISSING_REQUIRED_FIELD": "encounter", "DISCHARGE_BEFORE_ADMISSION": "encounter",
    "DISCHARGE_NOTE_MISSING": "discharge_note", "UNKNOWN_BILL_ITEM": "bill", "ICD_CODE_VERSION_MISMATCH": "diagnoses", "LOS_NOT_VERIFIABLE": "encounter",
    "RULE_NOT_EVALUABLE": "claim_admin", "EXCLUSION_KEYWORD_MATCH": "diagnoses", "RULES_QUARANTINED": "policy",
    "NO_MEMBER_POLICY": "policy", "POLICY_EXPIRED": "policy", "WATERFALL_RESIDUAL": "bill",
    "ADJUDICATION_ON_DISPUTED_BILL": "bill", "HISTORICAL_OUTLIER": "bill", "CONFLICTING_ASSESSMENT": "bill",
}

NEXT_ACTIONS = {
    "MISSING_DISCHARGE_TIME": "Record the discharge date and time in the encounter.",
    "MISSING_REQUIRED_FIELD": "Complete the missing encounter field.",
    "DISCHARGE_BEFORE_ADMISSION": "Correct the admission or discharge timestamp.",
    "INVALID_ICD_CODE": "Replace the code with a valid, fully specified ICD-10-CM code.",
    "GENDER_DX_CONFLICT": "Verify the patient's recorded sex and the sex-specific diagnosis; remove or correct the code.",
    "PROCEDURE_DATE_OUT_OF_RANGE": "Correct the procedure date to fall within the admission.",
    "NOTE_DX_NOT_CODED": "Coder review: add the documented diagnosis code, or amend the note if it is wrong.",
    "CODED_DX_NOT_IN_NOTE": "Ask the treating team to document the principal diagnosis in the discharge note.",
    "DISCHARGE_NOTE_MISSING": "Attach the discharge summary.",
    "MISSING_CLAIM_DOCUMENT": "Collect and attach the missing claim document.",
    "READINESS_ITEM_INCOMPLETE": "Complete the pending discharge checklist item.",
    "LOS_BILLING_MISMATCH": "Correct the room/ICU day quantity to the length of stay.",
    "BILLED_PROCEDURE_NOT_DOCUMENTED": "Remove the charge, or add the missing procedure documentation.",
    "MISSING_CHARGE": "Add the bill line for the documented procedure (or confirm it is packaged).",
    "DUPLICATE_BILL_ITEM": "Remove the duplicate bill line.",
    "BILL_ARITHMETIC_ERROR": "Correct the line amount to quantity x unit price.",
    "UNIT_PRICE_ABOVE_TARIFF": "Reprice the line at the rate card price.",
    "UNKNOWN_BILL_ITEM": "Map the item to a rate card entry.",
    "RECONCILIATION_RESIDUAL": "Re-issue the bill so that the header total equals the sum of lines.",
    "WATERFALL_RESIDUAL": "System defect: report to the ClearClaim team.",
    "WAITING_PERIOD_NOT_MET": "Policy waiting period not met: confirm the claim cannot be prepared under this policy.",
    "POLICY_INACTIVE": "Policy cancelled before admission: confirm with the insurance desk.",
    "POLICY_EXPIRED": "Policy expired before admission: confirm with the insurance desk.",
    "NO_MEMBER_POLICY": "Link the patient's insurance policy.",
    "LATE_FILING": "Record the reason for delay; insurers may condone genuine delay.",
    "LATE_NOTIFICATION": "Record the reason for late notification.",
    "PREAUTH_LEAD_TIME_SHORT": "Record the reason pre-authorisation was requested late.",
    "EXCLUSION_KEYWORD_MATCH": "Check the cited exclusion text against the actual treatment.",
    "RULES_QUARANTINED": "Policy rules are under source review; a reviewer must check the policy terms manually.",
    "RULE_NOT_EVALUABLE": "Provide the missing claim field so the cited rule can be evaluated.",
    "MISSING_KYC_DOCUMENT": "Collect KYC documents (required above the policy threshold).",
    "ADJUDICATION_ON_DISPUTED_BILL": "Resolve the billing discrepancy; the estimate updates automatically.",
    "MISSING_SOURCE_RECORD": "Import the missing source record.",
}


def owner_domain(code):
    if code in ERROR_TYPES:
        return ERROR_TYPES[code][2]
    return EXTRA_DOMAINS.get(code, "encounter")


def correctable(code):
    if code in ERROR_TYPES:
        return ERROR_TYPES[code][3]
    return code not in ("RULES_QUARANTINED", "EXCLUSION_KEYWORD_MATCH", "WATERFALL_RESIDUAL", "POLICY_EXPIRED")


def run_root_blocker(inp, up, ctx):
    all_f = []
    for s in STAGES:
        if s in up and isinstance(up[s], list):
            for x in up[s]:
                all_f.append(dict(x, stage=s))
    roots, consequences, advisories = [], [], []
    for x in all_f:
        cause, why = explain(x, all_f)
        if x["severity"] in ("ADVISORY", "INFO") and cause is None:
            advisories.append(dict(x, role="ADVISORY" if x["severity"] == "ADVISORY" else "INFO"))
        elif cause is not None:
            consequences.append(dict(x, role="CONSEQUENCE", caused_by=cause["code"], causal_rule=why,
                                     caused_by_stage=cause["stage"]))
        else:
            dom = owner_domain(x["code"])
            plan = rerun_plan_for_domain(dom)
            roots.append(dict(x, role="ROOT", owner_domain=dom, owner_stage=DOMAIN_OWNER.get(dom, x["stage"]),
                              owner_team=STAGES[DOMAIN_OWNER.get(dom, x["stage"])]["owner"],
                              rerun_stages=plan, correctable=correctable(x["code"]),
                              next_action=NEXT_ACTIONS.get(x["code"], "Review the evidence.")))
    roots.sort(key=lambda r: (-SEVERITY_RANK[r["severity"]], STAGE_INDEX[r["stage"]], r["code"]))
    for c in consequences:
        c["downstream_of_stage"] = next((r["stage"] for r in roots if r["code"] == c["caused_by"]), None)
    primary = roots[0] if roots else None
    amount_at_risk = round(sum((r.get("amount") or 0) for r in roots if r["severity"] == "BLOCKING"), 2)
    return dict(data=dict(roots=roots, consequences=consequences, advisories=advisories,
                          primary_root=primary and dict(code=primary["code"], stage=primary["stage"],
                                                        severity=primary["severity"], title=primary["title"],
                                                        owner_stage=primary["owner_stage"]),
                          amount_at_risk=amount_at_risk,
                          counts=dict(roots=len(roots), consequences=len(consequences), advisories=len(advisories))),
                findings=[])


# ---------------------------------------------------------------------------
# review routing
# ---------------------------------------------------------------------------

IDENTITY_KEYS = ("line_no", "seq_num", "item", "document", "phrase", "field")


def _identity(x):
    ev = x.get("evidence") or {}
    return (x["code"],) + tuple(str(ev.get(k, "")) for k in IDENTITY_KEYS)


def task_key(x, rule_ids=None):
    rules = sorted(rule_ids if rule_ids is not None else [x.get("rule_id") or ""])
    ident = list(_identity(x)) + rules
    return hashlib.sha1("|".join(ident).encode()).hexdigest()[:16] + ":" + x["code"]


def group_findings(findings):
    """One review task per distinct issue: findings that differ only in which cited rule
    fired (e.g. initial + PED + specified-disease waiting periods for the same admission)
    are merged, keeping every rule citation. Findings with their own identity (bill line,
    diagnosis, document, checklist item) stay separate because each needs its own fix."""
    groups = {}
    for x in findings:
        groups.setdefault(_identity(x), []).append(x)
    out = []
    for members in groups.values():
        head = dict(members[0])
        head["group_rules"] = [dict(rule_id=m.get("rule_id"), source_document=m.get("source_document"),
                                    source_page=m.get("source_page"), expected=m.get("expected"),
                                    actual=m.get("actual"), message=m["message"]) for m in members]
        if len(members) > 1:
            head["message"] = f"{len(members)} cited rules raise this issue. " + members[0]["message"]
        out.append(head)
    return out


def run_review_routing(inp, up, ctx):
    rb = up["root_blocker"]
    policy = ctx.settings["routing_policy"]
    if policy == "all_findings":
        candidates = rb["roots"] + rb["consequences"]
    else:
        candidates = rb["roots"]
    decisions = {}
    for d in inp["reviews"]:
        decisions[d["task_key"]] = d["decision"]
    tasks = []
    for x in group_findings([c for c in candidates if c["severity"] in ("BLOCKING", "REVIEW")]):
        rule_ids = [r["rule_id"] or "" for r in x["group_rules"]]
        key = task_key(x, rule_ids)
        corr = x.get("correctable", correctable(x["code"]))
        if x["severity"] == "BLOCKING" and corr:
            allowed = ["CORRECT_DATA"]
        elif x["severity"] == "BLOCKING":
            allowed = ["CONFIRM_BLOCKED"]
        else:
            allowed = ["ACCEPT_WITH_JUSTIFICATION"] + (["CORRECT_DATA"] if corr else [])
        state = {"ACCEPT_WITH_JUSTIFICATION": "ACCEPTED_WITH_JUSTIFICATION",
                 "CONFIRM_BLOCKED": "CONFIRMED_BLOCKED"}.get(decisions.get(key), "OPEN")
        dom = x.get("owner_domain") or owner_domain(x["code"])
        tasks.append(dict(task_key=key, code=x["code"], severity=x["severity"], title=x["title"], role=x.get("role"),
                          stage=x["stage"], owner_stage=x.get("owner_stage") or DOMAIN_OWNER.get(dom, x["stage"]),
                          input_domain=dom, reason=x["message"],
                          evidence=dict(rule_id=x.get("rule_id"), source_document=x.get("source_document"),
                                        source_page=x.get("source_page"), expected=x.get("expected"),
                                        actual=x.get("actual"), amount=x.get("amount"),
                                        rules=x["group_rules"] if len(x["group_rules"]) > 1 else None,
                                        **(x.get("evidence") or {})),
                          recommended_action=x.get("next_action") or NEXT_ACTIONS.get(x["code"], "Review the evidence."),
                          required_reruns=x.get("rerun_stages") or rerun_plan_for_domain(dom),
                          allowed_decisions=allowed, state=state))
    open_tasks = [t for t in tasks if t["state"] == "OPEN"]
    return dict(data=dict(routing_policy=policy, tasks=tasks, open_count=len(open_tasks),
                          confirmed_blocked=sum(1 for t in tasks if t["state"] == "CONFIRMED_BLOCKED")),
                findings=[])


# ---------------------------------------------------------------------------
# claim preparation packet
# ---------------------------------------------------------------------------

def run_claim_generation(inp, up, ctx):
    rr = up["review_routing"]
    rb = up["root_blocker"]
    adj = up["adjudication"]
    tasks = rr["tasks"]
    accepted = {t["code"] for t in tasks if t["state"] == "ACCEPTED_WITH_JUSTIFICATION"}
    blocking_unresolved = [t for t in tasks if t["severity"] == "BLOCKING" and t["state"] != "ACCEPTED_WITH_JUSTIFICATION"]
    # Safety: a BLOCKING finding blocks preparation even when it is not routed as its own task
    # (a root without a task, or a consequence), unless its root was explicitly accepted.
    blocking_unrouted = ([r for r in rb["roots"] if r["severity"] == "BLOCKING" and r["code"] not in accepted
                          and not any(t["code"] == r["code"] for t in tasks)]
                         + [c for c in rb.get("consequences", []) if c["severity"] == "BLOCKING"
                            and c.get("caused_by") not in accepted])
    if blocking_unresolved or blocking_unrouted:
        status = "BLOCKED"
    elif any(t["state"] == "OPEN" for t in tasks):
        status = "NEEDS_REVIEW"
    else:
        status = "PREPARATION_COMPLETE"

    enc, pol = inp["encounter"], inp["policy"] or {}
    lines = inp["bill"]["lines"]
    packet = {
        "resourceType": "Claim",
        "meta": {"profile": [], "tag": [{"code": "FHIR-SHAPED-NOT-VALIDATED"},
                                         {"code": f"DATA-{enc['data_source']}"}]},
        "status": "draft" if status != "PREPARATION_COMPLETE" else "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "institutional"}]},
        "use": "claim",
        "patient": {"reference": f"Patient/{enc['subject_id']}", "display": "de-identified"},
        "billablePeriod": {"start": enc["admittime"], "end": enc["dischtime"]},
        "provider": {"display": "Demo hospital (synthetic)"},
        "priority": {"coding": [{"code": "normal"}]},
        "insurance": [{"sequence": 1, "focal": True,
                       "coverage": {"display": f"{pol.get('policy_version_id')} / UIN {pol.get('uin')}"}}],
        "diagnosis": [{"sequence": i + 1, "diagnosisCodeableConcept": {"coding": [{
            "system": "http://hl7.org/fhir/sid/icd-10-cm" if d["icd_version"] == 10 else "http://hl7.org/fhir/sid/icd-9-cm",
            "code": d["icd_code"], "display": d["long_title"]}]}} for i, d in enumerate(inp["diagnoses"])],
        "procedure": [{"sequence": i + 1, "date": p["chartdate"], "procedureCodeableConcept": {"coding": [{
            "system": "http://www.cms.gov/Medicare/Coding/ICD10" if p["icd_version"] == 10 else "http://hl7.org/fhir/sid/icd-9-cm",
            "code": p["icd_code"], "display": p["long_title"]}]}} for i, p in enumerate(inp["procedures"])],
        "item": [{"sequence": l["line_no"], "productOrService": {"coding": [{"system": "urn:clearclaim:rate-card",
                                                                             "code": l["item_code"]}],
                                                                "text": l["description"]},
                  "servicedDate": l["service_date"], "quantity": {"value": l["quantity"]},
                  "unitPrice": {"value": l["unit_price"], "currency": "INR"},
                  "net": {"value": l["amount"], "currency": "INR"}} for l in lines],
        "total": {"value": up["billing"]["lines_total"], "currency": "INR"},
    }
    summary = dict(
        preparation_status=status,
        status_meaning={"PREPARATION_COMPLETE": "All automated checks passed or were resolved by a reviewer.",
                        "NEEDS_REVIEW": "Open review tasks must be decided before submission.",
                        "BLOCKED": "A blocking issue must be corrected (or is confirmed non-claimable)."}[status],
        disclaimer="Internal claim-preparation status. NOT an insurer decision. Not submitted to any insurer or NHCX.",
        primary_root=rb["primary_root"], open_tasks=rr["open_count"],
        estimate=dict(label=adj["label"], gross=adj["gross"], payable_if_eligible=adj["payable_if_eligible"],
                      estimated_eligible=adj["estimated_eligible"], eligibility_blocked=adj["eligibility_blocked"],
                      waterfall=adj["waterfall"]),
    )
    return dict(data=dict(status=status, packet=packet, summary=summary,
                          packet_sha256=hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest()),
                findings=[])
