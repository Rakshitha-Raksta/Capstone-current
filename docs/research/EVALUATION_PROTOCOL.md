# Experiment Protocol

## Hypothesis (tested, not assumed)

> Evidence-preserving, dependency-aware claim review reduces unnecessary stage executions and
> review workload compared with full re-execution and all-findings routing, while maintaining
> claim-issue detection performance.

## Cohort

- **Synthetic cohorts:** from `scripts/generate_synthetic_data.py` (generator v2.2.0) with **200
  encounters** per seed.
  - **Development seeds 1, 2 and 3** were used for error analysis.
  - **Held-out seeds 4, 5 and 6** were run only after the error-analysis fixes, as an unbiased check.
  - The fault rate is **0.35**: the share of encounters that receive 1–2 injected data faults.
- **Real-encounter cohorts:** the 275 admissions of the MIMIC-IV Clinical Database Demo v2.2
  (`--mimic-root data/mimic_iv/demo`), with development seeds 1–3 and held-out seeds 4–6.
  - Clinical tables are real and never modified.
  - Each seed generates its own synthetic administrative layer (policies, claim dates, checklist,
    bill, derived note).
  - Faults are injected **only** into that synthetic layer.
- **Policy issues:** added on top by member-behaviour probabilities, all labelled from the cited
  rule thresholds. They cover waiting period, late filing, late notification, short preauth,
  missing KYC and inactive policy.
- **Scenario classes:** normal, single-fault, multi-fault, missing-information and legitimate
  outlier.
- **Ground truth:** `ground_truth/injected_errors.csv` is written by the injector, independently
  of any pipeline output. It is read only by the evaluation harness. Runtime databases are built
  from the same files with the labels left out.
- **Isolation:** each seed builds a fresh SQLite database (rules seeded, cohort imported). Each arm
  runs on its own copy, so arms see identical inputs.

## Arms

| Arm | Execution | Rebuild on correction | Review routing |
|---|---|---|---|
| A manual (modelled) | none (no software) | full redo per fix | n/a |
| B sequential full | sequential | every stage re-executed | root blockers |
| **C parallel targeted** (treatment) | billing ∥ eligibility | targeted plus input-hash reuse | root blockers |
| C − targeted (ablation) | parallel | every stage re-executed | root blockers |
| C − root routing (ablation) | parallel | targeted | every BLOCKING/REVIEW finding, including consequences |

## Procedure per arm

1. **Pass 1:** process every case once (`trigger=INITIAL`).
2. **Pass 2, correction loop (oracle reviewer).** For each case, repeatedly take the highest-priority
   open review task (BLOCKING first, then by id):
   - If a recorded ground-truth correction matches the task's code and the task allows
     `CORRECT_DATA`, apply that correction through the normal `apply_correction` path. This
     triggers that arm's rerun policy.
   - Otherwise record the decision a reviewer would have to make: the recorded resolution, else
     `ACCEPT_WITH_JUSTIFICATION` if allowed, else `CONFIRM_BLOCKED`.
   - The loop is capped at 12 actions per case.

   The oracle reviewer isolates pipeline behaviour from human error. It does **not** model real
   reviewer speed or mistakes.

## Metrics (all computed from actual executions)

| Metric | Definition |
|---|---|
| Detection P/R/F1 | Per encounter and per issue type: an injected type is a TP if any current finding has that code. Registry codes found but not injected are FPs. Reported for all findings and for ROOT findings only. |
| Root-blocker exact match | Share of faulty encounters whose set of ROOT registry codes equals the injected set |
| First-pass yield | Share of all cases at `PREPARATION_COMPLETE` after pass 1 |
| Review rate | Share of cases with at least one review task after pass 1 |
| Review tasks created | Tasks created in pass 1 (workload exposed to reviewers) |
| Issue coverage by tasks | Share of injected issues that have a review task of the same code after pass 1 (workload recall) |
| Human review actions | Corrections plus decisions performed by the oracle reviewer |
| Loop stage executions | Stages actually executed during pass 2 |
| Unnecessary executions | Pass-2 executions whose output hash equals that stage's previous output (the work changed nothing) |
| Reused stages | Stages re-checked but skipped because their input hash was unchanged |
| False-clear rate | Share of faulty encounters reaching `PREPARATION_COMPLETE` in pass 1 |
| Latency | `perf_counter` wall time per pipeline run (software only; SQLite; one process) |
| Modelled human minutes | **Assumption-based:** review actions × 6 min (automated arms). Arm A uses the stage durations below, plus a full redo per correction, plus 6 min per decision. A sensitivity check reports ×0.5 and ×2. |

### Assumed manual stage durations (minutes; NOT measured)

| Stage | Minutes |
|---|---|
| ingestion | 3 |
| data_validation | 10 |
| discharge_readiness | 8 |
| billing | 20 |
| eligibility | 12 |
| adjudication | 15 |
| historical | 0 |
| reconciliation | 8 |
| root-cause | 10 |
| routing | 3 |
| claim_generation | 12 |

No time-and-motion study was performed. These numbers exist only to make the modelled comparison
explicit, and they must not be reported as hospital measurements.

## Reproduce and verify

```powershell
$P = ".\.venv\Scripts\python.exe"
& $P scripts\run_experiments.py --name main               --seeds 1 2 3 --n 200
& $P scripts\run_experiments.py --name main_heldout       --seeds 4 5 6 --n 200
& $P scripts\run_experiments.py --name mimic_demo         --seeds 1 2 3 --mimic-root data/mimic_iv/demo
& $P scripts\run_experiments.py --name mimic_demo_heldout --seeds 4 5 6 --mimic-root data/mimic_iv/demo
& $P scripts\verify_results.py results\experiments\main      # repeat for each run directory
```

**Long runs on a laptop.** Prefix a run with `& $P scripts\run_awake.py --`. It asks Windows not to
sleep while the run lasts, and it changes nothing else. An earlier run was suspended for about 22 h
by system sleep. Its per-case timings were unaffected, but its wall-clock total was meaningless.

**Do not run other CPU-heavy work** (tests, builds) while an experiment runs, because latency is
measured as wall time.

Each run writes:
- `summary.json` (aggregate and per-seed metrics);
- `raw_<arm>_seed<k>.json` (per-case outputs);
- the seeded `data_seed<k>/` datasets.

`verify_results.py` independently recomputes every reported metric from the raw files and the
ground truth, without using the harness's evaluation code. It then checks:
- agreement with `summary.json`;
- completeness of every seed × arm, with full cohorts and no capped or stuck correction loops;
- **cohort equivalence**: identical first-pass findings across arms, with a base database SHA-256
  recorded per seed;
- a listing of every root-blocker error.

It writes `verification.json` and exits non-zero on any problem.

**Documented source anomalies** (`data/mimic_iv/known_source_anomalies.json`) are reported
separately, not hidden. A listed case counts as an expected outcome only when all three hold:
- the anomaly is re-confirmed from the MIMIC files on that run (`--mimic-root`, default
  `data/mimic_iv/demo`);
- the documented finding is a root of the case;
- every other answer-key root is an injected fault.

Without the source files, the case remains a completeness problem.

**Do not run CPU-heavy work in parallel with an experiment**, because it distorts the latency
measurements. Earlier runs are archived under `results/experiments/*_before_error_analysis_fixes`
and `main_v1_unconditional_rules`.

## Software runtime experiment (Agent Control Room)

**Question.** For one encounter, how long does the real workflow take sequentially versus in
dependency-aware parallel mode, and are the outputs identical?

**Procedure.**
1. Copy the SQLite database with the SQLite backup API, under the processing lock. Live data is
   never modified.
2. In the copy, run the real `Orchestrator.process` with the full re-run policy (all 11 stages):
   - first, one warm-up pair (sequential, then parallel), excluded from statistics;
   - then N repetitions per mode, alternating the order (sequential first in even repetitions,
     parallel first in odd ones).
3. Before every run, hash the input snapshot. After every run, fingerprint the final outputs (the
   output hash of every stage plus the case status).

**Measurement.** All times come from `time.perf_counter()` inside the orchestrator:
- **stage interval** = start and end of the stage function call;
- **wall-clock** = run start → end of the last wave;
- **end-to-end** = run start → after the outcome update and commit.

Frontend rendering is never included. Definitions of the derived metrics are in
`app/clearclaim/timing.py`:
- sum of stage durations;
- busy time, as the union of intervals;
- overhead = wall-clock − busy;
- maximum concurrency;
- overlapping pairs;
- critical path through the real DAG, weighted by measured durations.

**Reported.** For each mode: median, minimum, maximum, mean and SD; counts of successful and failed
runs; whether all fingerprints and all input snapshots were identical; and the stage pairs that
actually overlapped.
- Percentage reduction = (median sequential wall − median parallel wall) / median sequential wall ×
  100. It is reported as a slowdown when negative, and only when the sequential median is
  positive.

**Correction comparison.** Two isolated copies of the same state receive the same correction
through the real `apply_correction`; one re-runs with the full policy, the other with the targeted
policy. Executed, reused and not-re-run counts are reported, together with the final output
fingerprints.

**Playback in the user interface.**
- A run takes tens of milliseconds, so the Control Room plays back the recorded events after a run
  ends.
- Playback is slowed (default 500×) and labelled "slower than real" in the HUD.
- Positions, order and durations are the measured values. Playback speed is never reported as
  execution time.

**Separation of time measures.**
- Measured software time (this section) is never added to or compared with the *modelled* human
  minutes (above, assumptions).
- No hospital timings were observed.

## Additions (2026-10-27)

### Reproduction protocol
- Re-run a recorded cohort under a **new name** (never overwrite a recorded run folder). Then run
  `scripts/verify_results.py` on the new folder and compare every aggregate metric with the recorded
  `summary.json`.
- Deterministic metrics (detection, routing, execution counts, false clears, modelled minutes) must
  match exactly. Latency metrics are wall-clock and are expected to vary with machine load; they are
  reported, never substituted. The first reproduction is in `EXPERIMENT_RESULTS.md` §14.

### Leakage control (unchanged, restated)
- Development seeds 1–3 and held-out seeds 4–6 are generated independently. Rules, root-blocker
  causal rules and the note matcher were fixed before the held-out runs, and they are not tuned on
  them. Ground truth lives in a folder the runtime never opens (tested in `tests/test_datasets.py`).

### Interactive measurement in the command center (Execution laboratory)
- **Sequential vs parallel.** `POST /api/v1/control-room/comparisons` runs on an isolated SQLite copy
  with the full re-run policy:
  - 1 discarded warm-up pair;
  - alternating order (sequential first on even repetitions);
  - medians over the chosen repetitions;
  - the input-snapshot hash must be identical for every run, and the output fingerprints must be
    identical before any timing is shown.
- **Full vs targeted after a correction.** `POST /api/v1/control-room/correction-comparisons`
  applies the same correction to two isolated copies, one per policy. It reports executions avoided
  and per-stage output equality.
- **Four time concepts are never mixed:**
  - software execution time (measured ms);
  - critical-path duration (longest dependency chain of measured durations; `timing.critical_path`);
  - re-execution efficiency (counts);
  - hospital workflow time (not measured; only labelled assumptions in the cohort experiments).

### What the 3D interface is and is not evaluated for
The interface is an implemented artefact. Its fidelity to the backend is tested:
`e2e/test_command_center.py` compares node states, edges, rule tiles, peer points, Gantt lanes and
correction marks with API records. Its **usefulness to people is not evaluated** (no user study). A
future protocol would compare the 3D stations with the list view on traceability tasks (find the
root blocker; name the stages a correction re-runs; locate a rule's page), measuring accuracy and time.
