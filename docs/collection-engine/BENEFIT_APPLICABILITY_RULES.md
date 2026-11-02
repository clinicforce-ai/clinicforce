# Service-level benefit applicability

## Selection precedence

For each selected billable group, require the same patient/policy and service date, then match provider tier, exact CPT, narrower service description, and only then an applicable general STC. Preserve exclusions, payer messages and selected row IDs. An STC 30 general plan coinsurance does not automatically add to an STC 98 specialist office copay. A separate diagnostic benefit can coexist with an office benefit if both services are billable and the payer terms say they apply independently.

| Pattern | Meaning | First-milestone status |
| --- | --- | --- |
| `COPAY_ONLY` | One fixed copay replaces deductible/coinsurance for the covered group. | Implemented for selected fixed copay with no additional coinsurance and deductible exempt or exhausted. Day/visit frequency is deduplicated. |
| `DEDUCTIBLE_PLUS_COINSURANCE` | Shared applicable deductible, then percent of remaining reference/exact price. | Implemented with one deductible balance per ID and group rounding. |
| `NO_PATIENT_COST_SHARE` | Verified covered zero or applicable exhausted OOP. | Implemented for supported zero; billing restrictions still control collection. |
| `DEDUCTIBLE_THEN_COPAY` | Source-specific deductible first, then copay on defined base. | Deferred until payer wording/formula is structured and tested. |
| `COPAY_PLUS_COINSURANCE` | Both components explicitly required. | Deferred until source establishes base and frequency. |
| `COPAY_PLUS_DEDUCTIBLE_COINSURANCE` | Components apply to distinct covered portions. | Deferred until components and allocation are identified. |
| `UNKNOWN_INTERACTION` | Available rows do not establish one formula. | No arbitrary total; return a research/staff action. |

Office E/M, EMG/NCS day, skin punch biopsy and other procedure groups are separate. An office visit integral to a procedure cannot be added solely from an AI proposal. A copay with one procedure-day frequency applies once to multiple included diagnostic CPTs. Two independent groups may both contribute. A matching encounter copay ID with visit frequency is counted once across groups.

For covered in-scope components, a verified applicable individual OOP remaining caps the combined share once. A family accumulator must not be added to individual remaining; applying one to a member needs the plan's embedded/aggregate relationship. A staff note claiming “OOP met” is a verification lead, not an accumulator. Mixed covered and excluded services require separate handling before a full visit total is authorized.

The synthetic mixed 271 regression selects $80 specialist office copay over $40 PCP/general office copay, uses office 0% rather than unrelated general 30% coinsurance, records individual deductible remaining $0, and leaves OOP remaining unknown despite a reported annual maximum and staff note.
