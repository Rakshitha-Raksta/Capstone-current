"""
Environment-based configuration. No secrets are hard-coded; see .env.example.

DATABASE_URL is shared with the legacy rule engine (app/database/db.py reads
the same variable). The default is the legacy relative SQLite file, so run
backend commands from the backend/ directory (the run scripts do this).
"""
import os
from dataclasses import dataclass, field
from typing import List

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _list(v, default):
    if not v:
        return default
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Settings:
    app_env: str = os.environ.get("APP_ENV", "development")
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./claim_validator.db")
    data_mode: str = os.environ.get("DATA_MODE", "demo")      # demo | mimic
    demo_dataset_dir: str = os.environ.get("DEMO_DATASET_DIR", os.path.join(REPO_ROOT, "data", "synthetic", "demo"))
    mimic_clinical_dir: str = os.environ.get("MIMIC_CLINICAL_DIR", os.path.join(REPO_ROOT, "data", "mimic_iv", "demo"))
    mimic_admin_dir: str = os.environ.get("MIMIC_ADMIN_DIR",
                                          os.path.join(REPO_ROOT, "data", "synthetic", "admin_for_mimic_demo"))
    mimic_source_label: str = os.environ.get("MIMIC_SOURCE_LABEL", "MIMIC_IV_DEMO_2.2")
    cors_origins: List[str] = field(default_factory=lambda: _list(
        os.environ.get("CORS_ORIGINS"), ["http://localhost:5173", "http://127.0.0.1:5173"]))
    # If set, every mutating ClearClaim endpoint requires "Authorization: Bearer <token>".
    write_token: str = os.environ.get("CLEARCLAIM_WRITE_TOKEN", "")
    max_request_bytes: int = int(os.environ.get("MAX_REQUEST_BYTES", str(256 * 1024)))
    allow_experiments: bool = _bool(os.environ.get("ALLOW_EXPERIMENTS"), True)
    log_level: str = os.environ.get("LOG_LEVEL", "INFO")
    app_version: str = "0.3.0"


settings = Settings()
