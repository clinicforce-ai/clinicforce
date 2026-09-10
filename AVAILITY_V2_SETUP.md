# Availity Beta V2 — setup and remaining actions

Deployed 9 September 2026 to **https://app.clinicforce.ai**. Application release `36dee40` and infrastructure release `182bce1` completed successfully. The live browser check passed: synthetic patient QR → passcode → **Verify insurance → Use Availity beta V2**, with no JavaScript errors. Backend tests: 118 passed, plus 9 subtests; frontend lint/build and CloudFormation validation passed.

**Current hosted status: V2 is deployed in Demo mode, but its Availity credentials are not connected yet.** The authenticated live configuration confirms `credentials_configured=false`. AWS denied `secretsmanager:CreateSecret` for the available local AWS identity; an authorized administrator must create the secret described in section 6 and provide its ARN. This is the outstanding AWS connection step. The working local `.env` remains where it was; it is not copied into the deployed image.

The successful Availity coverage check below used the local Demo credentials directly. A hosted Availity transaction cannot be verified until the AWS secret is connected. PCE, Service Reviews and Configurations still require resolution of the observed HTTP 403 responses. **Real-patient Availity access and pricing are not activated.**

Release records: [application deployment](https://github.com/clinicforce-ai/clinicforce/actions/runs/34429339708), [infrastructure deployment](https://github.com/clinicforce-ai/clinicforce-aws/actions/runs/34429080511). The first infrastructure attempt rolled back because the existing workflow tried to recreate an already-existing ID.me secret. The corrected workflow preserves existing integration parameters unless explicitly overridden. Application releases also wait for an active infrastructure update to finish.

## What V2 now does

Staff do not type patient or billing details into forms. In production they upload existing clinic documents, review the extracted request, and click the appropriate API action. Patient-specific insurance answers come from Availity. V2 does not use the old PDF estimate calculator.

The workflow includes payer requirements, eligibility and benefit details, Professional Patient Cost Estimator 2.0, outpatient authorization requests, referral requests, saved transaction IDs, asynchronous result retrieval, and a response history. An uncertain submission is not automatically resubmitted. A rejected access request can be explicitly retried after access is fixed.

**The current scope is a professional outpatient encounter.** Hospital facility/institutional estimates, inpatient admissions, secondary-payer coordination, attachment submission and delegated-payer portals are not implemented. They must not be presented as supported by this beta. A production request can also stop when a payer requires fields this importer does not yet map. Its error identifies the missing fields; send a redacted sample export and the payer's configuration to the developer so that exact mapping can be added.

The existing clinician QR share and insurance-beta session protect these endpoints. Cases are bound to that patient/share, the Availity account/environment, and the practice configuration. They become inaccessible after 24 hours and are removed by the existing case-expiry cleanup on case access/creation. This temporary case store is not a permanent authorization record; staff should record relevant references in the clinic system.

## 1. What we actually tested with your credentials

Your keys were found in `clinicforce/.env`, alongside `backend`, `frontend` and `hapi`. Their names are `AVAILITY_API_KEY` and `AVAILITY_SECRET_KEY`. The backend now supports these names and loads this file automatically. It also supports the standard names `AVAILITY_CLIENT_ID` and `AVAILITY_CLIENT_SECRET`. Existing process/ECS environment values take precedence over `.env`.

No secret values are included in this document or returned to the browser.

| Operation tested against Availity | Result with your current Demo application |
|---|---|
| Synthetic coverage inquiry | HTTP 200; complete; Availity mock-response header confirmed |
| Payer directory | HTTP 200; the actual response uses a `payers` envelope, now handled |
| Configurations | HTTP 403 |
| Professional PCE 2.0 submission and retrieval | HTTP 403 |
| Service Reviews submission and retrieval | HTTP 403 |

The alternate documented gateway path was also checked for PCE and Service Reviews; it returned 403 too. We have not established why your subscription denies these endpoints. Support must confirm the product/version entitlement and gateway for this application.

The other workflow stages have been tested with synthetic HTTP responses, including a simulated production document-to-estimate flow. These tests validate our code; they do not establish production payer connectivity or contracting.

To repeat a credential check locally, from the inner `clinicforce` repository:

```bash
backend/venv/bin/pip install -r backend/requirements.txt
PYTHONPATH=backend backend/venv/bin/python -m app.insurance.check_availity
```

In Demo this command submits only published synthetic inquiries. It prints status codes, not tokens or raw patient payloads. Exit code 1 means at least one required check failed; 403 is an access/setup result, not a Python crash. In production the command never sends a patient inquiry; add `--payer-id YOUR_AVAILITY_PAYER_ID` to test metadata access.

## 2. Send this follow-up to Availity

You have already contacted them about Healthcare HIPAA Transactions. Send this yourself; no message has been sent on your behalf:

> ClinicForce has a Developer Portal application subscribed to Healthcare HIPAA Transactions Demo, and we have requested Healthcare HIPAA Transactions production access. Our synthetic Coverages call succeeds, and the payer directory succeeds. Configurations, Professional Patient Cost Estimator 2.0, and Service Reviews return HTTP 403 with our current application credentials.
>
> Please confirm and enable the application entitlements for Coverages, Availity Payer List, Configurations, Professional Patient Cost Estimator **2.0** (`/v2/patient-cost-estimates/prof`), and Service Reviews **2.0** (`/v2/service-reviews`) for outpatient authorizations and referrals. If any requires a separate product, contract, scope or activation process, please identify it and provide its documentation.
>
> Please provide the correct gateway URL, OAuth token URL/scopes, testing/certification steps, production activation process, and our submitter/customer identifiers. Please confirm whether Demo access to PCE 2.0 and Service Reviews can be enabled before production activation.
>
> Our first clinic is Neurology and Movement Center of Texas. Please confirm the exact Availity payer IDs and API connectivity for the BCBS Texas products we intend to demonstrate, which billing/rendering NPIs and Tax IDs need enrollment, how enrollment completion is confirmed, whether Professional PCE supports those plans and providers, any service-date restrictions, and transaction pricing/limits. We need professional patient-cost estimates for the clinic's intended services, including office visits and clinician-ordered EMG/NCS.

Do not send patient PDFs or client secrets with an ordinary sales inquiry. Provide the app identifier and nonsecret request/transaction references if support requests them through its support process.

The official [HIPAA Transactions reference](https://developer.availity.com/blog/2025/3/25/hipaa-transactions) describes the endpoints, payer configurations and mock scenarios. The [production product page](https://developer.availity.com/portal/catalogue-products/healthcare-hipaa-transactions-1) is the product you have requested. Subscription and a successful eligibility response do not by themselves prove PCE or authorization support for a particular patient/plan.

## 3. Which files staff should upload

The examples are actually under the outer workspace's `contextforcodex/` directory. The inner app repository does not currently have a separate `contextforcodex` directory.

| Existing example | Use in V2 |
|---|---|
| `Availity Essentials.pdf` | **Insurance card** upload: subscriber name, Member ID and group only. Card benefit amounts are discarded. |
| `Medical Group - NEUROLOGY AND MOVEMENT CENTER OF - TX ALL AGMNT NEXT AVL EFF DAT_Original_Profession (1) (1).pdf` | **Clinic / billing provider** upload: identifies the group NPI and Tax ID. Its network and compensation clauses are not used to price V2. The notice address is not assumed to be the billing address. |
| `shmso.com Mail - (no subject).pdf` | Optional **Visit correspondence** upload: contextual reason only. The parser discards the email body, login information, old deductible/OOP assertions and collection advice. Obtain a redacted copy; rotate the embedded portal password if it is still active. |
| `Availity Essentials - Cov.pdf` | **Do not upload for V2.** Get coverage and benefits through Availity. |
| `fee-schedule-results.csv` / `fee schedule.png` | **Do not upload as billed charges.** These show negotiated PPO rates. V2 obtains payer allowed amounts through PCE. |
| `ncs-emg-cash-pricing-guide.pdf` | **Not used for insured V2 estimates.** Cash packages do not establish the patient's ordered/billed services. |
| `NMCT_Payer_Plan_Matrix (1).xlsx` | Not used to assert a live patient's network, coverage or price. |

**There is still a missing clinic source.** The email says new patient/neuropathy/EMG, but does not provide a patient-specific, dated, coded order with units and billed line charges. Neither the agreement nor the payer fee schedule supplies that encounter. An insurance API cannot decide which procedure the clinician will perform.

Ask the clinic for its existing registration/encounter/order export and billed-charge export. This is a request for existing system data, not a request for staff to fill a new patient form. A complete encounter export may also supply the approved billing-provider NPI, making a separate provider-document upload unnecessary.

### Supported import format

Searchable PDFs support explicit `Label: value` fields. CSV supports the following headers (snake_case equivalents are also accepted). Card images use local Tesseract OCR. Other scanned documents need a searchable PDF or CSV export.

| Information | CSV/PDF label |
|---|---|
| Patient identity | `Patient First Name`, `Patient Last Name`, `Patient DOB` |
| Insurance routing and subscriber | `Member ID`, `Availity Payer ID`, `Subscriber Relationship Code`, optional `Group Number` |
| A dependent's subscriber | `Subscriber First Name`, `Subscriber Last Name`, `Subscriber DOB` |
| Patient contact | `Patient Phone` — 10 digits |
| Encounter | `Service Date` — YYYY-MM-DD; `Place of Service Code`; optional `Reason for Visit` |
| Each ordered service | `CPT` or `HCPCS` or `Procedure Code`; `Units`; `ICD-10`; optional `Modifiers` |
| Billing | `Billed Charge` or `Line Charge`: total charge for that service line, not a unit rate and not an allowed amount |
| Provider | `Billing Provider NPI` |
| Service Reviews | `Rendering Provider NPI`, `Service Type Code` |

Use one patient encounter per CSV and one row per service. Repeat the encounter identifiers on its service rows. Each row's ICD-10 is associated with that service. Do not put alternative possible procedures into one submitted encounter. Modifiers are comma/semicolon/space separated; quote CSV cells containing commas. Subscriber relationship values supported by the importer are `18` self, `01` spouse, `19` child and `G8` other. Dates must be ISO dates. This is ClinicForce's current importer contract, **not a claim that eCW's default export already has these columns**. Share a redacted actual export if it needs a mapping.

For a searchable PDF, a supported service row looks like this synthetic format example:

```text
CPT: 99204 | Units: 1 | Billed Charge: 300.00 | ICD-10: G62.9
```

These example values are not coding recommendations or prices for the example patient. No codes, units, diagnoses or service dates are inferred from symptoms or email timestamps.

If billed charges are in a separate CSV, use `Procedure Code`, optional `Modifiers`, and `Billed Charge`. It must have an unambiguous matching row for each order line missing a charge. V2 does not multiply an unidentified fee-schedule rate by units.

## 4. One-time clinic setup — administrator, not patient forms

Before production, obtain the approved billing provider(s), rendering provider(s), billing address(es), Tax IDs, Availity submitter ID, payer IDs and enrollment confirmations from the clinic and Availity. The group NPI in the agreement differs from the requesting-provider NPI in the old coverage screenshot; establish the correct roles instead of copying one over the other.

Set `AVAILITY_PRACTICE_CONFIG` to JSON with this structure, replacing every placeholder. Keep `production_enabled` false until setup is complete:

```json
{
  "production_enabled": false,
  "timezone": "America/Chicago",
  "submitter_id": "<Availity-issued submitter ID>",
  "enrolled_payers": [],
  "payer_aliases": {},
  "providers": {
    "<approved 10-digit NPI>": {
      "lastName": "<billing organization or provider last name>",
      "ein": "<9-digit Tax ID>",
      "addressLine1": "<verified billing street address>",
      "city": "<billing city>",
      "stateCode": "TX",
      "zipCode": "<billing ZIP>",
      "specialtyCode": "<provider taxonomy if required>",
      "contactName": "<clinic contact if required>",
      "phone": "<clinic phone if required>"
    }
  }
}
```

Add the approved rendering-provider entry too if its NPI differs. Optional fields that do not apply should be omitted, not left as placeholders. Only add payer IDs to `enrolled_payers` after the required enrollment has been confirmed. `payer_aliases` can map an exact, distinctive payer label on the card to its verified Availity payer ID. Do not map a generic “BCBS” label to a state/product without confirmation. An explicit `Availity Payer ID` in the registration export avoids that ambiguity.

For local development this JSON may be a single-quoted `.env` value. For AWS use the separate practice secret described below. Staff select none of these settings in the patient UI; the server matches the uploaded NPI to this approved configuration.

## 5. Run locally

1. Keep `.env` where it is. Do not move it into `frontend` and do not rename secrets with a `VITE_` prefix.
2. Install backend requirements with the command above.
3. Install OCR if using the local Python backend: `sudo apt-get install tesseract-ocr`. You installed it during this work and the example card now passes OCR. The existing backend Dockerfile already installs it.
4. Keep `AVAILITY_ENVIRONMENT=demo` and `AVAILITY_SCOPE=healthcare-hipaa-transactions-demo` while using Demo credentials. Enable `INSURANCE_BETA_ENABLED=true`. Existing database and share-session configuration is still required, as for the upload beta.
5. Start the backend from the repository: `PYTHONPATH=backend backend/venv/bin/uvicorn app.main:app --reload --port 8000`.
6. In a second terminal, start the frontend: `cd frontend` then `npm run dev`.
7. Open a patient share in the clinician view, click **Verify insurance**, then **Use Availity beta V2**. The beta remains protected by the existing clinician-share session.
8. In Demo, start a demo case and run coverage. There are no real-patient uploads in Demo. Try the other stages after Availity enables their Demo entitlements; any 403 is displayed as unavailable.

## 6. Deploy Demo to AWS, then switch to production

The current deployment template is `clinicforce-aws/stacks/clinicforce-production.yaml`. Updating the older `ecs-backend.yaml` alone does not configure this stack.

1. Using an authorized AWS console account, open **Secrets Manager** and select **US East (N. Virginia), us-east-1**. Choose **Store a new secret → Other type of secret**. Add `client_id` with the value of `AVAILITY_API_KEY` from your local `.env`, and `client_secret` with the value of `AVAILITY_SECRET_KEY`. Use the AWS-managed encryption key (`aws/secretsmanager`). Choose **Next**, name it `clinicforce-production/availity-demo`, leave automatic rotation disabled, and store it. Open the resulting secret and copy its **ARN**. Send only the ARN to the developer; never the values. If the console denies creation too, ask your AWS administrator to create this secret. No application IAM escalation or plaintext ECS credential workaround is needed.
2. Deploy the updated infrastructure template/workflow with `availity_environment=demo`, `availity_credentials_secret_arn=<that ARN>`, and `insurance_beta_enabled=true`. Leave the gateway at its default unless Availity confirms another URL. This adds ECS secret injection and the execution-role permission to retrieve that secret.
3. Release the app backend and frontend changes together using the app deployment workflow. New backend dependencies require rebuilding its image. The release workflow now preserves **all** existing stack parameters rather than a fixed list, so unrelated integration settings and new Availity settings survive app releases.
4. Verify the hosted Demo through the real clinician QR flow. Local `.env` values are not automatically copied into ECS.
5. After contracting/certification/enrollment, create a **separate production credentials secret**. In another secret, use the plaintext editor to store the complete practice JSON object from step 4. Do not wrap that object in an extra `practice_config` key. Use the AWS-managed encryption key for both secrets, as required by the supplied execution-role setup.
6. Once the clinic configuration is verified, set its `production_enabled` to true. Deploy infrastructure with `availity_environment=production`, the production credentials ARN, and `availity_practice_secret_arn=<practice-secret ARN>`. The template selects the production OAuth scope automatically. No frontend rebuild is needed just to change environments.
7. ECS reads secret values at task startup. If you rotate a secret without changing its ARN, force a new backend deployment or release a new backend image so tasks receive the new values.
8. Start a **new V2 case** after switching account/environment/practice settings. Old demo cases cannot be used as live coverage or live price evidence.
9. Upload the correct patient's source documents, review extracted identifiers/services, fetch payer requirements, run coverage, then request the estimate. Only submit authorization/referral when staff intend to make those requests.
10. For the first authorized production patient, reconcile the returned identifiers, benefit dimensions, service codes, provider network, prices and authorization details with the clinic and payer. Automated synthetic tests cannot replace this payer-specific acceptance check.

Use professional PCE only for a supported date. This implementation currently requires the service date to be today in the configured clinic timezone and does not silently change a future appointment into a current-day estimate. Ask Availity about any future-date workflow you require.

## 7. What to request from eClinicalWorks

Ask for the **clinic/practice integration** needed to read existing registration, subscriber/insurance, appointment/encounter, coded orders, billing/rendering providers, service locations, units/modifiers, diagnoses and billed-charge data. Ask them to identify the actual APIs/feeds, scopes, practice activation, sample payloads and fees that cover each category. In particular, confirm access to future appointments/orders and practice-management billing data; do not assume patient record access includes them.

Suggested message:

> ClinicForce needs a provider-authorized integration for this practice to read patient/subscriber insurance registration, scheduled encounters and clinician-coded orders, CPT/HCPCS and ICD-10 associations, modifiers and units, service dates/locations, billing/rendering provider NPIs and Tax IDs, and the clinic's billed charges. We use those inputs for Availity eligibility, Professional PCE and Service Reviews. Please identify the supported APIs or exports for each item, required scopes and activation, and provide redacted sample payloads and test access. Is a separate practice-management/billing interface needed beyond patient-facing SMART/FHIR?

The existing `ECW_INTEGRATION.md` discusses sharing/importing clinical records, not an implemented source of all these insurance transaction inputs. eCW's [official disclosure](https://www.eclinicalworks.com/wp-content/uploads/2021/03/eCW-V11-Mandatory-Disclosures-March-2021.pdf) describes practice-side FHIR activation; confirm the current workflow and the additional data access directly with eCW. Once its actual API contract and access are available, an adapter can replace the document importer while keeping the Availity request/result workflow.

## 8. Understanding stopped or incomplete results

| What staff see | Next action |
|---|---|
| Missing source information | Obtain the named registration/order/provider/charge export. The app does not ask staff to invent missing values. |
| Patient/member/provider mismatch | Correct the clinic source or patient profile and start a matching case. Do not use another patient's Availity result. |
| HTTP 403 | Confirm the application's endpoint/version entitlement, scope and payer enrollment with Availity. Use the explicit retry only after the problem is resolved. |
| Pending | Check the saved result ID; the UI polls briefly and then allows manual checks. This is not a new submission. |
| Submitting/indeterminate after interruption | Do not create a duplicate inquiry. Resolve its existence with Availity using the saved case/request/transaction references. |
| Estimate unavailable | The payer did not return a usable matching estimate. No PDF rate, local benefit formula or zero-dollar fallback is substituted. |
| Partial authorization, modified, contact-payer, or additional documentation needed | Follow the returned payer instructions. Do not interpret a PCE result as permission to schedule. The current beta does not submit attachments or automate delegated portals. |

For institutional/facility pricing, ask Availity which Institutional PCE product is offered for your payer and application. That needs a separate institutional request mapping and validation before the UI can support it. Do not treat a professional clinic estimate as a hospital facility estimate.
