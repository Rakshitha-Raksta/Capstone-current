# Persistent issue memory and historical-case intelligence

Two extensions to the deterministic ClearClaim pipeline. Neither replaces the pipeline or changes how
findings, root/consequence roles or case statuses are computed.

| Extension | Code | Writes | Read by the DAG? |
|---|---|---|---|
| Persistent issue memory + idempotent review routing | `backend/app/clearclaim/issues.py`, `identity.py` | `cc_issues`, `cc_issue_events`, `cc_review_tasks.issue_id` | No (runs after the stages, in the run's transaction) |
| Decision evidence fingerprint | `stages/analysis.py::run_review_routing`, `snapshot.py` | `cc_review_decisions.evidence_fingerprint` | Yes: a decision applies only while the evidence it was made on is unchanged |
| Historical-case intelligence | `backend/app/clearclaim/case_intel.py` | nothing | No (read-only API) |

## 1. What the previous design did (measured, not assumed)

Measured on an isolated copy of the 200-encounter database with the code at commit `bdb0c3c`
(`legacy_probe.py`, results in `results/experiments/intel_memory_v1/before_after_probe.json`):

* Review tasks already had an evidence-based key (`task_key` = code + bill line / diagnosis sequence /
  document / checklist item / note phrase / field + cited rule ids) and `_sync_tasks` reused an active
  task with the same key. **Identical sequential reruns created no duplicate tasks** (40 cases x 6 reruns).
* There was no stable issue identity: findings are re-inserted every run; a resolved issue that came
  back produced a new, unlinked task; there was no detection count, first/last-seen time, reopen
  history or per-issue reviewer notes.
* A change in which policy rules fired changed `task_key`, so one issue could get a second task.
* **False suppression**: a reviewer's "accept with justification" was keyed only by `task_key`, so it
  kept applying after the evidence changed materially (e.g. the principal diagnosis replaced by
  another undocumented code), and the case could be left PREPARATION_COMPLETE.
* **Concurrency**: no database uniqueness on tasks; concurrent runs of one case (separate sessions)
  read the stage "current" pointers before taking the write lock and left every stage with several
  `is_current=True` executions.

## 2. Issue identity, fingerprint, lifecycle

* `issue_key` = code + identity evidence (`line_no`, `seq_num`, `item`, `document`, `phrase`, `field`).
  It is exactly the grouping the routing stage already used, so one task <-> one issue. Cited rules
  are *not* part of it. Two bill lines with the same code are two issues (verified: 0 merges).
* `evidence_fingerprint` = SHA-256 of severity, expected, recorded, amount, sorted cited rules and
  `item_code / icd_code / duplicate_of / rule_category / rate_card_item / record`. Free text excluded.

| Status | Meaning |
|---|---|
| `PENDING_REVIEW` | present, one open review task |
| `OPEN` | present, no review pending (reviewer confirmed it blocks, or it is currently explained as a consequence of another root) |
| `RESOLVED` | no longer detected **after a recorded data correction**; `resolution_action` names the correction |
| `DISMISSED` | accepted by a reviewer with a justification (kept while the evidence is unchanged) |
| `NO_LONGER_DETECTED` | gone without a correction (e.g. a settings change); **never claimed as resolved** |

Rerun behaviour (all inside the run transaction, `IssueTracker.sync`):

| Issue state when detected again | Action |
|---|---|
| none | create issue + task (or adopt a pre-existing task with the same key) |
| `PENDING_REVIEW` / `OPEN` | detection count +1, last-detected updated, evidence refreshed in place, **same task, owner, assignee and notes kept**; recorded as `REVIEW_TASK_DEDUPLICATED` in the audit trail; a rule-set change re-keys the same task (`TASK_REKEYED`) |
| `DISMISSED`, same evidence | dismissal kept |
| `DISMISSED`, material evidence change | routing no longer applies the decision -> issue `REOPENED`, new task, previous decision kept in history |
| `RESOLVED` / `NO_LONGER_DETECTED` | genuinely back -> `REOPENED` (same issue row), new task, previous resolution kept in the `REOPENED` event |
| not routed but still among current findings | `OPEN`, task superseded ("explained as a consequence") |
| not detected | `RESOLVED` only after a CORRECTION run, otherwise `NO_LONGER_DETECTED`; dismissals preserved |

UI labels (one definition in `frontend/src/lib/issues.ts`): New issue, Already under review,
Previously unresolved, Resolved, Reopened, Dismissed, No longer detected, Prediction only.

### Idempotency and concurrency

1. `Orchestrator._lock_case` serialises runs of a case before any state is read: Postgres
   `SELECT ... FOR UPDATE`; SQLite `BEGIN IMMEDIATE` (a deferred transaction would hold a shared lock
   while waiting and deadlock the writer). SQLite busy timeout raised to 30 s.
2. Database constraints: `UNIQUE(case_id, issue_key)` on `cc_issues`; partial unique indexes
   `uq_task_open_key (case_id, task_key) WHERE status='OPEN'` and `uq_task_open_issue (issue_id) WHERE status='OPEN'`.
3. Inserts run in a SAVEPOINT; if a concurrent writer won, the existing row is adopted.

### Decision fingerprints and backward compatibility

`POST /review-tasks/{id}/decision` stores the fingerprint of the evidence the reviewer saw. The routing
stage applies a decision only if its fingerprint is NULL (recorded before this change: original
behaviour, unchanged) or equal to the current one. The snapshot includes the field only when set, so
existing cases keep byte-identical stage input hashes. The experiment harness records decisions without
fingerprints, so the recorded experiment protocol is unaffected.

## 3. Historical-case intelligence

Weighted structured nearest-neighbour retrieval (case-based reasoning). **No model is trained** and
nothing is learned from real hospital outcomes; the "history" is the encounters stored in the database
(synthetic in demo mode) and whatever reviewers recorded on them.

* Features (input data only, never the encounter's own findings): ICD-10 diagnoses and 3-character
  categories, principal category, procedure codes, MS-DRG, length of stay, bill item codes and the
  category mix of the billed amount, policy version, claim type / room / admission type.
* Per-feature similarity: diagnoses `0.5 J(categories) + 0.3 J(codes) + 0.2 [same principal category]`;
  procedures `J(codes)` (not compared when neither has procedures); DRG exact within scheme; stay
  `1 - |a-b|/max(a,b)`; bill `0.5 J(items) + 0.5 cosine(category shares)`; policy equality; admin share
  of equal attributes. Score = sum(w x s) / sum(w over features defined for the pair).
* Weight profiles per kind of issue put weight on the input domains that the deterministic check for
  that kind reads (dag.py): coding, billing, readiness, insurance, plus a general profile for the list.
  Weights are fixed by design and were not tuned on evaluation data.
* Prediction: an issue category is reported for the k (=25) most similar processed encounters as
  "observed in n of k" with a 95% Wilson interval and the base rate; it is *predicted* only when the
  interval's lower bound exceeds the base rate. Fewer than 5 comparable encounters, or fewer than 10
  processed encounters overall -> `INSUFFICIENT_DATA`. Predictions are labelled "Prediction only" and
  never create findings, tasks or status changes (tested).
* Recommendation: among similar encounters that had the same issue, count recorded outcomes
  (resolved / dismissed / confirmed / unreviewed / unknown). Unreviewed and unknown outcomes are never
  counted as resolved or failed. A correction is recommended only when >= 3 similar encounters have a
  review outcome and at least one was resolved by a recorded correction; otherwise `INSUFFICIENT_DATA`
  and the deterministic rule's own guidance is shown, labelled as such.

On the current demo database 2 corrections and 2 decisions are recorded and 163 of 167 historical issue
occurrences were never reviewed, so recommendations correctly return insufficient data there.

## 4. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/cases/{id}/issues` | issues of a case with lifecycle label, dedup note and history |
| GET | `/api/v1/issues`, `/api/v1/issues/summary` | issue list; issue-memory metrics (operations dashboard) |
| POST | `/api/v1/review-tasks/{id}/note` | reviewer note / assignee (kept across reruns) |
| GET | `/api/v1/intel/cases/{id}/similar-cases?k=&k_pred=&k_rec=` | similar encounters, predictions, recommendations, limitations |
| GET | `/api/v1/intel/similarity-method`, `/api/v1/intel/extension-results` | method; recorded evaluation |

`GET /cases/{id}`, `/review-tasks` and `/overview` additionally return issue lifecycle fields.

## 5. Database changes

Alembic revision `c4e8a1f2b9d3` (additive): tables `cc_issues`, `cc_issue_events`; nullable columns
`cc_review_tasks.issue_id`, `cc_review_decisions.evidence_fingerprint`; two partial unique indexes. The
migration refuses to run (instead of altering data) if duplicate OPEN tasks exist. Existing tasks are
linked by `scripts/backfill_issue_memory.py` (idempotent; `--dry-run` available), which reconstructs
detection counts only from recorded runs and records tasks closed without a correction as
`NO_LONGER_DETECTED`.

## 6. Evaluation

`python scripts/run_intel_memory_experiments.py` -> `results/experiments/intel_memory_v1/`
(protocol in `backend/app/experiments/intel_memory_eval.py`). See section 7 of this file for the
recorded numbers and their caveats.

## 7. Recorded results (`results/experiments/intel_memory_v1/`, 6 seeds, mean ± sd per seed)

Existing synthetic cohorts (seeds 1-3 = `main`, 4-6 = `main_heldout`, 200 encounters each), read only;
MANIFEST hashes identical before and after every seed. History outcomes = the harness's oracle reviewer
(~87 corrections and ~75 decisions per seed), not human reviewers.

**Issue memory**

| Metric | Result |
|---|---|
| Reruns (200 cases x 3, full) | 600 per seed |
| Review tasks created by those reruns / open duplicate tasks | 0 / 0 |
| Re-detections that kept the existing task ("duplicates prevented") | 64.5 ± 23.1 |
| Repeat detections linked to existing issues | 225 ± 30.5 |
| Genuine reappearances (inverted update corrections) reopened as the **same** issue | 26.8 ± 6.5 of 26.8 attempted; 0 extra issue rows |
| Two same-code bill lines per case (20 cases): merged / other still pending after fixing one | 0 / 20 of 20 |
| Issues marked RESOLVED without a correction | 0 |
| Dismissal + material evidence change (40 cases): reopened, with fingerprints | 40 of 40 |
| ... false suppression **before** (no fingerprint) vs **after** | 40 of 40 vs 0 of 40 |
| ... case left PREPARATION_COMPLETE despite new evidence, before vs after | 39.8 vs 0 |
| ... false reopen after identical rerun / unrelated change | 0 / 0 |

Before/after concurrency probe (live-DB copy, 8 cases x 4 concurrent sessions): duplicate tasks 0 -> 0;
cases with stages holding several "current" executions 8 -> 0.

Caveat: identical sequential reruns did not duplicate tasks before this change either; the "prevented"
count is relative to a router that inserts one task per detection.

**Historical intelligence (leave-one-out)**

| Metric | Seeds 1-3 | Seeds 4-6 | All |
|---|---|---|---|
| Issue prediction F1 (similar neighbours) | 0.067 ± 0.023 | 0.057 ± 0.030 | 0.062 ± 0.025 |
| ... precision / recall | 0.055 / 0.086 | 0.050 / 0.068 | 0.053 / 0.077 |
| Issue prediction F1, random-neighbour baseline | 0.018 | 0.043 | 0.031 |
| Retrieval precision@5 (proxy), similar vs random | 0.061 vs 0.051 | 0.050 vs 0.061 | 0.056 vs 0.056 |
| Recommendation evidence coverage | 0.60 | 0.67 | 0.63 |
| Recommendation insufficient-data rate | 0.40 | 0.33 | 0.37 |

Interpretation, stated plainly:
* Prediction is weak in absolute terms and only modestly above the random baseline; retrieval precision
  is indistinguishable from random. This is expected: the generator injects errors at random with
  respect to clinical features, so there is little for similarity to find. The feature is useful as
  explained evidence retrieval, not as a predictor, on this data.
* Recommendation agreement with the ground-truth correction op was 100%, but that is near-tautological:
  each synthetic error type has one canonical correction op and the oracle history applied exactly those.
* Retrieval recall@K is not reported (no ground-truth similarity set). Prediction-item insufficient-data
  rate was 0 because every query had >= 25 processed neighbours; on small databases it is non-zero.
* On the live demo database (2 corrections, 2 decisions recorded) recommendations return insufficient data.

What would make these numbers meaningful: real (or realistically correlated) historical claims with
recorded reviewer outcomes, a labelled set of "truly similar" encounter pairs for recall@K, and issue
labels that are not injected independently of the clinical record.
