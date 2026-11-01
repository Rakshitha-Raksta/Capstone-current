# Root-Blocker and Consequence Rules

Source: `backend/app/clearclaim/stages/analysis.py` (`CAUSAL_RULES`). Tests:
`backend/tests/test_root_blocker.py`.

## How a finding is classified

1. **CONSEQUENCE.** A finding is a consequence if a rule below lists its code and some other current
   finding on the same case matches the rule's root code **and** satisfies the rule's condition. The
   finding records `caused_by`, `causal_rule` and `caused_by_stage`.
2. **ADVISORY / INFO.** Otherwise, ADVISORY and INFO findings stay advisory or informational. They
   never create review tasks.
3. **ROOT.** Otherwise the finding is a root. Every root carries:
   - its owner input domain, owner stage and team;
   - the rerun plan, derived from the stage DAG (stages that read the domain, plus their dependents);
   - whether it can be corrected by a data fix;
   - a next action.

## Rules

| Consequence | Root | Condition (on the two findings' evidence) | Reason |
|---|---|---|---|
| LOS_NOT_VERIFIABLE | MISSING_DISCHARGE_TIME | always | length of stay needs the discharge time |
| LOS_NOT_VERIFIABLE | DISCHARGE_BEFORE_ADMISSION | always | length of stay is undefined |
| LOS_NOT_VERIFIABLE | MISSING_REQUIRED_FIELD | root `field == admittime` | length of stay needs the admission time |
| RULE_NOT_EVALUABLE | MISSING_DISCHARGE_TIME | consequence `rule_category == CLAIM_FILING_DEADLINE` | the filing deadline is measured from discharge |
| RULE_NOT_EVALUABLE | MISSING_REQUIRED_FIELD | root `field == admittime` | date-based rules need the admission date |
| RULE_NOT_EVALUABLE | MISSING_SOURCE_RECORD | root `record == claim_admin` | claim administration dates were never imported |
| NOTE_DX_NOT_CODED | INVALID_ICD_CODE | root `icd_code` ∈ consequence `possible_cause_codes` (the note phrase matches the title of a valid ICD-10-CM code inside the invalid code's own category) | the phrase is the mis-coded diagnosis |
| CODED_DX_NOT_IN_NOTE | INVALID_ICD_CODE | same `seq_num` | the principal code itself is invalid |
| ADJUDICATION_ON_DISPUTED_BILL | any BLOCKING billing root (`*BILLING_ROOT`: LOS_BILLING_MISMATCH, DUPLICATE_BILL_ITEM, BILL_ARITHMETIC_ERROR, UNIT_PRICE_ABOVE_TARIFF, BILLED_PROCEDURE_NOT_DOCUMENTED) | root severity BLOCKING | the estimate used a disputed bill |
| HISTORICAL_OUTLIER | any BLOCKING billing root | consequence \|z_total\| > threshold | the total is inflated by a billing discrepancy |
| HISTORICAL_OUTLIER | MISSING_DISCHARGE_TIME | always | the comparison used an unverifiable stay |
| MISSING_KYC_DOCUMENT | any BLOCKING billing root | consequence `verified_total` ≤ `kyc_threshold`, where `verified_total` = bill − the **minimum** plausible dispute (excess bed-days at the cheapest room rate; other disputes exact), so KYC is discounted only if the bill is below the HDFC21-011 threshold for certain | KYC is triggered only by over-billing |

## Safety rules (claim preparation)

- **No routing gap blocks silently.** If any BLOCKING root has no review task, or any BLOCKING
  consequence exists whose root was not explicitly accepted, the case is `BLOCKED`.
- **Uncertainty goes to a person.** Any open REVIEW task gives `NEEDS_REVIEW`. Uncertain or
  unevaluable results (`RULE_NOT_EVALUABLE`, `UNKNOWN_BILL_ITEM`, `DISCHARGE_NOTE_MISSING`,
  quarantined rules) are REVIEW findings. They are never passed silently.
- **Failures never pass.** A stage failure sets the case to `ERROR`. Earlier good results stay
  current, and dependents are skipped.
- **Corrections need evidence of resolution.** A correctable BLOCKING task only allows
  `CORRECT_DATA`. The API rejects "accept" decisions on it with 422.

## Changes from the first version

The first version used **unconditional** links, for example "any `NOTE_DX_NOT_CODED` is a
consequence whenever an `INVALID_ICD_CODE` exists". In the seed-1 experiment this misattributed one
independently injected documentation gap (see `research/EXPERIMENT_RESULTS.md`).

The conditions above, and the `possible_cause_codes` evidence computed in data validation, replace
those links. The regression test is
`test_independent_note_gap_is_not_misattributed_to_unrelated_invalid_code`.
