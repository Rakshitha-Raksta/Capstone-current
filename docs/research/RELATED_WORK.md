# Related Work (focused literature review and comparison)

Search date: 2026-10-21. This was a focused review of about 20 searches, not a systematic review.

**Verification.** Every entry below was opened or resolved through the publisher page, arXiv,
Crossref or Europe PMC. Bibliographic fields were copied from those records.

**Verification status column:**
- **V** means the metadata (and abstract where available) was read.
- **M** means only metadata was read, and the full text was not accessed.

## 1. Health-insurance claim fraud and anomaly detection

| # | Reference | Status | Relevance to this project |
|---|---|---|---|
| L1 | A. du Preez, S. Bhattacharya, P. Beling, E. Bowen, "Fraud detection in healthcare claims using machine learning: A systematic review," *Artificial Intelligence in Medicine*, vol. 160, 2025. doi:10.1016/j.artmed.2024.103061 | V (abstract) | Reviews 137 studies. The field is dominated by supervised and unsupervised ML on large claim histories. Recurring obstacles: class imbalance, scarce labels, interpretability. |
| L2 | H. De Meulemeester, F. De Smet, J. van Dorst, E. Derroitte, B. De Moor, "Explainable unsupervised anomaly detection for healthcare insurance data," *BMC Medical Informatics and Decision Making*, 2025 (PMC11720628). | V | 15 unsupervised detectors plus SHAP on 12.8 M Belgian billing acts, flagging *providers*. Shows that explainability is expected for anomaly flags. Our historical-comparison stage is much simpler: a transparent robust z-score per peer group, advisory only. |
| L3 | "Deep Learning for Anomaly Detection: A Survey," arXiv:1901.03407. | M | Background only. We deliberately do not use deep models, because there is no real labelled claim history. |
| L4 | B. Iglewicz and D. C. Hoaglin, *How to Detect and Handle Outliers*, ASQC Quality Press, 1993. Existence confirmed through a Crossref record of its 1994 *Technometrics* review. | M | Source of the modified z-score (median/MAD) used by our historical-comparison stage. |

## 2. Claim-denial prediction and revenue-cycle management

| # | Reference | Status | Relevance |
|---|---|---|---|
| L5 | M. Johnson, A. Albizri, A. Harfouche, "Responsible Artificial Intelligence in Healthcare: Predicting and Preventing Insurance Claim Denials for Economic and Social Wellbeing," *Information Systems Frontiers*, 25(6), 2023 (online 2021). doi:10.1007/s10796-021-10137-5 | M | Predicts denials from historical claims. This is a *prediction* framing. We instead *check* claims deterministically against cited rules, before submission. |
| L6 | M. M. Jadoon et al., "A machine learning framework for predicting healthcare insurance claim denials," *Frontiers in Health Services*, 2026. doi:10.3389/frhs.2026.1927175 | V | Two-stage Random Forest on 678,881 US claim lines (31.9% denied). Reports PR-AUC 0.777 and top-3 reason-code identification of 38.8% end to end. This is the closest prior work on *identifying the reason* for a claim problem, using learned reason codes on real data. We cannot reproduce this without real denials. |
| L7 | CMS, National Correct Coding Initiative (NCCI): PTP edits, Medically Unlikely Edits and add-on code edits. https://www.cms.gov/national-correct-coding-initiative-ncci | V | Established, rule-based, deterministic claim "edits". Deterministic pre-submission checking is **not new**; it is how payers already work. |

## 3. LLM and multi-agent claim adjudication

| # | Reference | Status | Relevance |
|---|---|---|---|
| L8 | M. L. Hoang and N. Shunmugam, "ClaimPilot: An Open Multi-Agent Pipeline for Veterinary Insurance Claims Adjudication," *Future Internet*, 18(9), 465, 29 Aug 2026. doi:10.3390/fi18090465 | V (abstract; full text returned HTTP 403) | **The closest system-level prior art.** It has specialised LLM agents, *deterministic policy evaluation*, *immutable audit logging*, *mandatory human review*, and a *synthetic benchmark of 150 claims with independently computed ground truth*. It reports 97.3% workflow completion and 90.0% adjudication accuracy. Therefore none of those properties, nor their combination, is claimed as novel here. |
| L9 | "LLMs and Agentic AI in Insurance Decision-Making," arXiv:2508.15110. | M | Survey-style background on agentic insurance processing. |
| L10 | "AI-powered life insurance claims adjudication using LLMs and RAG Architectures" (ResearchGate 390798681). | M (not peer-review verified) | Shows that RAG-based adjudication exists. Our RAG layer is explanatory only and cannot change outcomes. |

## 4. Root-cause analysis over dependency graphs

| # | Reference | Status | Relevance |
|---|---|---|---|
| L11 | S. Zhang, S. Xia, W. Fan, B. Shi, X. Xiong, Z. Zhong, M. Ma, Y. Sun, D. Pei, "Failure Diagnosis in Microservice Systems: A Comprehensive Survey and Analysis," arXiv:2407.01710 (v2 Jan 2025). The search result also lists ACM DOI 10.1145/3715005. | V | Surveys 98 papers. Separating a root cause from propagated symptoms using a dependency graph is **established practice** in software operations (MicroRCA, random-walk methods, GNNs). We transfer the root-versus-consequence distinction to claim-preparation *findings*, using explicit, hand-authored dependency rules rather than learned propagation. |

## 5. Targeted re-execution (incremental computation)

| # | Reference | Status | Relevance |
|---|---|---|---|
| L12 | A. Mokhov, N. Mitchell, S. Peyton Jones, "Build systems à la carte," ICFP 2018; extended in *J. Functional Programming* 30, e11, 2020. | V (metadata/abstract) | Formalises *scheduler* (order of tasks) and *rebuilder* (whether to rebuild) as separate choices, including content-hash ("verifying trace") rebuilders. Our orchestrator is a topological scheduler with an input-hash rebuilder; **the mechanism is borrowed, not invented.** |
| L13 | dbt documentation, state selection `state:modified+` / Slim CI. https://docs.getdbt.com/best-practices/best-practice-workflows | V (secondary pages) | Industrial practice: rebuild only modified nodes and their downstream dependents. |
| L14 | Apache Airflow documentation, DAG Runs: clearing a task with "Downstream". https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html | V (search summary) | Workflow engines already support "clear and re-run downstream". |

## 6. Human-in-the-loop routing

| # | Reference | Status | Relevance |
|---|---|---|---|
| L15 | D. Madras, T. Pitassi, R. Zemel, "Predict Responsibly: Improving Fairness and Accuracy by Learning to Defer," NeurIPS 2018. arXiv:1711.06664 | V | Formal basis for deciding *when an automated system should hand a case to a human*. Our routing is rule-based, not learned. We cite this as the conceptual framing for measuring review workload against detection performance. |

## 7. Indian claims context and datasets

| # | Reference | Status | Relevance |
|---|---|---|---|
| L16 | National Health Claims Exchange (NHCX), NHA and IRDAI under ABDM. FHIR-based claim exchange, live from 2024 according to secondary sources (e.g. NATHEALTH brief). | M (secondary sources) | Our claim packet is "FHIR-shaped". It is **not** NHCX-conformant and is never submitted. |
| L17 | IRDAI Master Circular on Health Insurance, 2024. Already in the dataset as IRDAI-001..010, including the 3-hour discharge-authorisation ceiling (IRDAI-003). | V (repository extract) | Provides real cited regulatory rules. |
| L18 | A. E. W. Johnson et al., "MIMIC-IV, a freely accessible electronic health record dataset," *Scientific Data* 10, 2023. PhysioNet MIMIC-IV **v3.1**, published 11 Oct 2024, credentialed (CITI training plus DUA 1.5.0). https://physionet.org/content/mimiciv/3.1/ | V | Source of the encounter data. The hosp module includes `patients`, `admissions`, `diagnoses_icd`, `procedures_icd`, `drgcodes`, `services` and `transfers`. |
| L19 | MIMIC-IV Clinical Database Demo **v2.2** (31 Jan 2023), 100 patients, open access, ODbL v1.0. https://physionet.org/content/mimic-iv-demo/2.2/ | V | The only real data obtainable without credentials. Supported by our `mimic` mode. |

## Synthesis: what is established

1. Deterministic rule-based claim checking is standard industry practice (L7).
2. ML denial prediction and anomaly detection exist on real data (L1, L2, L5, L6).
3. Multi-agent claim pipelines with deterministic policy evaluation, audit logs, mandatory human
   review and a synthetic ground-truth benchmark have been published (L8).
4. Root-cause analysis on dependency graphs is mature in software operations (L11).
5. Re-executing only the modified nodes and their dependents is textbook build-system and
   data-pipeline practice (L12–L14).

## What was not found in this focused search

None of the sources reviewed applies *finding-level root-blocker versus consequence attribution*
**together with** *dependency-aware selective re-adjudication after a human correction* to
**claim preparation**. None measures the resulting stage executions and review workload against a
full-rerun baseline at equal detection performance.

This absence is from a ~20-search review only. It is a **gap observed in this search, not proof
of novelty.** The defensible contribution wording is in the root `README.md` (Research contribution).

## 8. Additional sources (search of 2026-10-27, extending the review above)

This was a second focused pass of about 12 searches. It covered discharge, incremental computation,
interoperability standards, 3D/immersive visualization and workflow-UI practice, and looked for work
that would weaken the contribution claim. Status letters as above (V = abstract or official page
read; M = metadata or secondary description only).

| # | Reference | Status | What it supports for ClearClaim |
|---|---|---|---|
| L20 | M. Pahlevani, M. Taghavi, P. Vanberkel, "A systematic literature review of predicting patient discharges using statistical methods and machine learning," *Health Care Management Science* 27(3):458–478, 2024. doi:10.1007/s10729-024-09682-7 (PMC11461599) | V (abstract) | 101 papers on forecasting discharge **time, volume and destination**; about half use ML and half use statistical methods. Discharge research is dominated by *prediction*. ClearClaim does not predict discharge; its "discharge readiness" stage is an administrative checklist/document check. The review supports that framing and lists "addressing the primary causes of discharge delay" as an open direction. |
| L21 | Scoping review of delayed hospital discharge (23 reviews, >700 studies), *Health Expectations*, 2024 (via DOAJ record). | M | Attributes discharge delay mainly to gaps in **information flow, coordination and decision rights**. This motivates coordination tooling, but it is **not** evidence that ClearClaim reduces delay; no such claim is made. |
| L22 | U. A. Acar et al., self-adjusting computation: dynamic dependence graphs plus memoization, change propagation (e.g. *ACM TOPLAS* 2009, doi:10.1145/1596527.1596530); parallel change propagation, Anderson, Blelloch, Baweja, Acar, SPAA 2021. | M | Establishes "re-execute only what a change affects, reuse the rest" as a decades-old PL technique. These sources note that asymptotic gains are **not guaranteed**: for many changes, propagation is no faster than recomputation. That matches our honest framing (the gain is counted executions, not guaranteed time). |
| L23 | M. A. Hammer, K. Y. Phang, M. Hicks, J. S. Foster, "Adapton: Composable, Demand-Driven Incremental Computation," PLDI 2014, pp. 156–166. doi:10.1145/2594291.2594324 | V (abstract) | Demand-driven incremental computation with a dependency graph. Further prior art for selective re-execution; strengthens the conclusion that **the mechanism is not our contribution**. |
| L24 | HL7 Da Vinci **Coverage Requirements Discovery (CRD)** IG, STU 2.1 (FHIR R4; uses CDS Hooks and SMART on FHIR). https://hl7.org/fhir/us/davinci-crd/ | V (official IG pages) | Real standards exist for exchanging payer coverage requirements inside provider workflow. ClearClaim is **not** CRD/PAS conformant and implements no FHIR API; its packet is only "FHIR-shaped" (see L16). A deployment would map rule checks to CRD/PAS instead of private formats. |
| L25 | K. Marriott et al., "Immersive Analytics: Time to Reconsider the Value of 3D for Information Visualisation," in *Immersive Analytics*, LNCS 11190, Springer, 2018. | M | 3D has value for some tasks, but its effectiveness is **task-dependent**; most supporting studies use scatterplots and small samples. ClearClaim therefore keeps text, tables and forms in 2D panels and uses 3D for spatial overview, dependencies and navigation. |
| L26 | T. Munzner, *Visualization Analysis and Design*, CRC Press, 2014, Ch. 6 "Rules of Thumb" ("No unjustified 3D"; lecture slides consulted). | M (slides) | Occlusion, perspective distortion and tilted text make 3D weak for abstract data. Design consequences in ClearClaim: screen-aligned HTML labels (never tilted text in 3D), a list fallback, camera presets per task, and planar layouts for the Gantt and rule wall. |
| L27 | Apache Airflow UI documentation (Graph, Gantt and Task Duration views). https://airflow.apache.org/docs/apache-airflow/2.7.0/ui.html | V | Industrial precedent for showing a DAG's run state, per-task duration and overlap (Gantt) to find bottlenecks. ClearClaim's execution lab is the same idea, extruded into the shared 3D space. |
| L28 | Hospital command-centre evaluations: a retrospective interrupted-time-series study at Bradford Royal Infirmary (*Int. J. Qual. Health Care* 2023, White Rose eprint 209410); a scoping review (Franklin et al., 2022); the MUHC health technology assessment (2023). | M | The best controlled study found **no consistent benefit** on patient flow or data quality; positive reports come from uncontrolled before-after designs. "Command center" aesthetics are therefore **not** evidence of operational benefit, and ClearClaim makes no such claim. |
| L29 | Multi-agent LLM systems for prior authorization (arXiv:2404.17977, 2024) and discharge-report generation (repositori.uic.es 20.500.12328/5137). | M | Agentic automation of individual payer/discharge documents exists. Contrast: ClearClaim uses **no** language model in its 11 workflow stages; it targets coordination, attribution and selective re-execution. |
| L30 | E. Antoniadi et al., "Current Challenges and Future Opportunities for XAI in Machine Learning-Based Clinical Decision Support Systems: A Systematic Review," *Applied Sciences* 11(11):5088, 2021. doi:10.3390/app11115088 | V (abstract) | Explanations are needed to avoid over- or under-reliance on automated output. ClearClaim's deterministic rules make explanation direct: rule id, document, page, expected vs recorded value. This is a different and simpler setting than post-hoc ML explanation. |

**Searches that found nothing closer.** No source found in either pass combines all four of: (a) one
dependency graph spanning discharge-readiness, billing and insurance-rule checks, (b) finding-level
root-versus-consequence attribution, (c) selective re-execution after an audited human correction,
and (d) a measured comparison against full re-execution at equal detection. This remains a gap
**in our search**, not a proof of novelty; both passes were focused, not systematic.

## 9. Comparison of approaches by dimension

Reference IDs (L#) point to the tables above. "Our implementation" describes what exists in
this repository, as described in the root `README.md`.

| Dimension | Existing approaches | Our implementation | Evidence and limitations |
|---|---|---|---|
| Policy validation | Payer claim edits such as NCCI (L7). Deterministic policy evaluation inside a multi-agent pipeline (ClaimPilot, L8). LLM/RAG adjudication (L10). | The existing deterministic engine with 59 page-cited rules. It is split into an amount-independent **eligibility** stage and an amount-dependent **adjudication** stage that call the *unchanged* evaluator functions. NEEDS_REVIEW rules are quarantined. | Not novel. The value is the citations: every result carries its document and page. Coverage is limited to 2 insurers and 4 policy versions; 17 HDFC-2026 rules are quarantined because of a UIN conflict. |
| Billing discrepancy detection | ML anomaly detection on billing histories (L1, L2). NCCI MUE unit limits (L7). | **Line-level reconciliation** against a documented synthetic rate card. Checks: quantity × rate arithmetic, unit price versus tariff, duplicates, bed-days versus length of stay, billed procedures with no coded procedure, and header-total residual. | The checks are deterministic and transparent, but the rate card is synthetic (INR). Real hospital tariffs were not available. No ML is used. |
| Historical comparison | Unsupervised detectors with SHAP (L2). | Robust modified z-score (L4) of billed total and LOS within a DRG peer group, computed from **synthetic historical claims**. Advisory only; it never blocks a claim. | Synthetic peer history. A real deployment would need real historical claims. |
| Root-blocker identification | Root-cause analysis in microservices using dependency or propagation graphs (L11). Learned denial reason codes on real US claims (L6). | Explicit **stage-ownership plus finding-dependency rules**. Each finding is labelled ROOT or CONSEQUENCE, with its owner stage, the downstream stages it invalidates, and the next action. | The rules are hand-authored, which keeps them auditable but makes them only as complete as the rule table. Accuracy is measured against injected ground truth. The injector and the rules were written by the same team (circularity is discussed under Limitations in the root `README.md`). |
| Targeted reprocessing | Build systems with dependency and verifying-trace rebuilders (L12). dbt `state:modified+` (L13). Airflow "clear downstream" (L14). | After an authorised correction, the orchestrator invalidates the owning stage and its transitive dependents. Unaffected stage results are kept, and an input-hash check skips stages whose inputs did not change. Old and new outputs are both stored. | **The mechanism is established** (L12–L14). What we contribute is its application and measurement in claim preparation, not the algorithm. |
| Evidence-preserving review | Immutable audit log plus mandatory human review (L8). Learning to defer (L15). | Review tasks carry the rule ID, the document and page, expected versus actual values, the owner stage and the required reruns. Every stage execution, correction and decision is an append-only event. | Not novel in isolation (L8). Our routing is rule-based, not learned (contrast L15). |
| Evaluation | Accuracy on a 150-claim synthetic benchmark (L8). PR-AUC and top-k reason codes on real claims (L6). | A controlled synthetic benchmark with injected single and multiple faults, missing-information cases and legitimate outliers. It compares manual-style simulation, sequential automation and dependency-aware parallel execution, with ablations (no targeted rerun; route all findings). Metrics include detection P/R/F1, root-blocker accuracy, stage executions, review workload and software latency. | No real outcomes are available. Modelled "manual" durations are documented **assumptions**, not measurements. Software latency is never presented as a reduction in hospital discharge time. |
