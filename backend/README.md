# ClinicForce backend

The PDF ingestion and doctor-summary routes use AWS Bedrock Claude 3 Haiku. Configure
standard AWS credentials and a region with Bedrock model access before starting the backend:

```bash
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

In a separate terminal, verify it before uploading a PDF:

```bash
aws bedrock list-foundation-models --region "$AWS_REGION"
```

Then start the backend from this directory:

```bash
uvicorn app.main:app --reload --port 8000
```

The backend targets `anthropic.claude-3-haiku-20240307-v1:0` and uses the standard AWS
credential-provider chain (environment variables, shared profiles, or IAM roles).

## ECS production configuration

The backend is designed to use its ECS **task role** for Bedrock and S3; do not put
long-lived AWS access keys in the task definition. Set these task environment
variables (store the RDS password in Secrets Manager, not plain text):

```bash
DATABASE_URL=postgresql://clinicforce_app:<password>@<rds-endpoint>:5432/clinicforce-db?sslmode=require
S3_BUCKET=<your-private-bucket>
S3_PREFIX=clinicforce
HAPI_FHIR_URL=http://hapi:8080
PUBLIC_APP_URL=https://app.example.com
CORS_ALLOW_ORIGINS=https://app.example.com
AWS_REGION=us-east-1
# Optional patient-owned Google Drive storage (client secret belongs in Secrets Manager)
GOOGLE_OAUTH_CLIENT_ID=<google-oauth-web-client-id>
GOOGLE_OAUTH_CLIENT_SECRET=<google-oauth-client-secret>
GOOGLE_TOKEN_KMS_KEY_ID=<kms-key-arn>
# Epic production SMART-on-FHIR (local development must use a dedicated random value)
EPIC_CLIENT_ID=<epic-production-client-id>
EPIC_OAUTH_STATE_SECRET=<random-64-character-secret>
```

`DATABASE_URL` is required and must target the PostgreSQL/RDS database named
`clinicforce-db`. The application creates its metadata tables
on startup: patients, source-document metadata, SHL manifests, and doctor-report
metadata. The PDF bytes are stored in S3 under patient-specific prefixes; RDS stores
the exact S3 key, so a report and its source PDFs stay associated with one patient.

## Optional patient-owned Google Drive

When the Google OAuth variables are configured, a patient can connect their
Google Drive and choose it for future uploads. ClinicForce creates a `ClinicForce`
folder, stores source PDFs and one replace-in-place `Clinical Summary.pdf` there,
and stores only Drive IDs plus a KMS-encrypted OAuth refresh token in RDS. The
configured OAuth redirect URI must be:

```text
https://<your-public-domain>/api/patient/google-drive/callback
```

The integration uses the least-privilege `drive.file` scope. It processes PDFs
uploaded through the ClinicForce portal; it does not silently ingest arbitrary
files a patient adds elsewhere in Drive.

## Epic / MyChart live connection

ClinicForce uses an Epic-registered, standalone SMART-on-FHIR R4 public client.
Register this exact production callback URI with Epic:

```text
https://app.clinicforce.ai/api/patient/epic/callback
```

Configure the app as patient-facing, read-only, USCDI v3 automatic-distribution
access. Request no `offline_access` or write scopes. A patient chooses an Epic
hospital from the weekly mirrored endpoint catalog and authorizes at that
hospital's MyChart page. Access tokens, PDFs, and retrieved FHIR resources stay
only in the live backend process and are cleared on logout, token expiry, or
restart. They must never be sent to HAPI, S3, Google Drive, browser storage, or
patient-record tables.

`python -m app.scripts.sync_epic_endpoints` mirrors Epic's public Brands Bundle
into the non-patient `epic_endpoints` catalog. The single production backend
starts an isolated weekly background refresh outside patient requests and derives a
domain-separated state-encryption key from the existing task-only source-link
secret. The sole persistence
exception is an explicit patient-created QR share: it stores an encrypted,
timestamped FHIR snapshot in `shl_manifests`, excludes Epic attachments, and can
be revoked from the patient workspace immediately.

## eClinicalWorks / healow live connection

ClinicForce starts a patient-facing standalone SMART-on-FHIR R4 launch from the
six-character practice OEM code. The current healow base URL is derived as:

```text
https://fhir4.healow.com/fhir/r4/<OEM-code>
```

It discovers the authorization and token endpoints at that practice's
`/.well-known/smart-configuration` URL; do not configure the retired
`ecwcms-*.azurewebsites.net` endpoint. Register the following exact values in
the healow developer portal:

```text
Redirect URI: https://app.clinicforce.ai/api/patient/ecw/callback
JWKS URL:     https://app.clinicforce.ai/api/patient/ecw/jwks
```

Set `ECW_CLIENT_ID` and `ECW_KMS_KEY_ID` in production (or `ECW_CLIENT_JWK` in
local development). The connector requests healow's standard `offline_access`
scope so a registration that requires a refresh token can authorize correctly,
but it keeps neither access nor refresh tokens beyond the short-lived,
patient-authorized live session.

For ECS, use Service Connect aliases rather than task IP addresses:

```bash
# backend task definition
HAPI_FHIR_URL=http://hapi:8080
PUBLIC_APP_URL=https://app.example.com
CORS_ALLOW_ORIGINS=https://app.example.com

# frontend task definition
BACKEND_UPSTREAM=http://backend:8000
```

The frontend calls same-origin `/api` and `/manifest` URLs; its Nginx container
proxies them to `BACKEND_UPSTREAM`. Therefore neither the image nor frontend browser
code contains a backend task IP. `PUBLIC_APP_URL` must be the stable public ALB DNS
name or custom domain, because it is embedded in generated Smart Health Links.

For production, place the frontend behind an HTTPS Application Load Balancer. Keep
HAPI and RDS private; the backend reaches HAPI over Service Connect/VPC security
groups. You can then deploy a new task-definition revision without changing any URLs.

Uploaded PDF extractions are kept as a FHIR `collection` Bundle for the encrypted
Smart Health Link. Before persistence, ClinicForce converts that collection to a
FHIR `transaction` Bundle, which is the type accepted by HAPI's `/fhir` endpoint.
When the extraction references the entered patient ID, the transaction also creates
that Patient and uses a transaction-local UUID reference, so HAPI can resolve it
atomically.
