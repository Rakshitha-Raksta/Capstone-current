from app.clearclaim.stages.clinical import run_ingestion, run_data_validation, run_discharge_readiness
from app.clearclaim.stages.billing import run_billing
from app.clearclaim.stages.insurance import run_eligibility, run_adjudication
from app.clearclaim.stages.analysis import (
    run_historical, run_reconciliation, run_root_blocker, run_review_routing, run_claim_generation,
)

STAGE_FUNCTIONS = {
    "ingestion": run_ingestion,
    "data_validation": run_data_validation,
    "discharge_readiness": run_discharge_readiness,
    "billing": run_billing,
    "eligibility": run_eligibility,
    "adjudication": run_adjudication,
    "historical": run_historical,
    "reconciliation": run_reconciliation,
    "root_blocker": run_root_blocker,
    "review_routing": run_review_routing,
    "claim_generation": run_claim_generation,
}
