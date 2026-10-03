# Production insurance estimator setup

Use this checklist only in the deployment secret manager. Do not put live
credentials, patient data, or payer responses in the repository.

## 1. Availity production subscription

Request a production application subscription for **Healthcare HIPAA
Transactions** and configure its issued client ID and secret:

```dotenv
AVAILITY_ENVIRONMENT=production
AVAILITY_CLIENT_ID=issued-by-availity
AVAILITY_CLIENT_SECRET=issued-by-availity
AVAILITY_SCOPE=healthcare-hipaa-transactions
AVAILITY_BASE_URL=https://api.availity.com/availity
AVAILITY_TOKEN_URL=https://api.availity.com/v1/token
```

For every payer to be launched, confirm with a real non-patient test or an
approved pilot that:

- the payer has an API 270 eligibility route;
- Professional Patient Cost Estimator v2 / predetermination is supported;
- any payer enrollment is complete; and
- the payer accepts the clinic's billing provider and submitter ID.

ClinicForce checks payer routes and validation requirements before it sends a
patient inquiry. An OAuth token alone does not make every payer PCE-capable.

## 2. eClinicalWorks backend application

The application needs a backend-service eCW registration with the read scopes
in `backend/app/insurance/ecw_backend.py`, plus a registered JWKS/signing key.

```dotenv
ECW_BACKEND_CLIENT_ID=issued-by-ecw
ECW_BACKEND_ISSUER=https://your-ecw-fhir-issuer.example/fhir
# Production: an AWS KMS RSA signing key. Local development can use ECW_CLIENT_JWK.
ECW_KMS_KEY_ID=arn:aws:kms:region:account:key/key-id
```

eCW must return the selected appointment's coverage, provider/location,
CPT/HCPCS, ICD-10, units, member/subscriber data, phone number, and billed
charges. A clinic fee CSV can supply a missing billed charge, but never payer
benefits or a negotiated allowed amount.

For the staff-facing flow, allow the clinic's internal practice ID through
`STAFF_INTEGRATION_PRACTICE_ID` (or the comma-separated
`STAFF_INTEGRATION_PRACTICE_IDS`). Configure the existing private S3 bucket
and KMS key (`S3_BUCKET`, optional `S3_KMS_KEY_ID`) when staff will upload fee
CSVs; the application stores those files outside the short-lived patient case.

## 3. Practice configuration

Set `AVAILITY_PRACTICE_CONFIG` to one JSON object in the deployment secret
manager. Replace every example value; do not enable production until each
target payer is verified.

```json
{
  "production_enabled": true,
  "timezone": "America/New_York",
  "submitter_id": "AVAILITY_ISSUED_SUBMITTER_ID",
  "payer_aliases": {"Payer name as returned by eCW": "AVAILITY_PAYER_ID"},
  "enrolled_payers": ["AVAILITY_PAYER_ID"],
  "providers": {
    "1234567893": {
      "lastName": "LEGAL BILLING PROVIDER NAME",
      "ein": "123456789",
      "addressLine1": "1 MAIN STREET",
      "city": "ANYTOWN",
      "stateCode": "NY",
      "zipCode": "10001",
      "stedi_organization_name": "LEGAL BILLING PROVIDER NAME"
    }
  },
  "locations": {
    "ECW_LOCATION_RESOURCE_ID": {
      "billing_provider_npi": "1234567893",
      "place_of_service": "11",
      "service_type": "1"
    }
  },
  "stedi_payer_ids": {"AVAILITY_PAYER_ID": "STEDI_PAYER_ID"}
}
```

Only list a payer in `enrolled_payers` after its enrollment is complete. Omit
that list entry when Availity reports that enrollment is not required.

## 4. Stedi fallback

```dotenv
STEDI_API_KEY=production-key-from-stedi
STEDI_FALLBACK_ENABLED=true
```

Stedi is used only after Availity cannot provide usable **eligibility** or the
payer has no Availity eligibility route. It returns eligibility and benefits;
it does not calculate a patient price, network contract status, referral, or
authorization. Insured estimates remain Availity PCE-only.

### Stedi-only assisted launch

If Availity is not available at launch, set the Availity environment and live
scope as below, leave its client ID and secret unset, and configure Stedi plus
the eCW/practice fields above. This keeps live patient data out of Availity's
demo environment while allowing the Stedi eligibility fallback.

```dotenv
AVAILITY_ENVIRONMENT=production
AVAILITY_SCOPE=healthcare-hipaa-transactions
AVAILITY_CLIENT_ID=
AVAILITY_CLIENT_SECRET=
STEDI_API_KEY=production-key-from-stedi
STEDI_FALLBACK_ENABLED=true
```

The staff workflow records portal/call confirmation references for network,
referral, and authorization. It permits only **insurance estimate pending** or
**clinic cash-price** paths; it never calculates an insured patient amount.
After Availity PCE has passed the payer pilot, set
`STEDI_FALLBACK_ENABLED=false` to make Availity the sole automated path.

## 5. Launch test

For one consented test/pilot appointment per payer:

1. Open the eCW appointment and check that no required input is missing.
2. Run payer requirements, then eligibility.
3. If required, submit and receive an approved referral/authorization.
4. Submit PCE and verify each returned service line matches the submitted CPT,
   modifiers, units, and billed charge.
5. Verify the displayed patient responsibility and allowed amount against the
   payer response; do not compare it to a local formula.
6. Test an unavailable-Availity eligibility route and verify Stedi returns a
   benefits-only manual-review result.

Do not automatically collect an out-of-network estimate. ClinicForce flags it
for review even when PCE returns an amount.
