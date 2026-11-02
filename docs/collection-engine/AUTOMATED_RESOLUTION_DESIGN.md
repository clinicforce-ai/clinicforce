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

`insurance_research_agent.py` uses Bedrock Converse model `us.anthropic.claude-sonnet-5` when selected saved terms contain controlled review flags. It offers eleven typed, read-only tools across saved eCW context, selected benefits, scoped Library reference price, service template, policy scope, saved per-CPT requirements, approved clinic requirement references, official payer rule references, verified delegated administrator metadata, official contact and prior verified rule metadata. The model may make at most three tool calls in one round; each of at most two Converse requests has a 3-second connection timeout, 15-second read timeout and zero retries. Output is bounded to 900 tokens before tool results and 1,200 after. The finding has no calculation or policy authority. Transport, budget, tool validation and JSON failures become `research_incomplete`.

## Phase 7: procedure readiness and official contact fallback

The existing `contract_requirements.evaluate` remains the deterministic per-CPT referral and authorization selector. It compares current member-bound payer signals, scoped clinic documents, stored verified rules and published product rules; it separately records whether actual approval or referral clearance has been verified. The Phase 7 addition attaches an official provider contact and patient-free copyable call script only to selected CPTs still needing confirmation. Cigna, Aetna, UHC and BCBSTX contact routes have dated official source links; a mixed or unidentified payer gets no inferred number. Delegated administrators and exact product rules must still be confirmed on the call. These contacts never flip a requirement status or authorize collection.

Sonnet 5 may now read saved per-CPT requirement decisions, cited clinic and official payer source references, verified product-and-date-scoped delegated administrator metadata, the official contact starting point and prior verified rule metadata when a controlled referral or authorization review flag is present. It may return a cited next action; the calculator and approval state ignore its recommendation. Only selected benefit IDs or vetted official/clinic source references may be cited. Saved Stedi check links and patient names are excluded from the model prompt. The three-tool, timeout and failure limits above still apply.
The authorization and referral flags are derived from unresolved saved decisions for the selected CPT, even when a benefit row itself has no requirement note. A verified status clears that flag; a general contact never does.

The staging-only NMCT administrator can invoke `POST /api/staff/testing/collection-engine-research-check` with server-built invented facts. It returns advisory status and finding count without case or payer access. This verifies the deployed task's Bedrock path, while the deterministic requirement and collection decisions remain untouched.

## Phase 9: staging cached check and first proxy cutover

The staging-only NMCT administrator can call `GET /api/staff/testing/collection-engine-due?days=30` after seeding. It reads at most 100 marked fake visits due within 30 days, recomputes their current saved decision in memory, fingerprints service/evidence/policy/rule inputs, and returns visit actions plus deduplicated clinic-level missing-rate tasks. It makes no external call or database write. It is a bounded acceptance harness; an automated schedule and live eCW linkage are not active in staging.

When V2 is on, the previsit finish no longer constructs the Cigna/UHC Blue Cross fee-proxy suggestion; the generic scoped reference matcher and visit policy decide the amount. The legacy proxy path remains under the flag-off compatibility route until original-evidence replay covers each supported case. No real payer positive collection is inferred from a fake fixture.

Staging has no live eCW backend configuration and Stedi live eligibility is disabled. The integration acceptance path therefore uses the exact-staging-host, NMCT-admin-only `POST /api/staff/testing/collection-engine-scenarios` route to create 38 marked fake patients with invented appointment, Coverage and benefit evidence. Thirty new cases have successive saves, including pending eligibility, planning-only, policy approval, copay scope correction, payment, protection, cash price recovery and rate-scope regression. The original eight case IDs remain stable. Their saved reads recalculate from fixture evidence through the shared money and policy engine and render in React with a synthetic banner; they deliberately bypass live payer and Library refresh, so this exercise tests the downstream integration only. Pair it with the protected read-only October replay and separate scoped Library rate coverage. It must not initiate a payer transaction or treat synthetic policy fixtures as NMCT authorization. A real eCW visit is reserved for a separately approved production canary after the software and policy gates pass; raw eCW Eligibility Insights 271 access is investigated independently and is not presumed available.

## Known remaining engineering decisions

- Real payer wording must be mapped into the new source-backed interaction fields before combined patterns can be used. Ambiguous interactions remain unresolved.
- A genuinely complete October replay needs the original protected evidence and pinned historical configuration. Current mutable tables cannot reconstruct every old rule version.
- NMCT must approve exact-scope policies, including benchmark advances, caps, payment verification and disclosure. The code does not invent those permissions.
- No raw eCW Eligibility Insights 271 retrieval entitlement has been established; use existing FHIR eligibility and Stedi evidence until one is confirmed.
