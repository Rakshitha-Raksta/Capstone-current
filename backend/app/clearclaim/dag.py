"""
Stage dependency graph (data only; stage functions are bound in pipeline.py).

deps      : stages that must finish first (ordering and data dependencies)
domains   : input domains the stage reads from the case snapshot
consumes  : {upstream_stage: [output keys] | "*" | "findings"} -- the exact upstream
            data folded into this stage's input hash. A stage whose domains and
            consumed upstream data are unchanged is REUSED instead of re-executed.

Ingestion -> Data Validation -> Discharge Readiness -> {Billing || Eligibility}
-> Adjudication -> Historical -> Reconciliation -> Root Blocker -> Review Routing
-> Claim Generation
"""
from collections import OrderedDict

ALL_INPUT_DOMAINS = ["encounter", "diagnoses", "procedures", "discharge_note", "documents", "checklist", "bill",
                     "claim_admin", "policy"]

STAGES = OrderedDict([
    ("ingestion", dict(deps=[], domains=ALL_INPUT_DOMAINS, consumes={},
                       label="Ingestion", owner="Data intake")),
    ("data_validation", dict(deps=["ingestion"], domains=["encounter", "diagnoses", "procedures", "discharge_note"],
                             consumes={}, label="Data validation", owner="Medical records / coding")),
    ("discharge_readiness", dict(deps=["data_validation"], domains=["documents", "checklist"], consumes={},
                                 label="Discharge readiness", owner="Discharge desk")),
    ("billing", dict(deps=["discharge_readiness"], domains=["bill", "procedures"],
                     consumes={"data_validation": ["los_days"]}, label="Billing verification", owner="Billing")),
    ("eligibility", dict(deps=["discharge_readiness"], domains=["policy", "claim_admin", "encounter", "diagnoses"],
                         consumes={"data_validation": ["dx_description"]}, label="Insurance eligibility",
                         owner="Insurance desk")),
    ("adjudication", dict(deps=["billing", "eligibility"], domains=["policy", "claim_admin", "encounter"],
                          consumes={"billing": ["lines_total", "disputed_amount", "disputed_amount_min", "room_rate", "ward_days"],
                                    "eligibility": ["blocked"], "discharge_readiness": ["documents"],
                                    "data_validation": ["dx_description"]},
                          label="Insurance adjudication", owner="Insurance desk")),
    ("historical", dict(deps=["adjudication"], domains=["encounter"],
                        consumes={"billing": ["lines_total"], "data_validation": ["los_days"]},
                        label="Historical comparison", owner="Analytics (advisory)")),
    ("reconciliation", dict(deps=["historical"], domains=["bill"],
                            consumes={"billing": ["lines_total", "disputed_amount", "verified_total"],
                                      "adjudication": ["waterfall", "payable_if_eligible"],
                                      "historical": ["outlier"], "billing#findings": "findings"},
                            label="Reconciliation", owner="Revenue integrity")),
    ("root_blocker", dict(deps=["reconciliation"], domains=[],
                          consumes={s: "findings" for s in ["ingestion", "data_validation", "discharge_readiness",
                                                            "billing", "eligibility", "adjudication", "historical",
                                                            "reconciliation"]},
                          label="Root-blocker analysis", owner="ClearClaim")),
    ("review_routing", dict(deps=["root_blocker"], domains=["reviews"], consumes={"root_blocker": "*"},
                            label="Review routing", owner="ClearClaim")),
    ("claim_generation", dict(deps=["review_routing"], domains=["encounter", "diagnoses", "procedures", "policy",
                                                                "claim_admin", "bill"],
                              consumes={"review_routing": "*", "root_blocker": ["primary_root", "roots", "consequences"],
                                        "adjudication": "*", "billing": ["lines_total", "line_results"],
                                        "data_validation": ["los_days"]},
                              label="Claim preparation", owner="Claims desk")),
])

STAGE_ORDER = list(STAGES)
STAGE_INDEX = {s: i for i, s in enumerate(STAGE_ORDER)}

# Which stage OWNS each input domain (where a correction to that domain takes
# effect first, ignoring ingestion which reads everything for completeness).
DOMAIN_OWNER = {
    "encounter": "data_validation", "diagnoses": "data_validation", "procedures": "data_validation",
    "discharge_note": "data_validation", "documents": "discharge_readiness", "checklist": "discharge_readiness",
    "bill": "billing", "claim_admin": "eligibility", "policy": "eligibility", "reviews": "review_routing",
}


def descendants(stage: str) -> set:
    out, frontier = set(), [stage]
    while frontier:
        s = frontier.pop()
        for t, spec in STAGES.items():
            if s in spec["deps"] and t not in out:
                out.add(t)
                frontier.append(t)
    return out


def stages_reading(domains) -> set:
    domains = set(domains)
    return {s for s, spec in STAGES.items() if domains & set(spec["domains"])}


def invalidated_by(domains) -> list:
    """
    Stages that read a changed domain, plus the transitive dependents of those
    stages, in topological order. Ingestion reads every domain only to check
    record presence, so its dependents are NOT expanded here; the orchestrator
    re-checks them only if ingestion's output actually changes.
    """
    hit = set()
    for s in stages_reading(domains):
        hit.add(s)
        if s != "ingestion":
            hit |= descendants(s)
    return [s for s in STAGE_ORDER if s in hit]


def rerun_plan_for_domain(domain: str) -> list:
    return invalidated_by([domain])


def waves():
    """Topological layers; stages in one wave have all deps satisfied and may run concurrently."""
    done, out = set(), []
    remaining = list(STAGE_ORDER)
    while remaining:
        wave = [s for s in remaining if all(d in done for d in STAGES[s]["deps"])]
        out.append(wave)
        done |= set(wave)
        remaining = [s for s in remaining if s not in done]
    return out
