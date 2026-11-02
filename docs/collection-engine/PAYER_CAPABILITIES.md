# Payer evidence capabilities

| Source | Confirmed current capability | Limits and handling |
| --- | --- | --- |
| eCW appointment and FHIR Coverage | Current patient, visit date, coverage and subscriber links | Bind beneficiary/subscriber to the same patient and visit; a payer label alone is not a product. |
| eCW FHIR CoverageEligibilityResponse | Existing read-only reader and capability probe | Returned benefits are usable only when exposed, populated and patient-bound. Eligibility Insights raw 271 retrieval is unconfirmed. The first-milestone implementation does not claim that entitlement. |
| Saved Stedi 271 | Existing STC benefit rows, coverage and accumulators | STC 30/98 are research candidates. CPT-specific benefits are uncommon in the base response; network flags alone do not prove clinic participation. |
| Separate saved Stedi CPT inquiry | May supply exact-CPT requirements and some cost sharing | The October audit found no structured CPT allowance in retained CPT responses. Do not make a new paid inquiry merely to price a visit. |
| NMCT BCBSTX active schedule | Dated, scoped reference CPT rates | A cross-payer planning benchmark only. It never establishes Cigna/Aetna/UHC contracted reimbursement. |
| Clinic contract/participation record | Product/provider/location participation where the recorded scope applies | A contract for an insurer is not proof every product of that insurer is in network. Preserve conflicting payer evidence for review. |
| Bedrock Claude Sonnet 5 | Cited advisory on selected saved benefit facts | Cannot invent benefits, authorize a collection, or supply a price. No patient identifiers enter the prompt. |

The per-payer STC/CPT usefulness registry and official payer contact resolution are later work. Until validated, the engine uses each selected patient's already available benefit rows and returns a targeted verification action when a rule cannot be established.
