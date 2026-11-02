# Source-to-field resolution matrix

Each value used for a visit decision must be traceable to the saved case, a selected payer row or a scoped clinic record. Source IDs are protected references; never copy raw patient or payer rows into this document.

| Field | Candidate sources in order | Reject/conflict behavior | Independent work that continues |
| --- | --- | --- | --- |
| Patient, appointment, service date | eCW Appointment and patient binding; saved case context | Wrong beneficiary/date blocks insured collection | CPT template and generic reference lookup |
| Subscriber/member and primary policy | eCW Coverage and linked Patient/RelatedPerson; saved Stedi identity and discovery | Different member or unresolved COB blocks patient benefits | Clinic price and PA research |
| Payer product and route | Exact returned plan/product and matched eCW label; approved route aliases | Generic insurer name cannot prove product/network scope | Reference price lookup |
| Provider network | Product/provider/location/date scoped clinic participation; authoritative payer provider result | A benefit-tier indicator alone is insufficient; conflict retained | Planning scenarios and PA research |
| Selected CPT/modifier/units | Clinician-confirmed visit review, then documented eCW service; Bedrock proposal only for planning | Procedure suggested from diagnosis alone is not confirmed billable work | Benefits and reference price lookup |
| Service copay and frequency | Patient-bound exact CPT row, narrower selected service row, then applicable general row | Conflicting copay/frequency yields `UNKNOWN_INTERACTION`; duplicate rows are not summed | Other service groups |
| Deductible remaining | Applicable patient-bound individual accumulator; family rule only if embedded/aggregate relation established | Annual amount is not remaining; no implicit zero | Copay-only group modeling |
| Coinsurance | Applicable selected service and tier; source text for interaction | General STC 30 cannot replace a narrower rule without evidence | Other groups |
| OOP remaining | Applicable covered service/tier accumulator | Annual maximum and staff note do not establish remaining | Uncapped planning estimate, if otherwise supported |
| Exact price | Active target-payer contracted rate or authorized payer estimate | Missing exact fee does not block reference planning | BCBSTX fallback |
| BCBSTX reference | Active approved BCBSTX version and scoped line | Missing CPT/modifier/POS/provider match creates acquisition task; no guessed rate | Copay-only modeling |
| Prior visit payments | Staff ledger verification with reference in visit review | Without verified zero/payment amount, positive collection needs staff verification | Planning estimate |
| Collection permission | Versioned approved NMCT rule for product/tier/groups/date | No matching approval means null collection and explicit approval action | Numeric planning estimate |

The feature-flagged path now saves `canonical_evidence`: selected financial facts with value, source system and protected reference, patient/product/network/service/date scope, verification state, normalization rule and rule version. `canonical_evidence.build()` records identity, coverage, network, selected benefit rows, exact or reference price, policy and referenced payment verification. Field-level resolutions retain conflicts and attempted sources. It projects existing eCW/Stedi and clinic decisions; it does not replace the underlying identity or benefit selectors. Raw payer rows and patient names are excluded from the new fact projection. A full immutable cross-source history still requires separately versioned source snapshots.
