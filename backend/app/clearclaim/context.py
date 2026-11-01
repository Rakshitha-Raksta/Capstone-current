"""
Reference context shared by all stages: rate card, historical peers, ICD-10-CM
reference, readiness checklist configuration and tunable settings.

Loaded from the database once per process and cached; refresh() is called
after a dataset import or a settings change. Stages receive this object
read-only, which keeps them pure functions (safe to run concurrently).
"""
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.datasets.validate import load_icd10_reference

DEFAULT_SETTINGS = {
    # Administrative readiness (configurable; NOT a clinical-fitness assessment)
    "readiness_required_documents": ["claim_form", "discharge_summary", "final_bill", "photo_id"],
    "readiness_required_checklist": ["DISCHARGE_SUMMARY_FINALISED", "FINAL_BILL_GENERATED",
                                     "PHARMACY_RETURNS_CLEARED", "INSURANCE_DESK_NOTIFIED", "PATIENT_ID_VERIFIED"],
    # Billing
    "billing_price_tolerance_pct": 0.0,      # unit price may not exceed rate card by more than this
    "billing_amount_tolerance_inr": 1.0,     # rounding tolerance for qty x price
    # Historical comparison (Iglewicz & Hoaglin modified z-score)
    "historical_z_threshold": 3.5,
    "historical_min_peers": 20,
    # Review routing: "root" (evidence-aware, route root blockers only) | "all_findings" (ablation)
    "routing_policy": "root",
    # Orchestration
    "execution_mode": "parallel",            # parallel | sequential
    "rerun_policy": "targeted",              # targeted | full
    "max_stage_retries": 2,
}


@dataclass
class ReferenceContext:
    rate_card: Dict[str, dict] = field(default_factory=dict)
    historical: List[dict] = field(default_factory=list)
    icd10: Optional[Dict[str, str]] = None
    settings: Dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))

    def icd10_children(self, category: str):
        """Valid reference codes (with titles) under an ICD-10-CM category prefix, e.g. 'E78'."""
        global _icd10_index
        if _icd10_index is None:
            idx = {}
            for code, title in (self.icd10 or {}).items():
                idx.setdefault(code[:3], []).append((code, title))
            _icd10_index = idx
        return [(c, t) for c, t in _icd10_index.get(category[:3], []) if c.startswith(category)]

    @property
    def fingerprint(self) -> str:
        """Hash of everything a stage might read from the context. Part of stage input hashes."""
        h = hashlib.sha256()
        h.update(json.dumps(self.settings, sort_keys=True, default=str).encode())
        h.update(json.dumps(sorted((k, v["unit_price"]) for k, v in self.rate_card.items())).encode())
        h.update(str(len(self.historical)).encode())
        h.update(str(len(self.icd10 or {})).encode())
        return h.hexdigest()


_lock = threading.Lock()
_cached: Optional[ReferenceContext] = None
_icd10_cache = None
_icd10_index = None


def build_context(db) -> ReferenceContext:
    global _icd10_cache
    from app.clearclaim.models import RateCardItem, HistoricalClaim, AppSetting
    if _icd10_cache is None:
        _icd10_cache = load_icd10_reference() or {}
    settings = dict(DEFAULT_SETTINGS)
    for s in db.query(AppSetting).all():
        if s.key in settings:
            settings[s.key] = s.value
    return ReferenceContext(
        rate_card={r.item_code: dict(category=r.category, description=r.description, unit_price=r.unit_price)
                   for r in db.query(RateCardItem).all()},
        historical=[dict(drg_code=h.drg_code, claim_type=h.claim_type, los_days=h.los_days,
                         total_billed=h.total_billed) for h in db.query(HistoricalClaim).all()],
        icd10=_icd10_cache,
        settings=settings,
    )


def get_context(db, refresh: bool = False) -> ReferenceContext:
    global _cached
    with _lock:
        if _cached is None or refresh:
            _cached = build_context(db)
        return _cached


def invalidate_context():
    global _cached
    with _lock:
        _cached = None
