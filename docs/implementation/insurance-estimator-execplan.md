# Insurance estimator execution plan — October 13, 2026

This is an implementation record for the clinician estimator overhaul. It contains synthetic examples and source locations only; no patient records, insurance identifiers, credentials, raw payer responses, or private document text belong here. The application and infrastructure repositories remain local until a separate release authorization.

## Source and release baseline

- Application branch: `feature/commercial-estimator-overhaul-20261012`, forked from main after PR #145. The working tree was clean at the start.
- Read the workspace context index, application engineering guide, both application instructions, the supplied 11-page result PDF, the primary `Payer Plan Matrix` worksheet and supporting sheets, and the 60-page provider agreement. Confirm document provisions and production behavior from source before relying on this record.
- The supplied result PDF is evidence of a saved production estimate, not a safe test fixture. Reproduce its decision pattern with invented identifiers and no stored PHI.
- Workspace deployment gate and the current task prohibit pushing, merging, production data changes, and deployment without separate authorization. A prior authorization for PR #145 does not authorize this branch.

## Observed gaps to resolve

| Area | Current evidence | Work |
| --- | --- | --- |
| Clinician decision | The printout places a provisional office copay above long technical cards; contract filenames and array paths dominate the first two pages. | Make the amount and scope clear, shorten status cards, move detailed provenance to disclosures, and separate consultation from optional testing. |
| Printing | Print CSS hides all buttons, including the CPT/description labels within service choices, while leaving repeated cash-price boxes. Library diagnostics consume several pages. | Print a stable result summary and selected-service table; omit interactive planner and unrelated library diagnostics. |
| Network and requirements | The clinic has a scoped network contract candidate and workbook PPO research, but the saved plan description does not prove exact member product or provider participation. Stedi's benefit tier is not a provider participation assertion. | Preserve the unresolved state; attempt available provider-specific payer assertions and exact product matching, then surface the missing evidence once in plain language. Do not convert the contract candidate to a false in-network decision. |
| Pricing and additional services | The saved office copay is supported, while separately priced testing lacks an applicable allowed amount and selected CPT scope. | Trace the rate-resolution hierarchy, confirm missing-rate status and source attempts, and maintain partial consultation collection without inventing the testing amount. |
| Financial and identity safety | Existing code and tests appear to cover identity, deductible, OOP, copay, multi-CPT, and ledger-absence branches, but have not yet been audited against the new matrix. | Run the full relevant suite, add missing sanitized regression cases, and document verified coverage versus external limits. |
| Payer policy automation | The workbook is a research index. Current Availity production configuration was demo-only in the preceding authorized read-only review; standard Stedi eligibility does not guarantee provider network participation or CPT-level authorization. | Inspect configured official adapters and cached policy sources. Keep decisions unresolved when no authoritative member/product/CPT evidence is available; identify genuine external integration dependencies. |

## Execution and verification

1. Inspect both repositories, guide sections, source documents, current UI, integration adapters, data models, and existing tests. Record any source limitations without private data.
2. Implement typed, source-backed decisions only where current code has a verified gap; reuse existing identity, benefit, pricing, calculation, and audit modules where correct. Build the missing-rate and policy failure paths rather than silently setting zero or No.
3. Rework the clinician summary, per-CPT view, evidence links, responsive layout, and print/PDF output. Update the live engineering guide in the same change.
4. Run focused backend calculation/identity/library/policy tests, full backend tests, frontend lint/build, UI/print checks, and security/release checks. Distinguish synthetic tests from real provider transactions.
5. Reconcile each acceptance category below with source, tests, and any remaining external dependency. Keep the branch local pending explicit release authorization and the workspace workflow gate.

## Acceptance inventory (initial)

`Present; verify` means implementation exists in the current tree but the newly requested behavior has not yet been independently established. `External` means an authoritative payer, provider, or ledger interface may be needed; it is not a success claim.

| Requirement | Existing entry points | Initial state |
| --- | --- | --- |
| eCW/card/Stedi identity reconciliation and policy selection | `insurance/identity.py`, `insurance/stedi_discovery.py`, `insurance/ecw_backend.py` | Present; verify |
| Plan, product, network, contract participation | `insurance/commercial.py`, `api/routers/commercial_estimator.py`, `insurance/commercial_store.py` | Present; exact member product external |
| PDF/DOCX/XLSX/CSV Library ingestion and indexing | `core/library_files.py`, `insurance/library_pricing.py`, `api/routers/staff.py` | Present; verify extraction limits |
| Contract and cash rate resolution, missing CPT | `insurance/visit_estimate.py`, `insurance/practice_prices.py`, `insurance/library_pricing.py` | Present; verify source attempts |
| Bedrock evidence extraction and CPT candidates | `insurance/cpt_suggester.py`, Library extraction | Present; verify bounded output |
| Per-CPT coverage, referral, authorization and approval | `insurance/contract_requirements.py`, `insurance/plan_matrix.py` | Partial; official member-specific policy access external |
| Eligibility, deductible, copay, coinsurance, OOP, multi-CPT | `insurance/commercial.py`, `insurance/visit_estimate.py` | Present; verify arithmetic |
| Payments, credits and collection recommendation | `insurance/collection_guidance.py`, financial/remittance route | Partial; full eCW ledger access external |
| Clinician UI, per-CPT presentation and direct evidence | `CommercialEstimator.tsx`, `VisitServices.tsx`, CSS | Needs redesign and print fix |
| API/data safety, tenant separation, audit history | commercial routes, stores, tests | Present; verify |
| Deployment readiness and live docs | `api/developer_docs.html`, `.github/workflows/deploy.yml` | Local only; no deployment authorization |

## Verification log

- Baseline focused backend tests: 103 passed, 2 skipped across pricing, contract requirements, plan matrix, commercial normalization and previsit routes.
- Local backend regression after changes: 775 passed, 82 skipped, 6 existing framework warnings (`PYTHONPATH=backend pytest -q testing`). No live payer or eCW request was part of this run.
- Frontend production build passed. ESLint reported 0 errors and 1 existing hook-dependency warning in `InsuranceVerificationV2.tsx`.
- Synthetic Playwright workflow passed for immediate estimator entry, changed services, protected payer-evidence reuse, responsive widths, failure fallback, visible selected-CPT prices and provenance, and the administrator price-gap panel. A coherent failed-refresh fixture prints its selected CPT table with the summary on one Letter page; the synthetic PDF is stored in `contextforcodex`.
- Local CSV parsing of the supplied portal-style fee export produced 19 genuine rows with the metadata footer ignored. These are unactivated candidate rates; exact effective date, payer product, provider and POS scope still require confirmation before any patient calculation.
- Local OpenAPI inspection includes `GET /api/staff/library/missing-rates` and the existing commercial previsit route; live `/api/docs` has not been checked because this branch has not been released.

## Implemented locally

- An uncoded consultation leaves the E/M level unselected; mutually exclusive E/M alternatives keep a common supported office copay counted once. Scheduled CPTs and explicit clinician selections continue to drive actual care scope.
- Each selected CPT now has separate referral and authorization evidence with direct Library links. The visit summary flags an established required CPT without claiming unverified CPTs are clear.
- `encounter_summary` distinguishes today's advance from the complete patient estimate, later balance, components, verified payments, and unpriced codes. Complete totals stay unavailable when network scope or pricing is unresolved; a capped office copay is shown separately.
- The clinician result presents the amount and caveat first, CPT rows next, and source diagnostics in disclosures. A synthetic complex failed-refresh result now prints its summary and selected CPT table on one Letter page.
- The Library parser accepts blank rows and the portal's two-field retrieval footer without silently accepting malformed priced CPT rows. A duplicate diagnostic service-type mapping was removed.
- Each insured CPT line now returns `RESOLVED`, `RATE_UNAVAILABLE` or `RATE_CONFLICT` for the exact allowance candidates supplied to the calculator. This status does not imply that an inaccessible payer portal or contract amendment was searched; cash and nearby CPT prices never replace the missing insured allowance.
- An administrator-only Library inventory aggregates unresolved insured and cash CPT prices from up to 500 recent practice cases, without patient fields. Unselected alternatives are excluded. An exact configured payer mapping supplies the displayed payer label; unknown free text is suppressed. Administrators can upload an approved source through the existing Library workflow and recheck affected visits; old snapshots are preserved.
- Live guide source and route description were updated alongside behavior. No schema migration is required for the case-backed inventory.

## Remaining acceptance and release limits

- Exact patient-specific provider participation is not established by a generic Stedi in-network benefit tier or an unmatched clinic contract. The official payer/provider participation interface available to this clinic has not been verified in this branch.
- Complete CPT-level policy, authorization approval, plan-year status and actual contract allowance require matching authoritative sources. The clinic workbook is a research index; the local fee export does not itself establish an active patient-applicable rate. Unknown stays unverified.
- The full eCW ledger and credits were not available in the inspected appointment response. A staff-verified payment reference can reduce collection once; missing ledger data stays unknown.
- The recent-case inventory is bounded and does not automatically identify or reprice every historical affected visit. A direct single-rate approval workflow and the complete 105-case regression matrix remain outstanding. The local browser run uses synthetic data.
- The inventory uses the existing practice index and limits the scan to 500 recent cases. Query performance on production volume remains unmeasured; no schema change or rollback migration is needed.
- This branch is **No-Go** under the workspace deployment gate: the changed representative workflow has not been verified end to end, and this task separately forbids pushing, merging or deploying without new authorization.

## Regression matrix audit

The supplied 105 scenarios are a review checklist, not 105 individually named tests in this branch. The 775 passing backend tests cover many of the requested paths, but the following mapping is limited to what the test names and assertions demonstrate. It does not replace a real provider transaction.

| Checklist area | Representative local evidence | Unverified boundary |
| --- | --- | --- |
| Identity and card/eCW/Stedi mismatches | `test_commercial_previsit.py`, `test_stedi_discovery.py`, `test_commercial.py` | Live dependent/BlueCard payer response for the representative visit. |
| Plan, product, network and contract scope | `test_commercial.py`, `test_collection_guidance.py`, `test_plan_matrix.py`, `test_contract_requirements.py` | Member-specific provider participation and exact active product. |
| CPT choice, referral and authorization | `test_visit_estimate.py`, `test_contract_requirements.py`, `test_visit_plan_financials.py` | Current payer CPT policy and approval status for each selected service. |
| Contract, cash and missing rates | `test_library_pricing.py`, `test_library_files.py`, `test_visit_estimate.py`, `test_missing_rates.py` | Confirmed activation of the supplied portal export for the exact visit; unimplemented payer portal automation. |
| Deductible, copay, coinsurance, OOP and payments | `test_collection_guidance.py`, `test_encounter_summary.py`, `test_stedi_estimate.py` | Full eCW ledger/credits and actual patient cost estimator result. |
| Failures, reuse, tenant isolation and print | `test_commercial_previsit.py`, `test_stedi_discovery.py`, `test_missing_rates.py`, `result-first-ui.mjs` | Real changed-flow end-to-end and inventory query timing at production volume. |

## Genuine external dependencies under investigation

- A standard Stedi 271 may include an in-network **benefit tier** without a statement that the requested provider participates in the member's exact network. An explicit payer participation message or a member-specific payer/provider directory response can resolve it; an unmatched clinic contract cannot.
- An authenticated BCBS/Availity member-plan/CPT authorization or patient-cost-estimator interface and the practice's actual provider enrollment may be needed for definitive per-CPT determinations. Do not infer this access from an eCW or Stedi login.
- The full eCW financial ledger is not exposed through the currently inspected appointment response. A Stedi 835 feed can show routed processed claims, not all pre-service payments or credits.
