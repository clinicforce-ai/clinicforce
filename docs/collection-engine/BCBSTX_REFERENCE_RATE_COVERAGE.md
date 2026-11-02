# BCBSTX reference-rate coverage

## Phase 0 measurement, November 2, 2026

Read-only production SQL through the existing clinic reader found one enabled BCBSTX payer connection, one verified provider network and one enabled estimation profile. Its **nine currently active rate versions contain 136 lines for 22 distinct CPTs**. All active lines are unmodified, `per_unit`, POS 11, one rendering-provider scope, one clinic location, one TIN, Blue Choice PPO network and `CI` plan matcher. The base 19 CPTs have identical values in seven active versions; two biopsy CPTs and one chemodenervation CPT occur in later versions. These are the clinic's BCBSTX reference values, not another payer's allowed amounts. The private [dated audit](../../../contextforcodex/collection-engine-phase0-audit-2026-11-02.md) records the exact fee values, source classes, activation times and aggregate query method without patient rows.

| NMCT service family or template | CPTs in active BCBSTX rows | Missing CPTs in that family | Coverage for the configured POS/provider/location |
| --- | --- | --- | --- |
| New-patient office E/M | 99202–99205 | None | All four alternatives, one selected level per visit |
| Established office E/M | 99212–99215 | None | All four alternatives, one selected level per visit; 99211 is also in the schedule |
| NCS plus needle EMG | 95907–95913, 95885–95887 | None | All ten catalogue alternatives; actual selected units still matter |
| Standalone EMG | None | 95860–95864, 95867–95870 | No benchmark row |
| Skin punch biopsy | 11104, 11105 | None | Both rows; this version activated October 23 |
| Lumbar puncture | None | 62270, 62328 | No benchmark row |
| DBS analysis/programming | None | 95970, 95983, 95984 | No benchmark row |
| Botox drug and injection | 64612 | J0585, 64615 | Partial; 64612 version activated October 29 |
| CTA planning | None | 70496, 70498 | No approved clinic rate row; a separate public CTA reference is unversioned and cannot become an approved clinic benchmark |

No active row has a modifier or a non-office POS. A telehealth POS 02/10 or a required modifier is therefore outside this approved reference schedule. A rate for one provider/location cannot be generalized to another clinic or provider.

## October case comparison

The existing 57-patient audit totals can be reproduced as 69 cases and 350 saved snapshots by including one last payer label containing “Medicare.” The original audit did not retain a cohort membership manifest, so identical membership cannot be proven from totals alone. A strict commercial-label filter yields **56 patients, 67 cases and 338 snapshots**. Both counts are shown so that the classification discrepancy does not disappear into a coverage percentage.

Using each case's **last October saved selected CPTs**, patient-bound date and a context-token-matching saved workflow gives two different coverage measurements:

| Timing rule | Published 57-patient / 69-case cohort: complete, partial, none, no selected CPT | Strict 56-patient / 67-case cohort: complete, partial, none, no selected CPT |
| --- | --- | --- |
| Apply current active Library versions to the October visit scope | **38, 3, 2, 26**; 52 of 59 selected lines priced | **36, 3, 2, 26**; 50 of 57 lines priced |
| Also require Library version activation before the historical save | **21, 3, 19, 26**; 35 of 59 lines priced | **21, 3, 17, 26**; 35 of 57 lines priced |

The first row measures the current schedule's potential coverage for those saved visit scopes. The second is a conservative historical availability check; 17 published-cohort cases acquired complete Library reference coverage only after their October save. Among the current-schedule gaps, five selected lines lack a CPT row (70496 and 70498 twice each; 95860 once); two selected office lines use POS 02/10 while the schedule only has POS 11. All selected lines have a provider NPI and units; no selected line has a modifier. The historical workflow context token matches all 69 last snapshots, allowing provider, billing, location and POS comparisons without publishing their identifiers.

Both rows use **currently active** rate/network/profile status, which is mutable. Even the activation-timed row cannot reconstruct deleted or superseded historical configuration or prove the patient had applicable benefits and collection approval. The 26 cases without selected CPTs have no complete billable visit price despite a potentially available office-code family. The [aggregate SQL](../../../contextforcodex/phase0-bcbstx-coverage-aggregate-2026-11-02.sql) and [dated audit](../../../contextforcodex/collection-engine-phase0-audit-2026-11-02.md) specify the denominator and limits.

## Contracted-rate coverage is a separate measure

The only other configured payer with active rows is Aetna: two diagnostic CPT lines under a **draft** connection, without a verified network or enabled estimation profile. The UHC Medicare Advantage route is draft with no active rate row. Cigna and commercial UHC have no complete configured exact-rate chain. This does not prevent the BCBSTX *planning* reference for a matching selected service; a reference-based positive collection still needs patient-specific benefits and an approved NMCT policy.

## Acquisition worklist

The visit model emits a `price_acquisition` entry for each selected CPT missing a matching approved reference, with modifier, POS and service date. The admin-only Library missing-rates endpoint groups those saved entries by clinic/CPT/modifier/POS across payers, without patient details, and displays a BCBSTX reference worklist alongside existing exact-payer and cash gaps. The immediate families are standalone EMG, lumbar puncture, DBS, Botox drug/64615, CTA, and telehealth/modified services. Obtain a source schedule with its price, units and provider/location scope, validate it through the Clinical Library, import a version, check conflicting active rows, then recheck affected visits. Do not substitute cash prices, another CPT, or an unversioned public reference. This inventory is a case-backed acquisition prompt; it does not import or approve a rate automatically.
