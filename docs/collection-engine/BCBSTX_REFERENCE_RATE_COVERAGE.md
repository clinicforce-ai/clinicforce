# BCBSTX reference coverage and acquisition

## Baseline evidence

The November 1 October audit reported **136 active BCBSTX lines across 22 CPTs and nine active versions** in then-current production configuration. It reported two active Aetna lines under a draft route and no complete Cigna/UHC commercial contracted-rate chain. These are aggregate, dated observations, not a current per-visit coverage measurement. [Source audit](../../../contextforcodex/commercial-estimate-october-deep-dive-2026-11-01.md#clinical-library-contracts-and-prices).

| Coverage question | Measured result | Remaining work |
| --- | --- | --- |
| BCBSTX active CPT inventory | 22 distinct CPTs in the dated audit | Requery through an approved read-only SQL path for current CPT/modifier/POS/provider/date detail. |
| Complete common NMCT templates | Not established from aggregate counts | Join each approved template's CPTs and quantities to the scoped resolver; report complete/partial/missing by visit date. |
| 69 October commercial cases | Not established from retained aggregate reports | Replay deidentified selected CPTs against the rate versions effective at each save; mark irrecoverable historical versions. |
| Exact other-payer commercial rates | Aetna two active lines under draft route; no complete Cigna/UHC chain in dated audit | Count separately from BCBSTX benchmark coverage. |

The current environment has AWS identity but the maintained guide says production RDS is private with no provisioned operator tunnel or read-only SQL account. No production SQL was run during this milestone. The required October numerator therefore remains **unmeasured**, not zero; no claim that 69 cases were benchmark-priceable is made.

## Resolver and clinic-level acquisition rule

`commercial_previsit_rate_reference.py` matches the enabled BCBSTX connection, verified network/profile, rendering and billing NPI, location, POS, service date, code and modifier using active rate versions. It returns a separate per-line reference with source/version and `complete` status. Other commercial payers can use it without an approved target-payer rate connection. Equal-rank contradictory active rates are rejected by `matching_rates`. A missing line must create a reusable clinic-level acquisition item keyed by clinic, CPT, modifier, POS, provider scope and date; no unrelated CPT or cash price is substituted. The automated acquisition queue has not yet been built.
