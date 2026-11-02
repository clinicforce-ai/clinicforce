# Service-level benefit applicability

## Selection precedence

For each selected billable group, require the same patient/policy and service date, then match provider tier, exact CPT, narrower service description, and only then an applicable general STC. Preserve exclusions, payer messages and selected row IDs. An STC 30 general plan coinsurance does not automatically add to an STC 98 specialist office copay. A separate diagnostic benefit can coexist with an office benefit if both services are billable and the payer terms say they apply independently.

| Pattern | Meaning | First-milestone status |
| --- | --- | --- |
| `COPAY_ONLY` | One fixed copay replaces deductible/coinsurance for the covered group. | Implemented for selected fixed copay with no additional coinsurance and deductible exempt or exhausted. Day/visit frequency is deduplicated. |
| `DEDUCTIBLE_PLUS_COINSURANCE` | Shared applicable deductible, then percent of remaining reference/exact price. | Implemented with one deductible balance per ID and group rounding. |
| `NO_PATIENT_COST_SHARE` | Verified covered zero or applicable exhausted OOP. | Implemented for supported zero; billing restrictions still control collection. |
| `DEDUCTIBLE_THEN_COPAY` | Deductible first, then one copay on the remaining covered price. | Implemented only with a selected `interaction_evidence_id`, `copay_after_deductible=true`, amount and day/visit frequency. |
| `COPAY_PLUS_COINSURANCE` | Both components explicitly required. | Implemented only with selected interaction evidence and an explicit `FULL_PRICE` or `AFTER_COPAY` coinsurance base. |
| `COPAY_PLUS_DEDUCTIBLE_COINSURANCE` | Copay and deductible/coinsurance apply to distinct covered portions. | Implemented only with selected interaction evidence and an explicit copay component price; shared deductible is consumed once. |
| `UNKNOWN_INTERACTION` | Available rows do not establish one formula. | No arbitrary total; return a research/staff action. |

Office E/M, EMG/NCS day, skin punch biopsy and other procedure groups are separate. An office visit integral to a procedure cannot be added solely from an AI proposal. A copay with one procedure-day frequency applies once to multiple included diagnostic CPTs. Two independent groups may both contribute. A matching encounter copay ID with visit frequency is counted once across groups.

For covered in-scope components, a verified applicable individual OOP remaining caps the combined share once. A source-resolved applicable family OOP exhaustion can cap the visit at zero; other family balances are never added to individual remaining. An unknown individual OOP balance withholds collection unless an exact approved policy explicitly permits collection with that uncertainty. A staff note claiming “OOP met” is a verification lead, not an accumulator. Mixed covered and excluded services require separate handling before a full visit total is authorized.

The synthetic mixed 271 regression selects $80 specialist office copay over $40 PCP/general office copay, uses office 0% rather than unrelated general 30% coinsurance, records individual deductible remaining $0, and leaves OOP remaining unknown despite a reported annual maximum and staff note.
