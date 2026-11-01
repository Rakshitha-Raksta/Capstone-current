# HDFC ERGO my:Optima Secure — UIN Conflict Review

## Status: UNRESOLVED — review_status = NEEDS_REVIEW for all affected rules

## 1. UINs observed, by source

| UIN | Source | Source URL | Document date/label | Evidence |
|---|---|---|---|---|
| `HDFHLIP26058V082526` | Official HDFC ERGO Policy Wording PDF | https://customer-portal-assets.hdfcergo.com/documents/PolicyWordings_myOptimaSecure-76673175551.pdf | Linked from official hdfcergo.com downloads page under label "my:Optima Secure (for policies with period of Insurance starting 02-April-2026 onwards)" | Printed on every page of the 60-page Policy Wording document, in the running header/footer. |
| `HDFHLIP25041V062425` | Official HDFC ERGO CIS PDF | https://www.hdfcergo.com/docs/default-source/downloads/cis/cis---myoptimasecure.pdf | No explicit effective-date label on the linked CIS itself | Printed on every page of the 12-page CIS document. Also appears in the hdfcergo.com/download/policy-wordings/health page's own footer disclaimer text (a shared footnote block covering several products). |
| `HDFHLIP25011V052425` | Third-party mirror (ditto-partners S3) | https://ditto-partners.s3.ap-south-1.amazonaws.com/HDFC+Ergo/Optima+Secure-Policy+wording.pdf | Undated capture | Not independently verified against an hdfcergo.com URL during data collection. Not used as a source for any extracted rule. |
| `HDFHLIP23123V022223` | Third-party mirrors (Scribd, PolicyBazaar) | e.g. https://healthstatic.policybazaar.com/health-insurance/Insurer_Document/HDFC/optima_secure_policywordings.pdf | ~2023 vintage | Not independently verified against an hdfcergo.com URL during data collection. Not used as a source for any extracted rule. |
| `HDFHLIP21016V012122` | Official HDFC ERGO archived combined CIS+Policy Wording PDF | https://www.hdfcergo.com/docs/default-source/downloads/policy-wordings/health/myoptima-secure---cis-pww.pdf | "PWW/Ver - 9 AUG2021" / "PWW/Ver - 19 AUG 2021" printed in body | This is the genuinely-recovered 2021 historical version, hosted on HDFC ERGO's own official domain. Used as `HDFC_OPTIMA_SECURE_2021_HISTORICAL` — treated as a separate, correctly-isolated `policy_version_id` (`hdfc_optima_secure_2021_v1`), not affected by the current-version conflict below. |

## 2. The core conflict

Two documents fetched **on the same day, from the same official
`hdfcergo.com` domain family, both linked from the same official
"Download Policy Wordings" page**, for what appears to be the *same*
current product cycle, print **two different UINs**:

- The linked **Policy Wording** PDF (the substantive contract document) prints `HDFHLIP26058V082526` on every page, and its link label states it applies "for policies with period of Insurance starting 02-April-2026 onwards."
- The linked **CIS** PDF (the plain-language summary IRDAI requires alongside every policy) prints `HDFHLIP25041V062425` on every page.

Both are hosted on `hdfcergo.com` / `customer-portal-assets.hdfcergo.com`
domains and both are linked from the live official downloads page
fetched during data collection (https://www.hdfcergo.com/download/policy-wordings/health).

## 3. Likely explanation (not confirmed)

The most plausible explanation is that the CIS PDF at that URL has not
yet been refreshed to match a UIN revision that was applied to the
Policy Wording PDF (products commonly get a new UIN suffix on minor
revisions filed with IRDAI, and insurers do not always republish every
linked document — CIS, brochure, prospectus — in lockstep). This is a
plausible, common occurrence, **not evidence of insurer wrongdoing**,
but it is exactly the kind of discrepancy this project is designed to
catch and flag rather than silently paper over.

## 4. Likely authoritative value

If forced to guess: the **Policy Wording UIN (`HDFHLIP26058V082526`)**
is more likely to be current, because (a) it is the substantive
contractual document IRDAI actually files a UIN against, and (b) its
link label carries an explicit forward-looking effective-date
("02-April-2026 onwards") consistent with being the latest revision.

**This is a guess, not a resolution.** By design, no
UIN is silently chosen as ground truth.

## 5. Confidence

LOW. Both documents are from the same official domain; no third,
independent official tie-breaker (e.g. an IRDAI product-filing search
result, or the actual printed Policy Schedule of a real policyholder)
was consulted during data collection.

## 6. Impact on existing rule candidates

All 17 rule candidates extracted from `HDFC_OPTIMA_SECURE_2026_POLICY_WORDING`
and `HDFC_OPTIMA_SECURE_2026_CIS` (candidate IDs `HDFC26-001` through
`HDFC26-017`) are marked `review_status = NEEDS_REVIEW` in
`data/structured/policy_rule_candidates.csv`, and are consequently
**excluded from automatic deterministic rule-engine evaluation** (the
seeded backend's rule engine only evaluates `PENDING`/`APPROVED`
rules — see `backend/app/rules/engine.py`). This was verified directly:
a synthetic HDFC 2026 claim run through the seeded API
evaluated **zero** rules, precisely because of this flag.

## 7. Recommended next step

A human reviewer with access to an actual current HDFC ERGO
`my:Optima Secure` Policy Schedule (or a direct, browser-rendered visit
to hdfcergo.com, since the text-extraction method used could not
retrieve raw PDF bytes for independent cross-checking) should confirm
which UIN is printed on a live-issued policy schedule, then update
`policy_inventory.csv` and flip the affected rules to `APPROVED` or
`REJECTED` as appropriate.
