# Insurance verification — Phase 1 research and local prototype

## Validated approach

1. **eClinicalWorks / healow:** use SMART on FHIR and inspect each practice's
   `CapabilityStatement` (`GET /metadata`) before requesting a resource. FHIR
   R4 defines `Appointment` for scheduling and `Coverage` for a patient's
   coverage record. A tenant may not grant either resource or appointment data.
   The existing ClinicForce connector already obtains patient-authorized
   `Coverage`; it does **not** currently request appointment data.
2. **Insurance card:** a card image is not a guaranteed eCW FHIR response.
   If an EHR exposes it, the interoperable container is a `DocumentReference`
   with an attachment. Current ClinicForce deliberately strips document bytes
   and upstream URLs before a browser review, which is the right default. A
   live card-image feature needs a separately authorized download path.
3. **Eligibility:** the HIPAA-standard request/response is ASC X12N
   270/271, version `005010X279A1`. Availity's Coverages API is a REST
   representation of that workflow, using OAuth 2 client credentials. A
   clearinghouse can replace Availity behind the same normalized adapter.
4. **Network:** a card identifies a payer, member, group, and frequently plan;
   it does not establish that a particular clinician is contracted. Determine
   network status by matching payer + plan + provider NPI + billing TIN +
   service location against the clinic's credentialing/contract roster, then
   use the eligibility response for available benefits. Payer directories and
   plan rules can still be incomplete.
5. **Financial responsibility:** inactive coverage or no applicable
   out-of-network benefit goes to **self-pay review**, not an automatic
   assertion that the patient owes a particular amount. A 271 is not a payment
   guarantee; adjudication and office policy determine the final amount.

## Local implementation

- Clinician flow: decrypt a shared record → **Verify insurance** (development
  build only) → collect an insurance card JPG/PNG/PDF → review one estimate
  step at a time.
- The endpoint reads only enough to enforce a 10 MB limit, then discards the
  file. It stores no card bytes, performs no OCR, and contacts no payer.
- The local case is based on the supplied local documents, is kept outside Git,
  and is not an exact copy of any vendor API response. It declares the FHIR R4
  `Appointment`/`Coverage` and X12 270/271 boundaries the live adapters must
  satisfy.
- The feature is hidden from production frontend builds. The endpoint is also
  disabled outside localhost unless `INSURANCE_DEMO_ENABLED=true` is explicitly
  supplied.

## AWS mapping

No infrastructure change was made for this local-only phase. The current
production architecture is ECS/Fargate (FastAPI), RDS PostgreSQL, HAPI FHIR,
private KMS-encrypted S3, KMS signing, Secrets Manager, ALB, and CloudFront.
For live cards/eligibility, add a dedicated integration boundary in the backend
task; keep credentials in Secrets Manager, card originals in encrypted private
S3 only when retention is approved, and never expose payer tokens or upstream
document URLs to the browser.

## Phase 2: coding, referrals, authorization, and estimates

### Corrections to the proposed workflow

- A reason for visit is a **chief complaint**, not a diagnosis and not enough
  to select a billable CPT/HCPCS code. For most E/M families, code level is
  selected from documented medical decision making or total practitioner time;
  the record must also support the diagnosis and medical necessity.
- CPT is copyrighted and needs an AMA license for electronic use. Do not embed,
  train on, or ask a general model to invent CPT content. A licensed code set
  may be retrieved at decision time only under the applicable AMA AI addendum.
- A referral and prior authorization are related but distinct. eCW can hold an
  order/referral as FHIR `ServiceRequest`; payer confirmation comes from a
  payer API or X12 278/FHIR prior-authorization workflow.
- Eligibility benefits are not enough to calculate an exact balance. The
  estimate needs the selected procedure/service, diagnosis, date of service,
  units, modifiers, place of service, rendering/billing provider NPI, billing
  TIN, service location, payer plan, network match, allowed/contracted amount,
  accumulator balances, and authorization/referral result. The claim is the
  final adjudication.

## Document-backed clinician workbench

The original fictional patient case has been replaced for local testing by
an **untracked, document-backed local case**. It is read only by the backend
from `INSURANCE_LOCAL_CASE_PATH`; the data is not put into this repository,
the browser bundle, repository test data, or logs. The clinician-facing screen is a
seven-step workbench, not a patient portal:

1. A clinician or front-desk user collects the patient-provided insurance-card
   image/PDF. In the local workbench this confirms the supplied evidence is
   loaded; it does not claim to perform OCR.
2. Eligibility shows the dated coverage evidence and flags a card-versus-portal
   discrepancy for reconciliation. Production will replace this with the
   clearinghouse/Availity eligibility transaction.
3. Network shows the dated provider/plan evidence. Production must recheck the
   rendering provider, billing TIN, service location, plan, and date of
   service; a provider agreement alone is not a patient network decision.
4. The clinician records the visit reason and anticipated service from the
   appointment, referral/order, or scheduled procedure. The final diagnosis,
   MDM/time, and billed code belong to the post-encounter coding workflow.
5. Referral and prior authorization are separate confirmations. The supplied
   portal evidence is historical evidence only, not approval for a new service.
6. The workflow asks for the current contracted rate. The supplied agreement
   references a fee schedule but contains no numeric allowed amount for a
   selected service.
7. Therefore calculation is deliberately blocked. It is safer to state
   “estimate unavailable” than to turn a deductible/copay screenshot into a
   patient price.

Each completed stage has a collapsed **Integration diagnostics** panel. It is
for clinic/API testers, not patients: it identifies the source, internal
endpoint, and operating mode for that particular result. When real adapters
are connected, this is where a failed 270/271, network check, authorization
lookup, or estimator call can be located without exposing partner payloads or
credentials to the main workflow.

### Local V2 document requirements

The V2 workbench asks for a document only at the workflow point where it is
needed. The patient is not the operator; clinic staff upload clinic-held
evidence when it is unavailable from an integration.

| Workflow step | Required only when | Acceptable document | Supplied-document classification |
| --- | --- | --- | --- |
| Insurance details | Always, to begin a local case | Member ID card PDF | `Availity Essentials.pdf` is a member-ID-card artifact. |
| Eligibility | Coverage must be established and no live eligibility adapter is enabled | Dated eligibility/benefits response for the visit date, with coverage status and member/payer details | `Availity Essentials - Cov.pdf` is eligibility evidence. It is not a card. |
| Network | Coverage is active | Clinic–payer participation agreement/roster that names the clinic/provider and payer | The supplied professional provider agreement is valid participation evidence. It is not a numeric fee schedule. |
| OON benefits | **Only** if Network is out of network | Payer benefits response that explicitly addresses out-of-network coverage | No supplied document fills this role. It is not requested for in-network cases. |
| Referral / prior authorization | **Only** if the selected service/plan requires one | Referral order and/or payer authorization determination | The supplied email thread is operational context, not a payer authorization determination. |
| Fee schedule | A pre-service dollar estimate is requested | Current numeric CPT/HCPCS rate sheet for the matched payer/plan/provider/location/effective date | The supplied provider agreement references compensation but does not provide a selected-service numeric rate. |

The local validator checks PDF text for the distinguishing evidence above and
rejects a clearly wrong document type at upload. It remains a triage check,
not proof that the document is current, complete, or applicable to the visit;
staff review remains mandatory. A missing optional document does not stop an
unrelated branch. A missing mandatory input does not produce an invented
estimate: the eventual live adapter must obtain it from the payer or the
clinic's maintained contract/rate data.

Run the V2 workflow locally with the clinic-owned payer-plan matrix configured
outside Git. The V2 workflow does **not** use `INSURANCE_LOCAL_CASE_PATH`; that
variable belongs only to the older demo endpoint.

```bash
cd /home/mustansir/clinicforce/clinicforce
export INSURANCE_PLAN_MATRIX_PATH='/home/mustansir/clinicforce_visa_documents/NMCT_Payer_Plan_Matrix (1).xlsx'

# terminal 1
cd backend && uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend && npm run dev
```

Open the clinician app in development and choose the insurance-review entry.
The workbook is not copied into source control. It provides plan-level
operational rules only: it determines whether the workflow requests a referral
and identifies services that require a current payer authorization lookup. It
does not supply a member's copay, deductible remaining, network result, or an
allowed amount; those remain payer/date/provider-specific evidence.

### Clinic payer-plan matrix in V2

`INSURANCE_PLAN_MATRIX_PATH` points to the clinic-maintained `.xlsx` workbook.
After the insurance card and eligibility response are reviewed, V2 matches the
eligibility response's payer and plan/product to exactly one matrix row. A
non-match, ambiguous match, or missing workbook blocks the workflow rather
than selecting a referral rule. The matcher recognizes common printed plan
family spellings such as `BCBSTX` and `Preferred Provider Option`/`PPO`, but
does not turn a plan family into a benefit determination.

For a matched row, V2 does the following:

1. Applies the matrix's referral rule. A `Yes` row requests a reviewed referral
   order; a `No` row does not. A `Varies` row requires a current payer response.
2. Displays the matrix's prior-authorization trigger note for staff review.
   It never treats that note as a final authorization decision.
3. Requires a dated payer requirements response for the actual planned service,
   then an approval document if that response says authorization is required.

This is deliberately not a diagnosis-to-CPT generator. A reason such as
“neuropathy” is not enough to decide all billable services: diagnosis and final
service selection occur after clinical assessment and need licensed coding
terminology and clinician/coder review. The matrix's listed CPT values are used
only as plan-policy hints for an already selected service, never as automatic
codes.

### Service-item selection and “if elected” estimates

The selectable unit is now a **service item**, not an isolated code. An office
visit is scheduled; procedures suggested from the pre-visit reason are marked
**if elected** until the clinician confirms they will be performed. ClinicForce
may estimate both scenarios, but the front-desk instruction includes scheduled
items only. It must never ask staff to collect a contingent procedure amount in
advance.

The first reviewed guardrails are deliberately narrow:

Botox and EMG/NCS are examples, not the supported-procedure boundary. Staff may
enter any exact CPT/HCPCS code. A code found in the uploaded fee schedule gets
its reviewed description and rate; a code not found there remains a separate,
unpriced service item and cannot produce a collection estimate. ClinicForce
does not merge unfamiliar procedures merely because it lacks a family rule.

- Choose only one code from 95907–95913 for a date of service. These are
  alternatives based on the total number of nerve-conduction studies, not one
  independently selectable code per limb or nerve. Needle EMG is not mandatory
  for every NCS, but when same-day EMG add-on codes 95885–95887 are selected,
  one 95907–95913 NCS code must also be present.
- As one example, a Botox treatment item contains the condition-appropriate administration code
  and HCPCS J0585 for onabotulinumtoxinA. J0585 is billed per unit, so a pre-visit
  suggestion cannot finalize units, wastage modifiers, or the administration
  code before the clinician documents the treatment.
- Referral status remains plan/member-specific and prior authorization remains
  code-specific. If the uploaded payer response did not query every selected
  CPT/HCPCS code, the operational result is **verify before scheduling**, not
  “not required.” Authorization is not a guarantee of benefits or payment.

These are validation rules, not a claim that ClinicForce contains a universal
medical-billing rules engine. Production needs a licensed, versioned CPT/HCPCS
catalog, current NCCI/payer edits, units/modifiers, diagnosis and place of
service, and a payer response for the exact member and planned service. A local
LLM may help map staff language to a reviewed service family, but it must not
invent code relationships or override the deterministic validation layer.

Primary references:

- [CMS billing article for NCS and EMG](https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=57478) — study-count selection and same-day EMG/NCS code families.
- [CMS billing article for botulinum toxin injections](https://www.cms.gov/medicare-coverage-database/view/article.aspx?articleid=59707) — administration plus drug-code reporting, J0585 units, and documentation.
- [AANEM position statement on same-day E/M and electrodiagnostic testing](https://www.aanem.org/docs/default-source/documents/practice/billing-for-same-day-evaluation.pdf) — when separately documented E/M and EDX services may occur together.
- [BCBSTX utilization management](https://www.bcbstx.com/provider/claims/claims-eligibility/um) — exact-member eligibility, network, and procedure-code prior-authorization checks through Availity.

### Live API and data checklist

| Need | Source / API | Minimum live input or output |
| --- | --- | --- |
| Appointment, coverage, referral/order | eCW/healow SMART on FHIR | `Appointment`, `Coverage`, `ServiceRequest`; confirm tenant capabilities/scopes |
| Coverage and accumulators | Availity Coverages or another clearinghouse | 270/271-equivalent eligibility/benefits, dates, deductible/OOP, copay, coinsurance, service-type benefits |
| Referral/prior authorization | Availity Service Reviews/IsAuthRequired or payer prior-auth API | X12 278 or FHIR prior-auth status, authorization ID, units, service dates, approval/denial and requirements |
| Pre-service patient estimate | Availity Care/Patient Cost Estimator Professional or payer estimator | Provider/payer-supported estimate for the selected service lines; payer/product support is required |
| Contracted allowed amount | Practice-management/billing fee schedule and payer contract/roster | Contracted rate keyed by payer/plan, procedure, modifier, POS, provider NPI/TIN, location, effective date |
| Code selection | Licensed CPT/HCPCS + ICD-10-CM terminology service; clinician/coder workflow | Final services, diagnoses, modifiers, units, date, POS; AMA license and AI terms for CPT |

Availity's detailed schemas and payer support are subscription-gated, so the
partner sandbox OpenAPI/companion guide—not a guessed JSON payload—must become
the contract test for each adapter.

### Production architecture after contracts are obtained

Keep the current browser → FastAPI ECS task boundary. Add a backend-only
`eligibility`, `authorization`, `estimate`, and `coding-review` workflow that
normalizes vendor responses and preserves the raw partner transaction in a
restricted audit store. The browser receives only the normalized status,
explanation, and approved estimate. Store API credentials and the CPT license
configuration in Secrets Manager; grant the ECS task only the required secret
read and Bedrock Converse permissions. Store reviewed estimates/audit metadata
in encrypted RDS, limit retention, and use encrypted private S3 only for
attachments required by an authorization. Do not deploy that infrastructure
until a BAA, CPT license, payer enrollment, and test-payer acceptance cases are
in place.

## Required before a live release

1. Obtain eCW/healow application approval and practice scopes; confirm the
   target tenant's `Appointment`, `Coverage`, and document capabilities.
2. Enroll with Availity or the chosen clearinghouse, subscribe to eligibility
   and member-card products, obtain sandbox credentials and payer test cases.
3. Implement vendor-specific request/response adapters from the contracted
   OpenAPI/companion guide and validate them with their sandbox. Do not treat
this local case as an exact Availity payload; partner documentation is
   required for that contract.
4. Choose a HIPAA-appropriate OCR provider under a BAA, define minimum fields,
   confidence thresholds, human correction, retention, access controls, and
   audit logging.
5. Load the clinic's actual contracts/credentialing roster. This cannot be
   reconstructed from a patient's insurance card. Add payer cost estimation or
   predetermination when an estimate, rather than eligibility, is required.
6. Only after coverage/network routing is settled, begin Phase 2: draft CPT
   suggestions from documented reason/encounter information for coder review;
   never produce a final code from a reason-for-visit alone.

## Primary sources

- [eClinicalWorks interoperability and healow developer access](https://www.eclinicalworks.com/products-services/interoperability/)
- [HL7 FHIR R4 Appointment](https://hl7.org/fhir/R4/appointment.html)
- [HL7 FHIR R4 DocumentReference](https://hl7.org/fhir/R4/documentreference.html)
- [CMS: HIPAA eligibility inquiry and response](https://www.cms.gov/priorities/key-initiatives/burden-reduction/administrative-simplification/transactions/health-plan-eligibility-benefit-inquiry-response)
- [Availity API guide: Coverages, Member ID Card, OAuth](https://developer.availity.com/blog/2025/3/25/availity-api-guide)
- [CMS: a 271 does not guarantee reimbursement](https://www.cms.gov/priorities/key-initiatives/burden-reduction/administrative-simplification/operating-rules/faqs)
- [CMS E/M documentation and level selection](https://www.cms.gov/training-education/medicare-learning-networkr-mln/compliance/medicare-provider-compliance-tips/evaluation-management-services)
- [AMA CPT licensing and AI requirements](https://www.ama-assn.org/practice-management/cpt/cpt-licensing-frequently-asked-questions-faqs)
- [CMS referral and authorization transaction](https://www.cms.gov/priorities/key-initiatives/burden-reduction/administrative-simplification/transactions/referral-certification-authorization)
- [CMS prior-authorization API requirements](https://www.cms.gov/newsroom/fact-sheets/cms-interoperability-prior-authorization-final-rule-cms-0057-f)
- [FHIR CoverageEligibilityRequest](https://hl7.org/fhir/R4B/coverageeligibilityrequest.html)
