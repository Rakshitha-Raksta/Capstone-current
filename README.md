# ClearClaim

**Evidence-preserving, dependency-aware preparation of hospital discharge and insurance claims.**

ClearClaim runs a hospital encounter through an 11-stage claim-preparation workflow. For each
problem it finds, it shows the **root blocker**, the **cited evidence** (rule ID, source document,
page) and **which stages a correction will re-run**. After an authorised correction it re-executes
**only the affected stages**, reuses the rest (verified by input hashes) and records everything in a
hash-chained audit history.

Capstone project 133, *Streaming Healthcare: Enhancing Patient Discharge and Insurance Claims*
(PES University, UE23CS320B).

> **What ClearClaim is not.** It is not an insurer decision system and submits nothing to any insurer
> or to NHCX. Its statuses (`PREPARATION_COMPLETE`, `NEEDS_REVIEW`, `BLOCKED`) are internal
> claim-preparation states, not approvals or denials. All billing, policy-membership and insurance
> data is **synthetic**, and financial figures are rule-based estimates. No real-world hospital time
> savings have been measured.

---

## Contents

1. [Overview](#overview)
2. [Problem statement](#problem-statement)
3. [Proposed solution](#proposed-solution)
4. [Research contribution](#research-contribution)
5. [System architecture](#system-architecture)
6. [The 11 workflow stages](#the-11-workflow-stages)
7. [Technology stack](#technology-stack)
8. [Datasets](#datasets)
9. [Features](#features)
10. [Installation](#installation)
11. [Running the backend](#running-the-backend)
12. [Running the frontend](#running-the-frontend)
13. [Configuration](#configuration)
14. [Testing](#testing)
15. [Reproducing the experiments](#reproducing-the-experiments)
16. [Research results](#research-results)
17. [Limitations](#limitations)
18. [Future improvements](#future-improvements)
19. [Repository structure](#repository-structure)

---

## Overview

Preparing a claim at discharge involves several desks: medical records and coding, the discharge
desk, billing, and the insurance desk. Each desk checks its own part, but the checks depend on one
another. A wrong bill line or a missing discharge time quietly invalidates several later checks,
and after someone fixes it, the whole case is usually checked again from the start.

ClearClaim models these checks as **one dependency graph**. The stages are deterministic Python
functions; none of them calls a language model. On top of the graph it:

- separates **root blockers** from their **downstream consequences**, so a reviewer gets one task
  per real problem instead of one per symptom;
- attaches the **cited source** of every insurance rule (59 rules extracted from 5 real insurer and
  regulator documents, each with document and page);
- after a correction, re-runs **only the stages that read the changed data**, and reuses the
  others when their input hash is unchanged;
- keeps a **tamper-evident audit chain** of every run, stage execution, correction and decision.

The web interface is a single Three.js 3D "command center" with eight stations (overview,
investigate, departments, policy, history, lab, correct and audit). It has a 2D list view and a
fallback for browsers without WebGL. The interface uses "agent" as a presentation metaphor only:
the stages are rule-based functions, not autonomous AI agents.

## Problem statement

- **Coordination failures, not single-check failures.** One upstream error (for example, an
  invalid ICD code or an inflated room charge) surfaces as several downstream findings, and
  reviewers have to work out which finding is the cause.
- **Full re-checking after every fix.** A correction typically restarts every check, including
  checks whose inputs did not change.
- **Opaque rule outcomes.** Policy terms (waiting periods, room-rent limits, co-pay, filing
  deadlines) are applied without showing which clause of which document was used.
- **Weak traceability.** It is hard to show afterwards who changed what, and which results were
  recomputed.

## Proposed solution

1. **A stage DAG.** Eleven stages with declared dependencies, input domains and the exact upstream
   outputs each stage consumes.
2. **Finding-level root attribution.** Hand-authored, evidence-conditioned causal rules label each
   finding as `ROOT` or `CONSEQUENCE` (specified in
   [docs/ROOT_BLOCKER_RULES.md](docs/ROOT_BLOCKER_RULES.md) and checked by tests).
3. **Root-only review routing.** Review tasks are created for root blockers. Consequences resolve
   automatically once their root is fixed.
4. **Targeted re-execution.** Each of the 12 correction operations maps to one input domain. The
   DAG gives the set of stages to re-evaluate, a read-only preview shows that plan before anything
   changes, and stages with unchanged input hashes are reused.
5. **Cited evidence and audit.** Every rule finding carries its rule ID, document and page. Every
   event is appended to a SHA-256 hash chain that can be verified through the API.

## Research contribution

> ClearClaim models discharge-readiness, billing and insurance-rule checks as one dependency graph.
> It attributes each problem to its root blocker with cited evidence and, after a human correction,
> re-runs only the checks that can be affected. On four controlled cohorts this cut correction-loop
> stage executions by **55.6–62.0%** and unchanged-output re-executions by **89.1–91.4%** compared
> with full re-execution, with identical detection.

Selective re-execution is an established technique (build systems, incremental computation,
self-adjusting computation). The contribution is its **application, the root-attribution layer and
the measurement** in discharge-to-claim preparation. Our literature search did not find a system
that combines discharge readiness, billing and insurance-rule checks in one dependency graph with
finding-level root attribution and measured selective re-execution. That is a gap observed in our
search, not proof of novelty; see [docs/research/RELATED_WORK.md](docs/research/RELATED_WORK.md).

| Hypothesis | Outcome |
|---|---|
| **H1.** Targeted re-execution reduces correction-loop executions while final outputs stay identical. | **Supported** (4 cohorts × 3 seeds, 0 verifier mismatches) |
| **H2.** Root-only routing reduces review tasks without losing issue coverage. | **Supported** (15.2–22.6% fewer tasks, identical coverage) |
| **H3.** Dependency-aware parallel execution reduces software wall-clock time. | **Not supported.** Only one pair of stages can run concurrently and the stage work takes milliseconds |

Not claimed: first of its kind, clinical validation, real hospital time savings, effects on insurer
approvals, or any trained prediction model. The 3D interface is an implemented artefact, not an
evaluated research contribution; no user study was run.

## System architecture

```
Browser (React + TypeScript, Vite)
  CommandCenter ─ one persistent Three.js scene (8 stations) + 2D panels, list view, WebGL fallback
  ClassicApp    ─ earlier pages (cases, review queue, control room, experiments, policy library)
        │  fetch /api/v1/*
FastAPI backend (backend/app/main.py → app/clearclaim/server.py)
  api.py         cases, review tasks, corrections, audit       studio.py     live runs + event stream
  workspace.py   read-only correction preview                  control_room.py isolated comparisons
  intel.py       rules, historical peers, research (read-only) experiments_api.py
  legacy validator routes (/policies, /rules, /claims) and the policy explainer (TF-IDF retrieval)
        │
  Orchestrator ── DAG (dag.py) ── 11 pure stage functions (stages/*.py)
        │                              └── app/rules/*  original 59-rule evaluators (hash-frozen by tests)
  corrections.py (12 audited operations, optimistic versioning) · SHA-256 audit hash chain
        │
SQLite (development: backend/claim_validator.db) or PostgreSQL · Alembic migrations (cc_* tables)
```

- **Stages** are pure functions `(inputs, upstream, context) → {data, findings}`.
- **Input hash** of a stage covers the snapshot domains it reads, the upstream keys it consumes
  and the reference-context fingerprint. In a targeted run, a planned stage whose hash is unchanged
  is `REUSED`; stages outside the plan are `NOT RE-RUN` unless an upstream output actually changes.
- **Parallelism.** `dag.waves()` yields 10 waves; only billing ∥ eligibility can run concurrently
  (thread pool).
- **Isolation.** Comparisons in the Lab and Control Room run on temporary copies of the SQLite
  database, so they never change live data.

## The 11 workflow stages

```
Ingestion → Data validation → Discharge readiness → { Billing verification ∥ Insurance eligibility }
→ Insurance adjudication → Historical comparison → Reconciliation → Root-blocker analysis
→ Review routing → Claim preparation
```

| # | Stage | Owner desk | Reads | What it checks or produces |
|---|---|---|---|---|
| 1 | Ingestion | Data intake | all input domains | Required records and fields are present (`MISSING_SOURCE_RECORD`, `MISSING_REQUIRED_FIELD`) |
| 2 | Data validation | Medical records / coding | encounter, diagnoses, procedures, discharge note | Admission and discharge times, length of stay, ICD-10-CM validity and version, sex-specific codes, procedure dates within the stay, coded diagnoses vs the discharge note |
| 3 | Discharge readiness | Discharge desk | documents, checklist | Discharge checklist items and required claim documents (including KYC) |
| 4 | Billing verification | Billing | bill, procedures, length of stay | Line arithmetic, prices vs the (synthetic) tariff, duplicate lines, billed procedures not documented, room days vs length of stay |
| 5 | Insurance eligibility | Insurance desk | policy, claim admin, encounter, diagnoses | Policy active, waiting periods, exclusions, pre-authorisation, notification and filing deadlines, using the 59 cited rules |
| 6 | Insurance adjudication | Insurance desk | billing and eligibility outputs | Deduction waterfall: room-rent limit, sub-limits, deductible, co-pay; estimated eligible amount |
| 7 | Historical comparison | Analytics (advisory) | bill total, length of stay | Modified z-score against peers in the same MS-DRG group; flags outliers as advisory only |
| 8 | Reconciliation | Revenue integrity | billing, adjudication, historical | Attributes every rupee from gross bill to estimated eligible amount; flags any unexplained residual |
| 9 | Root-blocker analysis | ClearClaim | findings of stages 1–8 | Labels each finding `ROOT` or `CONSEQUENCE` with the causal rule; raises no findings of its own |
| 10 | Review routing | ClearClaim | root-blocker output, reviews | One review task per distinct root issue, with owner, evidence, next action and rerun plan |
| 11 | Claim preparation | ClearClaim | all of the above | Preparation status and a FHIR-shaped claim packet (not validated against FHIR or NHCX profiles) |

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI 0.156, Starlette 1.9, Uvicorn, Pydantic 2 |
| Data | SQLAlchemy 2.0, Alembic (4 migrations), SQLite for development, PostgreSQL supported (tested on 18.0) |
| Policy explainer | scikit-learn TF-IDF retrieval over 129 policy-text chunks; extractive by default, with an optional LLM summary if an API key is configured (never used by the workflow stages) |
| Frontend | React 18, React Router 7, TypeScript 5.6, Vite 6, Three.js 0.169 (lazy-loaded) |
| Testing | pytest (backend), Playwright + axe-core (browser end-to-end and accessibility) |
| Packaging | Dockerfiles for backend and frontend (nginx), `docker-compose.yml` with PostgreSQL 16 |

Exact backend versions are pinned in `backend/requirements.txt`; the full verified environment is
in `backend/requirements.lock.txt`. Frontend versions are locked in `frontend/package-lock.json`.

## Datasets

ClearClaim uses five kinds of data. Each row in the application carries a provenance label, and the
interface shows whether the encounter is synthetic or a real MIMIC-IV demo encounter.

### 1. Synthetic demo dataset (powers the main demo)

`data/synthetic/demo/` — generator `clearclaim-synthetic-encounters` v2.2.1, seed 42,
**200 encounters**, error rate 0.35.

- MIMIC-IV-shaped clinical tables (`hosp/`: patients, admissions, diagnoses, procedures, DRG codes,
  transfers, services, prescriptions), template-based discharge notes (`note/`), and an
  administrative layer (`admin/`: hospital bill lines, an invented INR tariff, member policies,
  claim administration dates, discharge checklist, historical peer claims).
- Faults are injected deliberately (invalid ICD codes, duplicate bill lines, late filing, waiting
  period not met, and so on). The answer key is in `ground_truth/`, which is used **only** for
  offline evaluation and is never read by the runtime.
- **Nothing in this dataset is a real patient, a real price or a real claim.**

`data/synthetic/claims.json` holds 28 synthetic test claims for the original rule validator, each
traced to the real rule it exercises.

### 2. MIMIC-IV Clinical Database Demo v2.2 (real clinical encounters, used in experiments)

The open-access demo of MIMIC-IV (100 patients, 275 admissions, ODbL v1.0) from PhysioNet. It is
**not committed** to this repository; `scripts/download_mimic_demo.py` downloads the required
`hosp` tables into `data/mimic_iv/demo/` and verifies each file against PhysioNet's `SHA256SUMS.txt`.

MIMIC-IV contains real, de-identified clinical records: patients (sex, anchor age), admissions,
ICD-9/ICD-10 diagnoses and procedures with dates, DRG codes, transfers, services and prescriptions.
**It does not contain hospital bills, insurance policies, claims or insurer decisions.** Real rows are
never modified, and no faults are injected into them.

Credentialed MIMIC-IV v3.1 and MIMIC-IV-Note were not used.

### 3. Synthetic administrative data linked to MIMIC

For MIMIC mode, the same generator creates a synthetic layer around each real admission:
bills, tariff, member policies, claim dates, KYC and claim documents, checklist, historical peers
and discharge notes derived from the structured data. All injected faults go into this synthetic
layer. It is written to `data/synthetic/admin_for_mimic_demo/` (git-ignored, regenerated on
demand). The encounters are US (BIDMC) and the rules are Indian; linking them is a deliberate
simulation device.

### 4. Real insurer and regulator documents

`data/raw/` holds the extracted text, with page markers, of five public documents:

| Document | Issuer |
|---|---|
| Star Health Assure Insurance Policy, Customer Information Sheet (2026) | Star Health and Allied Insurance |
| my:Optima Secure policy wording (2026) | HDFC ERGO General Insurance |
| my:Optima Secure Customer Information Sheet (2026) | HDFC ERGO General Insurance |
| my:Optima Secure (2021, historical) | HDFC ERGO General Insurance |
| Master Circular on Health Insurance Business (29 May 2024) | IRDAI |

`data/structured/policy_rule_candidates.csv` contains the **59 extracted rules** (49 from insurer
documents, 10 regulatory), each with source document, page and quoted source text.
`scripts/validate_dataset.py` checks them (latest report: 0 errors, 5 documented warnings). The 17
rules of the 2026 HDFC ERGO documents are quarantined because the two documents carry different
UINs; see [docs/HDFC_ERGO_UIN_REVIEW.md](docs/HDFC_ERGO_UIN_REVIEW.md).

### 5. ICD-10-CM reference codes

`data/reference/icd10cm_codes.csv` contains 74,260 ICD-10-CM diagnosis codes (fiscal year 2025),
imported by `scripts/import_icd10_reference.py`. They appear to match the CMS ICD-10-CM list, but
the original download source of the team's database dump was not recorded (see the `.meta.json`
file).

### Why MIMIC-IV for experiments, and synthetic data for the demo

- **The demo needs complete, controllable cases.** Every case must have a bill, a policy and claim
  dates, and the walkthrough needs known blockers to correct. Only synthetic data provides all of
  that, and it can be shipped with the repository.
- **The experiments need realism on the clinical side.** Real ICD coding, real dates, repeated
  procedure codes and real data-quality quirks test the detectors in ways templated data cannot.
  The MIMIC runs found defects that synthetic data hid (for example, ICD-10 version mismatches
  that would have falsely blocked 60 real admissions), and they show that the weakest detector
  drops to 65% recall on real diagnosis titles.
- **Ground truth must exist.** No public dataset links real encounters to real bills and insurer
  outcomes, so faults are injected into the synthetic administrative layer, where the answer key
  is known exactly.

### MIMIC-IV Clinical Database Demo v2.2

To use MIMIC mode (optional):

```powershell
.\.venv\Scripts\python.exe scripts\download_mimic_demo.py                 # add --all-hosp for every hosp table
.\.venv\Scripts\python.exe scripts\generate_synthetic_data.py --mimic-root data/mimic_iv/demo --out data/synthetic/admin_for_mimic_demo
.\.venv\Scripts\python.exe scripts\validate_encounter_data.py --clinical data/mimic_iv/demo --admin data/synthetic/admin_for_mimic_demo
$env:DATA_MODE = "mimic"; .\.venv\Scripts\python.exe scripts\setup_demo.py --source mimic
```

`setup_demo.py --source mimic` generates the admin layer automatically if it is missing.

How real-data quirks are handled:

- ICD-10 codes valid in 2008–2019 but absent from the FY2025 list are reported as the advisory
  `ICD_CODE_VERSION_MISMATCH`, not as invalid. ICD-9 codes are not checked against ICD-10-CM.
- Historical comparison uses MS-DRG peers only; APR-DRG-only admissions get `NO_COMPARABLE_DRG`.
- ICU days are detected by care-unit name pattern (MICU, SICU, TSICU, CVICU, CCU, and so on).
- Procedures performed in the ED before admission are accepted from the ED registration time.
- Real case 26529390 has procedures dated inside the same patient's later admissions, a
  source-side anomaly. ClearClaim reports it as `PROCEDURE_DATE_OUT_OF_RANGE` and never patches it;
  it is registered in `data/mimic_iv/known_source_anomalies.json`.

Licence and access rules: respect the ODbL licence (attribution, share-alike) if you redistribute
anything derived from the demo. Never commit credentialed MIMIC-IV data, put it in a Docker image or
public deployment, or send it to an external LLM API.

## Features

- **3D command center** (`/`) with eight stations, switched with the left rail or keys 1–8:

  | # | Station | Purpose |
  |---|---|---|
  | 1 | Overview | Top blocked encounter, its highest-priority blocker and the next action |
  | 2 | Investigate | Root blockers, evidence and dependencies; click any stage block |
  | 3 | Departments | Which desk owns each stage, and its open work |
  | 4 | Policy | The 59 cited rules, coloured by this encounter's recorded outcome |
  | 5 | History | Historical peers, with method and limitations |
  | 6 | Lab | Sequential vs parallel, or full vs targeted, on isolated database copies, replayed as Gantt lanes |
  | 7 | Correct | Preview the rerun plan, confirm with a reason, apply; see executed, reused and not-re-run stages |
  | 8 | Audit | Verify the hash chain and read the recorded research results |

  Drag to orbit, right-drag to pan, scroll to zoom; **R** re-centres and **Esc** clears the
  selection. The list view is in the top bar or at `?mode=list`, and `?webgl=off` forces the
  no-WebGL fallback.
- **Correction preview and application.** `GET /api/v1/workspace/correction-preview` returns the
  dependency plan without changing anything; `POST /api/v1/studio/runs` applies a confirmed
  correction and streams stage events.
- **Targeted reruns** with input-hash reuse, and a full-rerun mode for comparison.
- **Review queue** with one task per root issue, and recorded reviewer decisions.
- **Policy rule references.** Every rule finding links to its source document and page; the policy
  library and the explainer show the quoted text.
- **Audit history.** `GET /api/v1/audit-events/verify` recomputes the hash chain.
- **Agent Control Room** (`/control-room`) and presentation mode (`/present`): a recorded,
  time-scaled replay of real backend events. Earlier pages (ClearClaim One workspace at `/one`,
  cases, review, policy library, experiments) are under **Classic tools**.
- **Experiments page** to start small experiment runs from the UI (output in
  `results/experiments/run_<id>/`, git-ignored).

## Installation

Requirements: **Python 3.11**, **Node.js 20+** (verified with Node 24.14) and Git. The commands below
use Windows PowerShell; on macOS or Linux use `.venv/bin/python` and forward slashes.

```powershell
git clone https://github.com/Rakshitha-Raksta/Capstone-current.git
cd Capstone-current
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend; npm install; cd ..
```

Initialise the development database (creates `backend/claim_validator.db`, runs migrations, seeds
the 59 rules if missing, imports the synthetic demo and processes the 200 encounters):

```powershell
.\.venv\Scripts\python.exe scripts\setup_demo.py
```

Without `--reset` the script is safe to rerun: existing encounters are skipped. `--reset` wipes the
ClearClaim tables first and `--reseed-rules` drops and re-seeds the legacy validator tables, so use
those flags only when you intend to discard local data.

## Running the backend

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 5001
```

- API documentation: http://127.0.0.1:5001/docs
- Health check: http://127.0.0.1:5001/api/v1/health
- Run backend commands from `backend\`, so the default SQLite path resolves to
  `backend/claim_validator.db`.

## Running the frontend

```powershell
cd frontend
npm run dev          # http://localhost:5173 (proxies API calls to the backend on :5001)
npm run build        # type-check and production build into frontend/dist
npm run preview      # serve the production build
```

**Docker (optional).** `copy .env.example .env`, set `POSTGRES_PASSWORD`, then
`docker compose up --build` and open http://localhost:8080. The compose file runs PostgreSQL, the
backend and an nginx-served frontend. The Docker images have not been built or run in the recorded
verification.

## Configuration

Settings come from environment variables only; nothing secret is hard-coded. Copy `.env.example`
to `.env` for Docker (`.env` is git-ignored).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./claim_validator.db` | SQLAlchemy URL (relative to `backend/`) or a PostgreSQL URL |
| `APP_ENV` | `development` | `development`, `test`, `docker` or `production`. In production, writes are disabled unless a token is set |
| `CLEARCLAIM_WRITE_TOKEN` | empty | Bearer token required for corrections, decisions, settings and experiments |
| `DATA_MODE` | `demo` | `demo` (synthetic) or `mimic` |
| `MIMIC_CLINICAL_DIR`, `MIMIC_ADMIN_DIR`, `MIMIC_SOURCE_LABEL` | see `.env.example` | Locations and label for MIMIC mode |
| `DEMO_DATASET_DIR` | `data/synthetic/demo` | Synthetic demo dataset location |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8080` | Allowed browser origins |
| `ALLOW_EXPERIMENTS` | `true` | Allow CPU-heavy experiment runs from the API |
| `MAX_REQUEST_BYTES` | `262144` | Request-size limit |
| `AUTO_MIGRATE` | `true` | Run Alembic migrations at start-up |
| `LOG_LEVEL` | `INFO` | Log level (JSON request logs) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | unset | Optional LLM summary in the policy explainer only (also `pip install anthropic` or `openai`; without the package it stays extractive). Leave unset for extractive answers, and never use it with credentialed MIMIC data |
| `TEST_DATABASE_URL` | unset | Run the backend tests against PostgreSQL |
| `E2E_PYTHON`, `E2E_BROWSER` | current Python, `chrome` | Interpreter and browser for the end-to-end tests |

## Testing

```powershell
# Backend: unit, API, isolation, root-blocker, rule-coverage and frozen-file tests
cd backend; ..\.venv\Scripts\python.exe -m pytest -q; cd ..

# Frontend: strict type-check and production build
cd frontend; npm run build; cd ..

# Datasets
.\.venv\Scripts\python.exe scripts\validate_dataset.py          # 59 cited rules
.\.venv\Scripts\python.exe scripts\validate_encounter_data.py   # synthetic encounter dataset
.\.venv\Scripts\python.exe scripts\check_synthetic_claims.py    # 28 legacy validator claims

# Browser end-to-end and accessibility tests (Playwright + installed Chrome)
$env:E2E_PYTHON = "$PWD\.venv\Scripts\python.exe"
python -m pytest e2e -q
```

- The backend suite uses its own database (`backend/test_claim_validator.db`) and never touches
  the development database.
- The end-to-end suite starts its own backend on a throwaway SQLite database in a temporary folder,
  initialised by `scripts/setup_demo.py --reset`, and serves the production build with
  `vite preview`. It needs Playwright in the Python that runs pytest, and `axe-core` from
  `npm install` for the accessibility audit.
- `test_root_blocker.py` checks SHA-256 hashes of the original rule evaluators and
  `policy_rule_candidates.csv`, so accidental changes to them fail the build.

## Reproducing the experiments

Each run generates its seeded datasets, gives every arm its own SQLite copy and writes
`summary.json` plus per-case raw outputs. Neither the development database nor existing results
are touched. `run_experiments.py` refuses to write into a folder that already holds a
`summary.json` unless you pass `--overwrite`, so use a new `--name`:

```powershell
$P = ".\.venv\Scripts\python.exe"
# Synthetic, development seeds (about 4 minutes on the recorded laptop)
& $P scripts\run_awake.py -- $P scripts\run_experiments.py --name repro_main --seeds 1 2 3 --n 200
& $P scripts\verify_results.py results\experiments\repro_main

# Synthetic, held-out seeds
& $P scripts\run_awake.py -- $P scripts\run_experiments.py --name repro_main_heldout --seeds 4 5 6 --n 200

# Real MIMIC-IV demo encounters + synthetic layer (download the demo first)
& $P scripts\run_awake.py -- $P scripts\run_experiments.py --name repro_mimic --seeds 1 2 3 --mimic-root data/mimic_iv/demo
& $P scripts\run_awake.py -- $P scripts\run_experiments.py --name repro_mimic_heldout --seeds 4 5 6 --mimic-root data/mimic_iv/demo
```

- `run_awake.py` stops Windows sleeping during long runs; on other systems it just runs the command.
- `verify_results.py` recomputes every metric from the raw outputs and the ground-truth files
  without using the harness's evaluation code, and exits 1 on any mismatch.
- The recorded runs are in `results/experiments/main`, `main_heldout`, `mimic_demo` and
  `mimic_demo_heldout`. For the MIMIC runs only the aggregate `summary.json` and `verification.json`
  are committed; per-case outputs keyed on real admission IDs stay local.
- Archived earlier runs (`archive_*`, `*_v1_*`, `*_v2_*`) and an independent reproduction
  (`repro_2026-10-27`) are kept for the run history.

The full protocol is in [docs/research/EVALUATION_PROTOCOL.md](docs/research/EVALUATION_PROTOCOL.md).

## Research results

**Conditions.** Four final runs on the final code; each encounter is one claim-preparation case.
Arm **B** is the baseline: sequential execution with a full rerun after every correction. Arm **C** is
the treatment: parallel waves, targeted rerun with hash reuse and root-only routing. Ablations
C − targeted and C − root routing isolate each mechanism. Corrections are applied by an **oracle
reviewer** that uses the recorded fix for each injected fault; no human reviewers took part. All four
arms in a seed start from the same database file (SHA-256 recorded), and their first-pass outputs
were identical case by case in all 12 seeds. Values are means across 3 seeds unless stated.
Every number was recomputed by `verify_results.py` with **0 mismatches**.

| Run | Cohort | Seeds | Encounters per seed |
|---|---|---|---|
| `main` | Synthetic, development | 1, 2, 3 | 200 |
| `main_heldout` | Synthetic, held-out | 4, 5, 6 | 200 |
| `mimic_demo` | Real MIMIC-IV demo encounters + synthetic layer, development | 1, 2, 3 | 275 |
| `mimic_demo_heldout` | Same, held-out | 4, 5, 6 | 275 |

Held-out seeds were generated and run only after all detector and causal-rule fixes were frozen.

| Metric (C vs B) | Synthetic dev | Synthetic held-out | MIMIC dev | MIMIC held-out |
|---|---|---|---|---|
| Correction-loop stage executions, B → C | 1793 → 734.3 | 1760 → 780.7 | 3355 → 1276 | 3509 → 1357.7 |
| **Loop execution reduction** | **59.0%** | **55.6%** | **62.0%** | **61.3%** |
| **Unnecessary re-execution reduction** | **91.0%** | **89.1%** | **91.4%** | **91.4%** |
| **Review-task reduction (root-only routing)** | **19.1%** | **22.6%** | **15.2%** | **17.0%** |
| Issue coverage by tasks (both routings) | 99.74% | 99.74% | 98.44% | 97.87% |
| Detection precision, all findings (pooled) | 95.72% | 94.35% | 98.44% | 98.85% |
| Misattributed issues | 0 | 0 | 0 | 0 |
| False clears (faulty cases marked complete) | 1 of 283 | 0 of 276 | 3 of 452 | 3 of 481 |
| Root-blocker exact match | 99.29% | 99.64% | 96.90% | 96.05% |

- **C − targeted** returns exactly to the baseline execution counts, so the saving comes from the
  rebuild policy, not from parallelism. Detection is identical across B, C and C − targeted.
- **Parallel execution gave no reliable latency benefit.** Median first-pass latency of C was within
  noise of B in every run. In a dedicated 10-repetition measurement on one encounter, parallel was
  2.8% slower (72.0 vs 74.1 ms median), and about 85% of wall-clock time was spent outside the stage
  functions.
- **The weakest component** is the `CODED_DX_NOT_IN_NOTE` detector (token overlap between diagnosis
  titles and the note). Its recall on real MIMIC titles is 65% (46/71). It caused all 27 missed
  issues and all 7 false clears.
- **Latency** is software time on one laptop with SQLite and varies with machine load; only
  within-run comparisons are meaningful, and no latency claim is made.
- **Modelled human minutes** in the results are computed from assumed durations. They are not
  time-and-motion measurements and must not be quoted as time savings.
- An independent rerun of the synthetic development cohort (2026-10-27) reproduced all 73
  deterministic metrics exactly; latencies differed, as expected.

Per-seed values, error analysis, the documented source anomaly and threats to validity are in
[docs/research/EXPERIMENT_RESULTS.md](docs/research/EXPERIMENT_RESULTS.md).

## Limitations

**Data**
- Only the open MIMIC-IV demo was used (100 patients, 275 admissions). Credentialed MIMIC-IV and
  MIMIC-IV-Note were not available.
- All financial and administrative data is synthetic, including bills, the invented INR tariff,
  policies, claim dates and, in MIMIC mode, the discharge notes.
- Synthetic notes are template-based; real notes are far more varied.
- Historical peers are 16 synthetic MS-DRG groups, so 256 of 275 real admissions get
  `INSUFFICIENT_PEERS` (informational).
- The MS-DRG titles in the generator catalog and the origin of the ICD-10-CM dump are unverified.

**Method**
- **Circularity:** the same team wrote the fault injector, the detectors and the causal rules, so
  synthetic detection and root-blocker figures are optimistic upper bounds.
- The causal rules are hand-authored and only as complete as the rule table.
- The reviewer is an oracle: there was no user study and no time study.
- Three seeds per cohort give a rough standard deviation only; no significance testing was done.
- Note matching is token overlap with a small synonym list, not clinical NLP.
- The original keyword-based exclusion evaluator is noisy on real diagnosis titles.

**Rules (original evaluators, unchanged and hash-frozen)**
- Only 21 of the 59 rules are evaluable per claim: 11 are administrative, 17 HDFC ERGO 2026 rules are
  quarantined because of a UIN conflict, and 10 IRDAI rules are regulatory references.
- Waiting-period rules apply PED and specified-disease waits to every claim, because there is no
  PED declaration or specified-disease list.
- Date-only fields cannot distinguish a 24-hour deadline from a 25-hour one.
- Neither the adjudication waterfall nor the legacy calculator has been validated against an insurer.

**Product and engineering**
- Statuses are internal preparation states; the claim packet is FHIR-shaped but not validated.
- Authentication is a single optional write token; there are no user accounts or roles.
- The audit chain is tamper-evident, not tamper-proof.
- Processing is serialised per process; the design suits a single-instance demo, not horizontal
  scaling.
- Docker images were not built or run during verification, and there is no public deployment.
- The original validator code still emits SQLAlchemy and Pydantic deprecation warnings; it is
  intentionally left unchanged.

## Future improvements

- Replace token-overlap note matching with a clinical concept extractor, and evaluate it on
  MIMIC-IV-Note under credentialed access.
- Run the experiments on credentialed MIMIC-IV v3.1 with more seeds and significance testing.
- Run a user study with billing and insurance staff to measure review effort and traceability.
- Complete source review of the quarantined HDFC ERGO rules and add the room-rent proportionate
  deduction clause.
- Validate the claim packet against FHIR R4 and NHCX profiles, as an export only.
- Add user accounts, roles and per-user audit identity; anchor the audit chain externally.
- Build and test the Docker images in CI, and add a GitHub Actions workflow for tests and build.

## Repository structure

```
.
├── backend/
│   ├── app/
│   │   ├── clearclaim/        # ClearClaim: models, DAG, orchestrator, stages/, corrections, API routers
│   │   ├── datasets/          # MIMIC-IV loader, seeded synthetic generator, error registry, validator
│   │   ├── experiments/       # experiment harness (arms, oracle reviewer, metrics)
│   │   ├── rules/             # original 59-rule evaluators (hash-frozen by tests)
│   │   ├── rag/               # policy explainer (TF-IDF retrieval, optional LLM summary)
│   │   ├── api/ database/ models/ schemas/ services/   # original validator API, DB, models, calculator
│   │   └── main.py            # FastAPI application entry point
│   ├── alembic/               # migrations for the ClearClaim tables
│   ├── tests/                 # backend test suite
│   ├── requirements.txt       # direct dependencies (pinned)
│   └── requirements.lock.txt  # full verified environment
├── frontend/
│   ├── src/
│   │   ├── world/             # Three.js engine and React wrapper
│   │   ├── pages/space/       # 3D command center
│   │   ├── pages/cc/, pages/one/, pages/*.jsx   # classic ClearClaim and validator pages
│   │   ├── components/ lib/ styles/
│   │   └── main.tsx           # entry point
│   ├── package.json, package-lock.json, vite.config.js, tsconfig.json
│   └── Dockerfile, nginx.conf
├── e2e/                       # Playwright end-to-end and accessibility tests
├── data/
│   ├── raw/                   # extracted text of the 5 insurer and regulator documents
│   ├── structured/            # 59 cited rules, policy inventory, source documents, validation report
│   ├── processed/             # policy-text chunks and TF-IDF index for the explainer
│   ├── reference/             # ICD-10-CM FY2025 codes
│   ├── synthetic/             # synthetic demo dataset (200 encounters) and legacy test claims
│   └── mimic_iv/              # known source anomalies (the demo itself is downloaded, not committed)
├── results/experiments/       # recorded experiment runs, verification files and run history
├── scripts/                   # setup, data generation and import, validation, experiments, verification
├── docs/
│   ├── ROOT_BLOCKER_RULES.md  # causal-rule specification (checked by tests)
│   ├── HDFC_ERGO_UIN_REVIEW.md # why 17 HDFC ERGO rules are quarantined
│   └── research/              # evaluation protocol, full results, related work, references
├── docker-compose.yml, .env.example, .dockerignore
└── README.md
```
