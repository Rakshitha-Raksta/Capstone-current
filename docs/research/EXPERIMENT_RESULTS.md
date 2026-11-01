# Results

**Provenance of every number below.** All figures were recomputed independently by
`scripts/verify_results.py` from two sources: the saved per-case raw outputs
(`raw_<arm>_seed<k>.json`) and the separate ground-truth files
(`data_seed<k>/ground_truth/*.csv`). That script does not use the harness's own evaluation code.
Each recomputation was compared with the harness `summary.json`: **0 mismatches in all four final
runs** (verified 2026-10-22). The verifier exits 0 for all four runs. In each MIMIC run, the 12 cases of real case 26529390 are reported separately as a confirmed documented source anomaly (§7); they are not hidden. An earlier version of the verifier, which did not distinguish this case, exited 1 on the MIMIC runs.

**Scope.** These results describe software behaviour on controlled cohorts:
- **Synthetic cohorts:** everything is synthetic.
- **MIMIC cohorts:** clinical encounters are real (MIMIC-IV demo); billing, insurance,
  administrative data, notes and every injected fault are synthetic.

They are **not** evidence about real hospitals, real claims, insurer outcomes or discharge time.
Statuses are internal claim-preparation states, never insurer decisions.

## 1. Final runs (all on the final code)

| Run | Cohort | Seeds | Encounters per seed | Raw files | Status |
|---|---|---|---|---|---|
| `results/experiments/main` | Synthetic, development | 1, 2, 3 | 200 | 12/12 | **Complete**, verified |
| `results/experiments/main_heldout` | Synthetic, **held-out** | 4, 5, 6 | 200 | 12/12 | **Complete**, verified |
| `results/experiments/mimic_demo` | Real MIMIC-IV demo encounters + synthetic layer (generator v2.2.1), development | 1, 2, 3 | 275 | 12/12 | **Complete**, verified |
| `results/experiments/mimic_demo_heldout` | Same, **held-out** | 4, 5, 6 | 275 | 12/12 | **Complete**, verified |

The two MIMIC runs were **rerun on 2026-10-22** after the ICU care-unit pattern fix (generator v2.2.1, §9). Synthetic data is byte-identical under that fix, so the synthetic runs were not rerun.

**48 of 48** seed × arm combinations completed, and every run exited with code 0.

**Commands.** Run from the repository root with the project venv:

```
.venv\Scripts\python.exe scripts\run_experiments.py --name main --seeds 1 2 3 --n 200
.venv\Scripts\python.exe scripts\run_experiments.py --name main_heldout --seeds 4 5 6 --n 200
.venv\Scripts\python.exe scripts\run_experiments.py --name mimic_demo --seeds 1 2 3 --mimic-root data/mimic_iv/demo
.venv\Scripts\python.exe scripts\run_experiments.py --name mimic_demo_heldout --seeds 4 5 6 --mimic-root data/mimic_iv/demo
.venv\Scripts\python.exe scripts\verify_results.py results\experiments\<run>
```

**Held-out seeds.** Seeds 4–6 were generated and run only after every detector and causal-rule fix
(section 7) was frozen. No code was changed in response to held-out results.

**Wall-clock note.** Elapsed times were 246 s, 586 s, 500 s and 487 s. Both MIMIC reruns ran under
`scripts/run_awake.py`, which stops Windows sleeping during a run.
- The previous MIMIC held-out run, now archived, recorded 82,179 s: the PC slept for about 22 h
  mid-run (Windows Kernel-Power events). That run was not stalled, and its per-case timings were
  unaffected (maximum 918 ms).

## 2. Definitions and denominators

- **Unit:** one encounter (a claim-preparation case).
- **Statistics:** mean ± SD is across 3 seeds (n = 3). "Pooled" means summed over seeds.
- **Loop stage executions:** stages actually executed during the correction loop (pass 2).
- **Reduction:** `1 − treatment / baseline`, on seed means.
- **Unnecessary execution:** an execution whose output hash equals that stage's previous output.
- **Issue coverage:** injected issues with a review task of the same code after pass 1, divided by
  all injected issues.
- **Detection:** per encounter and issue code. "All findings" includes consequences.
- **Misattributed:** an injected issue found only as a CONSEQUENCE, never as a ROOT.
- **Spurious root:** a ROOT finding with no matching injected issue in a faulty encounter.
- **False clear:** a faulty encounter that reaches `PREPARATION_COMPLETE` in pass 1.
- **Latency:** `perf_counter` wall time per pipeline run (software only, SQLite, one laptop). It is
  never presented as administrative or discharge time.

**Arms.**
- **B:** sequential, full rerun after every correction (baseline).
- **C:** parallel waves, targeted rerun with hash reuse, root-only routing (treatment).
- **C − targeted:** C with full rerun.
- **C − root routing:** C with routing of all findings.

**Cohort equivalence (all runs).**
- Within each seed, all four arms start from one database file with a recorded SHA-256, listed in
  each `verification.json`.
- Their first-pass statuses, findings, roots and consequences are identical case by case: **0
  differences in all 12 seeds**.
- The arms therefore differ only in rebuild policy, execution mode and routing.

## 3. Headline results (treatment C vs baseline B)

| Metric | Synthetic dev | Synthetic held-out | MIMIC dev | MIMIC held-out |
|---|---|---|---|---|
| Injected issues (pooled) / faulty encounters | 381 / 283 | 385 / 276 | 642 / 452 | 705 / 481 |
| Correction-loop stage executions, B → C (mean per seed) | 1793 → 734.3 | 1760 → 780.7 | 3355 → 1276 | 3509 → 1357.7 |
| **Loop execution reduction** | **59.0%** | **55.6%** | **62.0%** | **61.3%** |
| Unnecessary re-executions, B → C | 1163 → 104.3 | 1099.3 → 120 | 2273.7 → 194.7 | 2354.7 → 203.3 |
| **Unnecessary re-execution reduction** | **91.0%** | **89.1%** | **91.4%** | **91.4%** |
| Pass-1 review tasks, all findings → root only | 202 → 163.3 | 206.7 → 160 | 367 → 311.3 | 391.3 → 325 |
| **Review-task reduction (root routing)** | **19.1%** | **22.6%** | **15.2%** | **17.0%** |
| Issue coverage by tasks (both routings) | 99.74% | 99.74% | 98.44% | 97.87% |
| Detection recall, pooled | 99.74% | 99.74% | 98.44% | 97.87% |
| Detection precision, all findings, pooled | 95.72% | 94.35% | 98.44% | 98.85% |
| Misattributed issues | **0** | **0** | **0** | **0** |
| False clears | 1 of 283 | 0 of 276 | 3 of 452 | 3 of 481 |
| Root-blocker exact match (faulty encounters) | 99.29% | 99.64% | 96.90% | 96.05% |
| Human review actions (oracle), per seed | 163 | 160 | 305 | 319 |

**Detection, routing and human actions are identical in arms B, C and C − targeted.** Those arms
run the same stage functions and produced identical first-pass outputs.

## 4. Per-seed values (recomputed)

| Run | Seed | Loop exec B | Loop exec C | Unnecessary B | Unnecessary C | Tasks all → root | Issues | Missed | False clears |
|---|---|---|---|---|---|---|---|---|---|
| main | 1 | 1628 | 698 | 1034 | 104 | 188 → 148 | 115 | 0 | 0 |
| main | 2 | 1881 | 744 | 1247 | 110 | 208 → 171 | 133 | 0 | 0 |
| main | 3 | 1870 | 761 | 1208 | 99 | 210 → 171 | 133 | 1 | 1 |
| main_heldout | 4 | 1804 | 796 | 1126 | 118 | 214 → 164 | 130 | 1 | 0 |
| main_heldout | 5 | 1694 | 694 | 1111 | 111 | 183 → 154 | 121 | 0 | 0 |
| main_heldout | 6 | 1782 | 852 | 1061 | 131 | 223 → 162 | 134 | 0 | 0 |
| mimic_demo | 1 | 3355 | 1235 | 2314 | 194 | 359 → 311 | 207 | 5 | 2 |
| mimic_demo | 2 | 3542 | 1361 | 2396 | 215 | 391 → 329 | 239 | 1 | 0 |
| mimic_demo | 3 | 3168 | 1232 | 2111 | 175 | 351 → 294 | 196 | 4 | 1 |
| mimic_demo_heldout | 4 | 3443 | 1318 | 2310 | 185 | 378 → 318 | 218 | 4 | 1 |
| mimic_demo_heldout | 5 | 3388 | 1317 | 2277 | 206 | 379 → 316 | 226 | 5 | 0 |
| mimic_demo_heldout | 6 | 3696 | 1438 | 2477 | 219 | 416 → 341 | 261 | 6 | 2 |

The direction of every software metric is the same in all 12 seeds.

## 5. Findings against the hypothesis

1. **Fewer unnecessary stage executions: supported and reproduced on held-out seeds and real
   encounters.**
   - Targeted re-execution cut correction-loop executions by 55.6–62.0% and identical-output
     re-executions by 89.1–91.4%.
   - C − targeted returns exactly to the baseline counts, so the effect comes from the rebuild
     policy, not from parallelism.
2. **Detection is preserved, by construction.** First-pass outputs are identical case by case
   across arms. The rebuild policy changes *when* stages run, not *what* they compute.
3. **Review workload: supported in all four cohorts.**
   - Root-only routing created 15.2–22.6% fewer review tasks, with **identical issue coverage** to
     all-findings routing and **0 misattributed issues**.
   - Human actions are unchanged under the oracle reviewer: consequence tasks resolve
     automatically once roots are fixed.
   - Real reviewer behaviour was **not** measured.
4. **Parallel execution: no reliable latency benefit.**
   - Median first-pass latency for C is within noise of B's in every run: 15.4 vs 16.6, 47.0 vs
     41.3, 30.0 vs 29.8 and 24.6 vs 24.3 ms. Two runs favour each arm.
   - Only one wave (billing ∥ eligibility) can run concurrently, and at millisecond scale thread
     overhead dominates.
   - We make **no** claim for parallelism.

## 6. Latency (software only; noisy across sessions)

Median per-case latency in ms, mean ± SD across seeds:

| Run | First pass, B | First pass, C | Rerun, B | **Rerun, C** |
|---|---|---|---|---|
| main | 16.6 ± 2.7 | 15.4 ± 0.7 | 24.0 ± 0.3 | **14.8 ± 0.6** |
| main_heldout | 41.3 ± 1.6 | 47.0 ± 3.8 | 64.8 ± 1.4 | **44.5 ± 2.7** |
| mimic_demo | 29.8 ± 6.8 | 30.0 ± 12.4 | 40.8 ± 9.4 | **17.2 ± 4.4** |
| mimic_demo_heldout | 24.3 ± 2.1 | 24.6 ± 2.9 | 35.2 ± 3.8 | **16.3 ± 2.8** |

- **Within a run**, targeted reruns are 31–58% faster than full reruns.
- **Across runs**, absolute latency is not comparable, because background load on the laptop
  varied. `main_heldout` ran about 3× slower than `main`. The MIMIC reruns, made on an idle
  machine, ran about 2.5× faster than the archived MIMIC runs, which used near-identical code.
- Only within-run, same-seed comparisons are meaningful.

## 7. Error analysis (treatment arm; every case listed in `verification.json`)

**Missed issues (false negatives): 27 in total, and every one is `CODED_DX_NOT_IN_NOTE`.** Every
other injected issue type was detected in 100% of cases in all four runs.

| Cohort | `CODED_DX_NOT_IN_NOTE` recall |
|---|---|
| Synthetic dev | 14/15 |
| Synthetic held-out | 23/24 |
| MIMIC dev | 21/31 |
| MIMIC held-out | 25/40 |

- **The detector is token overlap between diagnosis titles and the note.** With real ICD titles it
  is fooled when a remaining diagnosis shares the key words. For example, "Abscess of liver" is
  "matched" by "...necrosis of liver", and synthetic case 98000070's "Sepsis, unspecified
  organism" is matched by "Severe sepsis".
- On real encounters its recall is **65% (46/71)**. **This is the weakest component, and the
  only source of false clears.** All 7 false clears (1 + 0 + 3 + 3) are cases whose only injected
  issue was a missed `CODED_DX_NOT_IN_NOTE`.
- The matcher was **not tuned** after seeing these cases, to avoid fitting the detector to the test
  cohorts.

**Spurious roots (over-attribution; safe direction):**
- **`MISSING_KYC_DOCUMENT` kept as a root beside `LOS_BILLING_MISMATCH`: 6 cases.**
  - The cases are synthetic 98000040, MIMIC dev 22059910, 27154822 and 24928679, and MIMIC
    held-out 23819016 and 28019404. Every one has an injected `LOS_BILLING_MISMATCH`.
  - The injector inflates a random room line, which can be the ICU line. The KYC causal rule values
    the excess days at the *cheapest* room rate, so it can prove "the true bill is under the KYC
    threshold" only conservatively.
  - When it cannot prove that, KYC stays a root. The reviewer gets one extra task, but the case is
    never cleared wrongly. This lower-bound valuation replaced the earlier max-rate valuation, which
    had misattributed real case 24104168.
- **Real case 26529390: a genuine source-data anomaly, kept visible.**
  - **The record:** in the real MIMIC data, admission 26529390 (2155-05-17 to 05-19) has 5 coded
    procedures. 4 of them are dated 05-28, 06-12 and 06-13. Those dates fall inside the same
    patient's next two admissions (05-22 to 05-30 and 06-10 to 06-15), which is consistent with a
    source-side admission linkage error. It is the only such admission in the demo.
  - **ClearClaim's behaviour:** it correctly reports `PROCEDURE_DATE_OUT_OF_RANGE` as a blocking
    root in every arm and seed. The answer key lists only injected faults, so this finding shows as
    a spurious root in the 3 seed-cases where the case also received injected faults.
  - **Why it ends `BLOCKED`:** the oracle reviewer has no recorded fix for it.
- **Oracle early stop (harness limitation; affects only this case).**
  - The oracle works on the highest-priority open task and stops a case when that task has no
    recorded fix (`harness.py`, `run_arm`).
  - For 26529390 the anomaly's BLOCKING tasks come first. In dev seed 2 and held-out seeds 5 and 6,
    the **6 injected faults in this case were therefore never corrected, in all four arms
    alike**.
  - Arm comparisons are unaffected, but those faults contribute no correction-loop work.
  - No other case in any run stops early. Every other unused fix belongs to a missed
    `CODED_DX_NOT_IN_NOTE` with no task. The oracle was not changed after the results, to avoid
    a post-hoc methodology change.
- **How the verifier treats it.**
  - The case is listed in `data/mimic_iv/known_source_anomalies.json`.
  - `verify_results.py` reports it separately as "BLOCKED by a confirmed documented source
    anomaly" (12 per MIMIC run), but only if the anomaly is **re-confirmed from the MIMIC files
    on that run** and every other answer-key root is an injected fault. Otherwise it remains a
    completeness problem and the verifier exits 1 (tested, including with the source files
    missing).
  - Before this rule existed, the verifier exited 1 on every MIMIC run because of this case. A new
    real regression would then have been indistinguishable from it.

**False positives (all findings):**
- **Synthetic:** 17 (dev) and 23 (held-out), mostly `NOTE_DX_NOT_CODED` where an injected
  `INVALID_ICD_CODE` removed the title the note phrase would have matched. They are attributed as
  consequences, so they create no task. ROOT precision is 99.75% (dev) and 100% (held-out).
- **MIMIC:** 10 (dev) and 8 (held-out). ROOT precision is 99.03% and 99.26%.

**Tasks without a matching injected issue** (dev / held-out, per seed):
- **Synthetic: 36.7 / 32.** Pooled over the dev and held-out runs respectively:
  - `RULES_QUARANTINED`: 104 / 90 (HDFC-2026 rules awaiting manual source review because of a UIN
    conflict; intended governance, not error);
  - `EXCLUSION_KEYWORD_MATCH`: 5 / 6;
  - `MISSING_KYC_DOCUMENT`: 1 / 0.
- **MIMIC: 100.7 / 95.0.** Pooled over the dev and held-out runs respectively:
  - `EXCLUSION_KEYWORD_MATCH`: 143 / 144, from the unchanged legacy exclusion evaluator on real
    diagnosis titles;
  - `RULES_QUARANTINED`: 115 / 101;
  - `RULE_NOT_EVALUABLE`: 29 / 26;
  - `PROCEDURE_DATE_OUT_OF_RANGE`: 12 / 12 (real case 26529390);
  - `MISSING_KYC_DOCUMENT`: 3 / 2 (see spurious roots above).

**Legitimate outliers.** There are 35 (dev) and 42 (held-out). One is `BLOCKED` (dev seed 2,
encounter 98000145), and its cause is an injected `WAITING_PERIOD_NOT_MET` (tenure 830 < 1,080
days), not the outlier flag. Held-out: 0 blocked.

**Loop completeness.**
- No case hit the 12-action loop cap, ended `NEEDS_REVIEW`, or ended in an ERROR status.
- Every final `BLOCKED` case has a non-correctable policy blocker confirmed by the reviewer
  (waiting period or inactive policy), except the documented source anomaly 26529390 above.

## 8. MIMIC cohorts: what is real and what is synthetic

| Real (MIMIC-IV Clinical Database Demo v2.2, SHA-256 verified, unmodified) | Synthetic (generator v2.2.1, labelled in data and UI) |
|---|---|
| patients (sex, anchor age), admissions (admit, discharge, ED registration, death), diagnoses_icd (ICD-9/10), procedures_icd with dates, drgcodes (MS-DRG / APR), transfers (care units), services, prescriptions (drug names), ICD dictionaries | hospital bills and tariff (INR), member policies and insurer rules linkage, claim admin dates, KYC and claim documents, discharge checklist, discharge notes (derived from structured data), historical peer claims, **every injected fault and the answer key** |

- Faults are injected **only** into the synthetic layer; real clinical rows are never modified.
- MIMIC-linked claims are **simulations**, not real insurer outcomes. The real encounters are US
  (BIDMC) and the rules are Indian; the linkage is a deliberate simulation device.

## 9. Run history (all archived runs kept and verified)

| Archived run | What changed afterwards | Effect |
|---|---|---|
| `main_v1_unconditional_rules` | Causal rules made conditional on evidence | 1 misattribution → 0 |
| `main_v2_before_error_analysis_fixes`, `mimic_demo_v1_before_error_analysis_fixes` | Real-data defects fixed: ICD-10 version mismatch (60 real admissions falsely blocked), MS-DRG-only peers, case-insensitive drug pricing, policy cover through readmissions, ICU name patterns, ED-window procedure dates, repeated procedure codes, KYC decided on the pre-fault bill | False findings on real data removed. All fixes have regression tests (`tests/test_root_blocker.py`) |
| `archive_pre_kyc_bound_fix/{main,main_heldout,mimic_demo,mimic_demo_heldout}` | KYC rule uses the minimum disputed amount | Real case 24104168 misattribution → 0. Loop and task reductions within 0.5 pp of the final runs |
| `archive_pre_icu_pattern_fix/{mimic_demo,mimic_demo_heldout}` (verified: 0 mismatches) | ICU care-unit pattern fixed (generator v2.2.1). Its `\b` word boundaries had been stored as backspace characters, so real "Trauma SICU (TSICU)" stays (16 admissions) were billed as ward days | The synthetic layer changed for those admissions, so seeded fault draws shifted, mostly in MIMIC dev seed 3. Headline reductions moved by at most 0.4 pp. Includes the run suspended by system sleep |

Archived runs are history, not results. The final figures are those in sections 3–8.

## 10. Modelled time (assumptions, not measurements)

| Run | Automated arms: human minutes per case | Manual model (arm A): minutes per case |
|---|---|---|
| main | 4.9 | 144.8 |
| main_heldout | 4.8 | 149.0 |
| mimic_demo | 6.7 | 153.3 |
| mimic_demo_heldout | 7.0 | 156.2 |

- **Automated arms:** review actions × an assumed 6 minutes, per case.
- **Manual model (arm A):** built from assumed stage durations (`EVALUATION_PROTOCOL.md`); varying
  them ×0.5 or ×2 scales the result proportionally.
- These are **not** time-and-motion measurements and must not be quoted as time savings.

## 12. Software runtime: sequential vs parallel (Agent Control Room, 2026-10-23)

**What was measured.** Backend execution time of the 11 real stages for one encounter, run by the
same orchestrator in sequential and in dependency-aware parallel mode. The full re-run policy was
used, so all 11 stages execute in both modes. The method is in `EVALUATION_PROTOCOL.md` →
*Software runtime experiment*.

**Setup.**
- **Encounter:** 98000056, synthetic seed-42 demo, in its original blocked state.
- **Isolation:** an isolated SQLite copy of the database.
- **Repetitions:** 10 per mode, after one discarded warm-up pair, in alternating order.
- **Machine:**
  - Windows 11 (build 26200);
  - Intel Core, family 6 model 142, 8 logical CPUs;
  - Python 3.11.5;
  - laptop with other applications running.
- **Record:** Control Room record #1 on the scratch database. Export the CSV or JSON for the raw values.

| Metric (median over 10 runs; min – max) | Sequential | Parallel |
|---|---|---|
| Wall-clock (run start → last stage done and staged) | 72.0 ms (52.1 – 148.3) | 74.1 ms (50.8 – 129.1) |
| End-to-end incl. outcome update and commit | 104.8 ms (80.6 – 195.0) | 107.8 ms (73.8 – 184.3) |
| Sum of stage durations | 8.23 ms | 10.38 ms |
| Critical path (measured durations) | 7.88 ms | 9.58 ms |
| Overhead outside stage functions | 61.1 ms | 64.6 ms |
| Max concurrency observed | 1 | 1 (0 of 10 runs had any overlap) |
| Final outputs | identical in all 22 runs (one fingerprint); same input snapshot | |

**Finding.** Parallel execution was **2.8% slower** by median wall-clock time; the absolute
difference (2.0 ms) is far smaller than the run-to-run spread.
- Only billing and eligibility are independent, and each takes about 0.5–2 ms. Thread start-up
  cost is of the same order, so in no measured run did the two actually overlap.
- About 85% of wall-clock time is spent outside the stage functions: snapshot building, input
  hashing and staging database rows.
- **The real workflow therefore gains nothing from parallelism at this scale.** This confirms the
  earlier finding in §5, finding 4.
- A unit test (`test_control_room.py`) shows the parallel path does overlap and shorten wall-clock
  time when the two independent stages are slow (60 ms of simulated work each). That is a property
  of the scheduler, not a measured property of ClearClaim's real stages.

**Replication, 2026-10-23** (rebuilt Control Room; same encounter, machine and method; a fresh
isolated copy; 10 repetitions per mode; record #3):

| Median (min – max) | Sequential | Parallel |
|---|---|---|
| Wall-clock | 59.4 ms (46.9 – 73.3) | 60.5 ms (48.5 – 125.5) |
| End-to-end | 88.0 ms | 89.2 ms |
| Sum of stage durations | 8.71 ms | 8.40 ms |

- Parallel was **1.9% slower**.
- Outputs were identical in all 22 runs, and no parallel run had overlapping stages.
- The correction comparison (record #4) again executed 8, reused 1 and did not re-run 2 stages,
  versus 11 for the full re-run, with identical final outputs.

**Correction, full vs targeted** (same encounter, Control Room record #2, one run each on two
isolated copies):
- The targeted policy executed **8 stages, reused 1 and did not re-run 2**; the full policy executed
  all 11.
- Final outputs were identical, and the status became Preparation complete in both.
- Single-run wall-clock times (full 85.5 ms, targeted 106.5 ms) are dominated by overhead and
  noise, so they show no runtime saving. The measured saving is in executions (3 of 11), not
  milliseconds, at this scale.

## 13. Threats to validity

- **Circularity.** The same team wrote the fault injector, the detectors and the causal rules, so
  synthetic detection and root-blocker figures are optimistic upper bounds. The real-encounter
  `CODED_DX_NOT_IN_NOTE` recall (65%) shows how much harder real text is.
- **Oracle reviewer.** No real reviewers took part; there was no user study and no time study.
- **Small n for variance.** Three seeds per cohort give a rough SD only, with no significance
  testing.
- **Latency** is noisy across sessions (section 6) and was measured on one laptop with SQLite.
- **Real data is only 100 patients** (the open demo, 275 admissions, reused across seeds with
  different synthetic layers). Credentialed MIMIC-IV was not used.

## 14. Independent reproduction (2026-10-27)

**What was re-run.** The synthetic development cohort, exactly as recorded in §1:

```powershell
.\.venv\Scripts\python.exe scripts\run_awake.py -- .\.venv\Scripts\python.exe scripts\run_experiments.py --name repro_2026-10-27 --seeds 1 2 3 --n 200
.\.venv\Scripts\python.exe scripts\verify_results.py results\experiments\repro_2026-10-27
```

The harness generates each seed's dataset and runs every arm on its own SQLite copy, so neither the
development database nor the recorded `results/experiments/main` folder was touched. The output is
in `results/experiments/repro_2026-10-27/`.

**Outcome.**

| Check | Result |
|---|---|
| Verifier: mismatches vs summary.json | **0** |
| Cohort equivalence differences (seeds 1/2/3) | **0 / 0 / 0** |
| Completeness problems | **0** |
| Root-blocker errors in the treatment arm | 2, the same two cases as the recorded run (98000040 spurious KYC root; 98000070 `CODED_DX_NOT_IN_NOTE` missed) |
| Detection, routing, execution-count, false-clear and modelled-time metrics, all arms | **73 of 73 identical** (mean and sd) to `main/summary.json` |
| Latency metrics (12: first-pass median and p95, re-run median, per arm) | **Differ.** Higher and noisier, e.g. treatment re-run median 25.1 ms (sd 7.1) vs 14.8 ms (sd 0.6) recorded |

**Interpretation.** Every deterministic result reproduces exactly. That covers the 55.6–62.0% /
89.1–91.4% correction-loop reductions, the routing reduction and detection performance. Latencies do
not reproduce exactly. That is expected: they are wall-clock measurements, and this run shared the
machine with a concurrently running browser test suite. This is why §6 treats latency as noisy
across sessions and the protocol makes no latency claim. The recorded latencies in §1–§6 are left
unchanged, and the reproduction's latencies are reported here as they came out.
