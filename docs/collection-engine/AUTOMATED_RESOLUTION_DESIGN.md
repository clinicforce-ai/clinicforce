# Automated resolution and deterministic visit decision

## First-milestone contract

`visit_collection_model.build(result, review)` reads the existing previsit result and returns `estimated_patient_responsibility_cents`, `collect_now_cents`, decision type, pricing basis, service-group trace and remaining actions. It does not write data or call a payer. `collection_guidance.build` attaches that result under `visit_model` when the feature flag is enabled. When the new model yields a complete numeric planning estimate, the legacy `amount_cents` is a backend alias of its authorized collection amount. Saved-case reload recomputes against current approved policy and scoped rates, retaining the source evidence in the case snapshot.

## Resolution and calculation order

1. Bind current appointment, patient, subscriber, medical policy and service date. Identity conflicts, multiple primary policies or stale evidence stay separate; independent CPT and price research can continue.
2. Use already saved eCW and Stedi evidence. Prefer a specifically applicable exact CPT/service benefit over a general STC row. Preserve row IDs and network/date applicability. A staff note cannot verify a payer accumulator.
3. Resolve selected CPTs into specialist office, EMG/NCS day, skin biopsy or other billable groups. The existing clinician edit path changes CPT, unit and modifier selections and triggers backend recomputation. Do not add EMG from diagnosis alone.
4. Resolve a dated exact target-payer rate first. Otherwise match an active scoped BCBSTX rate by CPT, modifier, provider/location, POS and date. Cross-payer basis is `BCBSTX_CROSS_PAYER_REFERENCE`, never the target payer allowance. Missing or conflicting rate rows remain unpriced.
5. Select one supported interaction per group: currently `COPAY_ONLY`, `DEDUCTIBLE_PLUS_COINSURANCE`, `NO_PATIENT_COST_SHARE`, or `UNKNOWN_INTERACTION`. Combined copay patterns require more source-specific formula validation before activation. Unknown interaction produces no fabricated total.
6. Count a group/day copay once, deduplicate the same encounter copay ID across groups, share a deductible ID once across eligible groups, apply `ROUND_HALF_UP` at group boundary, and cap covered groups by one verified applicable OOP remaining balance. Do not infer a remaining balance from annual maximum.
7. Apply an approved visit policy only when exact product, tier, service groups, date, benchmark permission, prior-payment evidence and billing restrictions all match. A missing positive policy yields a numeric planning amount and null collection; an approved defer gives an explicitly labeled $0.

For CPT 95908 ×1 at $105.11 and CPT 95886 ×2 at $89.18 each, reference total is $283.47. With $100 remaining deductible and 20% post-deductible coinsurance, share is `$100 + round_half_up(0.20 × $183.47) = $136.69`. An independent $40 office copay produces $176.69 modeled for the combined visit.

## Bounded Sonnet 5 advisory

`insurance_research_agent.py` uses Bedrock Converse model `us.anthropic.claude-sonnet-5` when selected saved terms contain controlled review flags. A single request has a 3-second connection timeout, 15-second read timeout, zero retries and 500 output tokens. It returns only findings tied to supplied selected benefit IDs; it has no calculation or policy authority. Transport/model/JSON failures become `research_incomplete`. The larger tool-using payer research and official phone resolver remain after the milestone gate.

## Known remaining engineering decisions

- General combined copay/deductible patterns need validated payer-specific formulas. The model currently refuses ambiguous interactions.
- A genuinely complete October replay needs the original protected evidence and pinned historical configuration. Current mutable tables cannot reconstruct every old rule version.
- NMCT must approve exact-scope policies, including benchmark advances, caps, payment verification and disclosure. The code does not invent those permissions.
- No raw eCW Eligibility Insights 271 retrieval entitlement has been established; use existing FHIR eligibility and Stedi evidence until one is confirmed.
