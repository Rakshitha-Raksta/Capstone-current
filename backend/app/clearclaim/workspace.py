"""
ClearClaim One workspace support (read-only).

Correction preview: given a correction operation, report exactly what the orchestrator's dependency
model plans for it, using the same functions the orchestrator uses (corrections.OP_DOMAINS,
dag.stages_reading, dag.invalidated_by). Nothing is executed or written.

The plan is what targeted reprocessing will CONSIDER. During the real run a planned stage can still
be REUSED (its input hash turns out unchanged), and a stage outside the plan is re-checked only if an
upstream output actually changes (ingestion's dependents are handled this way). The preview says so
instead of predicting hash outcomes.
"""
from fastapi import APIRouter, HTTPException

from app.clearclaim import dag
from app.clearclaim.corrections import OP_DOMAINS

router = APIRouter(prefix="/api/v1/workspace", tags=["clearclaim"])


def correction_preview(op: str) -> dict:
    if op not in OP_DOMAINS:
        raise KeyError(op)
    domain = OP_DOMAINS[op]
    planned = dag.invalidated_by([domain])
    reads = [s for s in dag.STAGE_ORDER if s in dag.stages_reading([domain])]
    why = {}
    for s in planned:
        if s in reads:
            why[s] = f"reads the changed input ({domain})"
        else:
            ups = [d for d in dag.STAGES[s]["deps"] if d in planned]
            why[s] = f"depends on {', '.join(ups)}, which is re-evaluated"
    kept = [s for s in dag.STAGE_ORDER if s not in planned]
    return dict(
        op=op, domain=domain, reads_domain=reads, planned=planned, kept=kept, why=why,
        notes=[
            "Planned stages are re-evaluated; any whose input hash is unchanged is reused instead of executed.",
            "Stages outside the plan keep their previous result unless an upstream output actually changes.",
            "Ingestion reads every input only to check presence; its dependents are re-checked only if its output changes.",
        ],
        source="dag.invalidated_by (the orchestrator's own dependency model)",
    )


@router.get("/correction-preview")
def preview(op: str):
    try:
        return correction_preview(op)
    except KeyError:
        raise HTTPException(422, f"op must be one of {sorted(OP_DOMAINS)}")
