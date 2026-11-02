# NMCT collection policy and readiness gate

## Versioned rule

The administrator route `PUT /api/doctor/insurance/commercial/connections/{connection_id}/collection-policy` accepts `collect_visit_model` with exact `product_binding`, `network_tier`, `service_groups`, effective start/end dates, `max_cents`, source reference, administrator approval, and an explicit `allow_benchmark_advance`. It writes an immutable row in `commercial_collection_policy_versions` and stores active exact-scope visit policies in the connection rules for current reads without replacing the legacy policy. Existing modes remain supported. The feature flag defaults off, and synthetic policy objects in tests do not grant NMCT permission.

Precedence in the first slice: patient/billing prohibition or evidence blocker → documented/covered zero → exact-scope approved visit rule → approved deferral → no authorized collection. A positive modeled share with no matching rule remains visible as planning with `collect_now_cents: null` and `POLICY_APPROVAL_REQUIRED`. An approved defer is `$0` with `DEFERRED_BILLING`; it does not mean final liability is zero. A cross-payer reference never becomes an exact insurer allowance.

Before a positive decision the engine requires patient/policy binding; active visit-date coverage; selected billable groups; applicable benefit interaction; scoped price where required; verified network; policy product/tier/groups/date match; benchmark opt-in when used; no secondary/QMB/adverse requirement blocker; and a verified prior-payment amount including verified zero. The result is capped by the applicable OOP remainder and approved policy maximum, then reduced by verified visit payments. No universal advance percentage or cap exists in code.

## NMCT approval matrix

This is a configuration checklist, not an assertion that NMCT has approved any row. The dated October audit found some legacy `collect_supported_advance` policies, but those do not authorize the new benchmark-derived combined-visit rule.

| Collection situation | Exact approval required before positive `collect_now_cents` | Current first-milestone status |
| --- | --- | --- |
| Verified specialist office copay alone | Product/tier/office/date policy, payment verification, disclosure | Synthetic fixture and calculation pass; real NMCT approval not verified. |
| Independent procedure-day copay | Product/tier/EMG or biopsy/day scope and aggregation with office | Synthetic two-CPT day fixture passes; real approval not verified. |
| Office copay plus procedure deductible/coinsurance | Combined group rule and reference/exact price permission | Synthetic $176.69 modeled fixture passes; real approval not verified. |
| BCBSTX benchmark provisional advance | Explicit `allow_benchmark_advance`, maximum cents and exact product/network/group/date scope | Configurable, no default permission; real approval not verified. |
| Applicable OOP limitation | Verified remaining balance and covered component applicability | $25 synthetic cap passes; unknown remaining cannot establish a zero balance. |
| Insurer/product restriction or protected patient | Source-backed exclusion and precedence | Blockers retained; no policy can override a prohibition. |
| Self-pay alternative | Separate approved cash schedule and patient choice/payer restrictions | Existing cash path unchanged; not mixed into insured modeled share. |
| Deferred collection | Approved defer instruction and wording | Existing route supported; $0 means defer, not waived liability. |

NMCT administrators must review each payer/product/network/service row with billing leadership and enter real approved source references, caps and disclosures. The project cannot be production-ready for benchmark collection until this matrix has real approvals and representative cases are verified. Lack of ledger API means staff must record a ledger reference even when the verified prior payment is $0.
