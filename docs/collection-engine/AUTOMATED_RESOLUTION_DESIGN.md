# Automated resolution and deterministic visit decision

## First-milestone contract

`visit_collection_model.build(result, review)` reads the existing previsit result and returns `estimated_patient_responsibility_cents`, `collect_now_cents`, decision type, pricing basis, service-group trace and remaining actions. It does not write data or call a payer. `collection_guidance.build` attaches that result under `visit_model` when the feature flag is enabled. When the new model yields a complete numeric planning estimate, the legacy `amount_cents` is a backend alias of its authorized collection amount. Saved-case reload recomputes against current approved policy and scoped rates, retaining the source evidence in the case snapshot.

## Resolution and calculation order

1. Bind current appointment, patient, subscriber, medical policy and service date. Identity conflicts, multiple primary policies or stale evidence stay separate; independent CPT and price research can continue.
2. Use already saved eCW and Stedi evidence. Prefer a specifically applicable exact CPT/service benefit over a general STC row. Preserve row IDs and network/date applicability. A staff note cannot verify a payer accumulator.
3. Resolve selected CPTs into specialist office, EMG/NCS day, skin biopsy or other billable groups. The existing clinician edit path changes CPT, unit and modifier selections and triggers backend recomputation. Do not add EMG from diagnosis alone.
4. Resolve a dated exact target-payer rate first. Otherwise match an active scoped BCBSTX rate by CPT, modifier, provider/location, POS and date. Cross-payer basis is `BCBSTX_CROSS_PAYER_REFERENCE`, never the target payer allowance. Missing or conflicting rate rows remain unpriced.
5. Select one supported interaction per group. Combined patterns require an interaction rule tied to a selected source ID and a structured base or component split. Unknown interaction produces no fabricated total; Bedrock cannot insert a rule into the calculator.
6. Count a group/day copay once, deduplicate the same encounter copay ID across groups, share a deductible ID once across eligible groups, apply `ROUND_HALF_UP` at group boundary, and cap covered groups by one verified applicable OOP remaining balance. Do not infer a remaining balance from annual maximum.
7. Apply an approved visit policy only when exact product, tier, service groups, date, benchmark permission, prior-payment evidence and billing restrictions all match. A missing positive policy yields a numeric planning amount and null collection; an approved defer gives an explicitly labeled $0.

For CPT 95908 ×1 at $105.11 and CPT 95886 ×2 at $89.18 each, reference total is $283.47. With $100 remaining deductible and 20% post-deductible coinsurance, share is `$100 + round_half_up(0.20 × $183.47) = $136.69`. An independent $40 office copay produces $176.69 modeled for the combined visit.

## Bounded Sonnet 5 advisory

`insurance_research_agent.py` uses Bedrock Converse model `us.anthropic.claude-sonnet-5` when selected saved terms contain controlled review flags. It offers five typed, read-only tools for saved eCW reconciliation, selected benefit facts, scoped Library reference price, service template and policy scope. The model may make at most three tool calls in one round; each of at most two Converse requests has a 3-second connection timeout, 15-second read timeout, zero retries and 500 output tokens. The returned finding must cite selected benefit IDs and has no calculation or policy authority. Transport, budget, tool validation and JSON failures become `research_incomplete`. The wider official payer research and phone resolver remain after the milestone gate.

Staging has no live eCW backend configuration and Stedi live eligibility is disabled. The first integration acceptance path therefore uses the exact-staging-host, NMCT-admin-only `POST /api/staff/testing/collection-engine-scenarios` route to create eight marked fake patients with invented appointment, Coverage and benefit evidence. Their saved reads recalculate from fixture evidence through the shared money and policy engine and render in React with a synthetic banner; they deliberately bypass live payer and Library refresh, so this exercise tests the downstream integration only. Pair it with the protected read-only October replay and separate scoped Library rate coverage. It must not initiate a payer transaction or treat synthetic policy fixtures as NMCT authorization. A real eCW visit is reserved for a separately approved production canary after the software and policy gates pass; raw eCW Eligibility Insights 271 access is investigated independently and is not presumed available.

## Known remaining engineering decisions

- Real payer wording must be mapped into the new source-backed interaction fields before combined patterns can be used. Ambiguous interactions remain unresolved.
- A genuinely complete October replay needs the original protected evidence and pinned historical configuration. Current mutable tables cannot reconstruct every old rule version.
- NMCT must approve exact-scope policies, including benchmark advances, caps, payment verification and disclosure. The code does not invent those permissions.
- No raw eCW Eligibility Insights 271 retrieval entitlement has been established; use existing FHIR eligibility and Stedi evidence until one is confirmed.
