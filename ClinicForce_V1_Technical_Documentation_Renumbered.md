# ClinicForce — Version 1.0 Technical Documentation

**Document status:** Version 1.0  
**Audience:** Senior Software Engineers, Junior Software Engineers, DevOps/Cloud Engineers, QA Engineers, and maintainers  
**Production application:** `https://app.clinicforce.ai`  
**AWS Region:** `us-east-1`  
**Production stack:** `clinicforce-production`

---

# 1. Purpose and Scope

This document is the Version 1 technical reference for ClinicForce. It describes the production architecture, application components, data flow, AWS infrastructure, security boundaries, document-ingestion pipeline, Smart Health Link (SHL)/QR workflow, doctor reporting workflow, testing status, and deployment process.

The goal is to provide enough operational and architectural context for both senior and junior developers to understand:

- what each component does;
- how components communicate;
- where data is stored;
- how patient documents move through the system;
- how QR-based sharing works;
- how doctors access source documents;
- how the application is deployed;
- what production limitations currently exist;
- what remains to be completed for fully self-service CI/CD.

### 1.1 Repository structure

ClinicForce is maintained across two repositories:

| Repository | Responsibility |
|---|---|
| `clinicforce` | Application source: React frontend, FastAPI backend, HAPI FHIR integration, Dockerfiles, tests, and build configuration |
| `clinicforce-aws` | AWS infrastructure: CloudFormation template, stack configuration/registry, and deployment workflow definitions |

The production application is hosted at:

`https://app.clinicforce.ai`

> **Scope note:** Scribe is unrelated to ClinicForce and is intentionally excluded from this architecture and deployment documentation.

---

# 2. System Architecture

## 2.1 High-level architecture

```text
                         ┌─────────────────────────────┐
                         │       Patient / Doctor      │
                         │          Browser            │
                         └──────────────┬──────────────┘
                                        │ HTTPS
                                        ▼
                         ┌─────────────────────────────┐
                         │     CloudFront + HTTPS       │
                         │       Custom Domain          │
                         └──────────────┬──────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
             Static frontend                         /api/* and
                    │                                /manifest/*
                    ▼                                       │
          ┌──────────────────┐                               ▼
          │ Private S3 Static│                    ┌─────────────────────┐
          │     Bucket      │                    │ Application Load     │
          └──────────────────┘                    │ Balancer (ALB)      │
                                                   └──────────┬──────────┘
                                                              │ :8000
                                                              ▼
                                                   ┌─────────────────────┐
                                                   │ Backend ECS/Fargate  │
                                                   │      FastAPI         │
                                                   └───────┬─────┬───────┘
                                                           │     │
                          ┌────────────────────────────────┘     └─────────────────┐
                          │                                                        │
                          ▼                                                        ▼
               ┌─────────────────────┐                                  ┌──────────────────┐
               │ HAPI FHIR ECS       │                                  │ Amazon Bedrock   │
               │ Internal :8080      │                                  │ Haiku model      │
               └──────────┬──────────┘                                  └──────────────────┘
                          │
                          ▼
                 ┌─────────────────────┐
                 │ Amazon RDS           │
                 │ PostgreSQL :5432     │
                 └─────────────────────┘

                         Backend also accesses:
                 ┌─────────────────────────────┐
                 │ Private S3 Document Bucket │
                 │ Original patient PDFs       │
                 └─────────────────────────────┘
```

## 2.2 Request routing

The production request path is:

1. A patient or doctor opens `app.clinicforce.ai`.
2. Route 53 resolves the application domain to CloudFront.
3. CloudFront serves static React assets from the private frontend S3 bucket.
4. Requests under `/api/*` and `/manifest/*` are routed through CloudFront to the ALB.
5. The ALB forwards traffic to the backend ECS/Fargate service.
6. The FastAPI backend communicates with:
   - HAPI FHIR for FHIR resources;
   - PostgreSQL/RDS for application metadata and persisted structured data;
   - private S3 for source PDFs;
   - Amazon Bedrock for clinical document extraction.

---

# 3. AWS Infrastructure

## 3.1 CloudFormation

The production environment is represented by the CloudFormation stack:

`clinicforce-production`

Region:

`us-east-1`

The infrastructure repository contains:

```text
clinicforce-aws/
├── stacks/
│   └── clinicforce-production.yaml
└── stacks.config.yaml
```

The CloudFormation template manages the major production resources, including:

- VPC and networking;
- security groups;
- ECS services;
- Fargate task definitions;
- RDS PostgreSQL;
- S3 buckets;
- KMS encryption;
- IAM roles and policies;
- CloudFront;
- Route 53;
- Bedrock permissions.

The stack was successfully deployed and reported:

```text
UPDATE_COMPLETE
```

## 3.2 Networking

The stack creates a VPC with two Availability Zone subnets.

Network access is intentionally restricted:

| Component | Allowed access |
|---|---|
| CloudFront | Internet-facing entry point |
| ALB | Accepts traffic from CloudFront's AWS-managed origin prefix list |
| Backend ECS task | Accepts traffic from the ALB on port `8000` |
| HAPI FHIR | Accepts traffic only from the backend on port `8080` |
| RDS PostgreSQL | Accepts traffic from backend/HAPI security groups on port `5432` |
| RDS public access | Disabled |

### Public subnet consideration

The current ECS tasks run in public subnets and receive public IP addresses. This avoids the cost of a NAT Gateway, which is important for the current budget.

A public IP does **not** mean the application ports are publicly reachable. Security groups restrict inbound traffic so that the backend remains reachable through the intended CloudFront → ALB → ECS path.

This is a cost-conscious design for the current environment, but it should be reviewed before scaling the platform to a larger production footprint.

---

# 4. CloudFront, Route 53, and Frontend Hosting

## 4.1 Frontend storage

The React application is built into static assets and uploaded to:

```text
clinicforce-production-staticbucket-efydnokb3qxa
```

The bucket is private.

CloudFront uses Origin Access Control (OAC), so users cannot directly browse or retrieve frontend objects from S3.

## 4.2 CloudFront

CloudFront distribution:

```text
E2YMS87KI006II
```

Configured behaviors:

| Path | Origin |
|---|---|
| Default | Private frontend S3 bucket |
| `/api/*` | Application Load Balancer/backend |
| `/manifest/*` | Backend, including required query strings |

## 4.3 Route 53

The DNS record for:

```text
app.clinicforce.ai
```

points to the CloudFront distribution.

---

# 5. ECS and Fargate

The production ECS cluster is:

```text
clinicforce-production
```

It currently contains two services.

| Service | Purpose | External reachability |
|---|---|---|
| `backend` | FastAPI API, document ingestion, QR/SHL processing, report generation | CloudFront → ALB → ECS |
| `hapi` | Internal FHIR server | Backend only |

## 5.1 Backend

Current task definition:

```text
clinicforce-production-backend:7
```

Current image:

```text
clinicforce-backend:cited-source-links-20260809
```

Current state:

```text
Desired tasks: 1
Running tasks: 1
Status: Healthy
```

Sizing:

```text
0.25 vCPU
512 MB memory
```

## 5.2 HAPI FHIR

Sizing:

```text
1 vCPU
2 GB memory
```

HAPI is internal and is not exposed to the public internet.

### Availability limitation

Both services currently run as one-task services to control cost.

This means the environment is **not high availability**. A task failure or problematic deployment can temporarily interrupt the service.

For higher availability, the next production stage should consider:

- multiple backend tasks;
- multiple HAPI tasks where appropriate;
- service scaling policies;
- stronger deployment safeguards;
- private subnets with NAT or an alternative controlled egress architecture.

---

# 6. Database Architecture

## 6.1 RDS PostgreSQL

Database:

```text
clinicforce-production-database-lzyxsxpysai8
```

Configuration:

| Setting | Value |
|---|---|
| Engine | PostgreSQL |
| Instance | `db.t4g.micro` |
| Allocated storage | 20 GB |
| Encryption | Enabled using ClinicForce KMS key |
| Public access | Disabled |
| Backup retention | 7 days |
| Deletion protection | Enabled |
| PostgreSQL logs | Exported to CloudWatch |
| Master password | AWS Secrets Manager |

## 6.2 What RDS stores

RDS stores structured application and FHIR-related data, including:

- patient workspace IDs;
- password hashes;
- document metadata;
- S3 object keys;
- extracted FHIR bundles;
- structured clinical facts;
- manually entered patient records;
- encrypted SMART Health Link manifests;
- HAPI FHIR relational data.

### Important storage boundary

**Original uploaded PDF bytes are not stored in RDS.**

The source PDF is stored in private S3. RDS stores the metadata required to locate that PDF.

This creates a clear separation:

```text
Original PDF bytes
        │
        ▼
Private encrypted S3

Document metadata / structured facts
        │
        ▼
PostgreSQL / RDS
```

## 6.3 Legacy database migration safety

The backend contains a safety migration that refuses to delete the legacy `pdf_bytes` column if it still contains data.

Production verification confirmed that active uploaded-document records use S3 keys and that HAPI does not contain binary blob rows containing source PDFs.

---

# 7. S3 Document Storage

Source PDFs are stored in:

```text
clinicforce-production-documentsbucket-rhcw4otrqu46
```

Security controls:

- all public access is blocked;
- objects are encrypted with the ClinicForce KMS key;
- versioning is enabled;
- non-current versions expire after seven days;
- backend IAM permissions are restricted to ClinicForce document objects;
- browsers never receive S3 credentials;
- browsers never access the bucket through a public S3 URL.

## 7.1 Upload failure behavior

When document ingestion fails after the source PDF has been uploaded, the backend deletes the corresponding S3 object.

This prevents orphaned source documents from accumulating when persistence or extraction fails.

---

# 8. Encryption, KMS, and Secrets

## 8.1 KMS

The production stack uses the KMS key:

```text
alias/clinicforce-production
```

The key is used for:

- document S3 objects;
- RDS storage.

## 8.2 Secrets Manager

The backend receives secrets through AWS Secrets Manager.

| Secret | Purpose |
|---|---|
| `DATABASE_PASSWORD` | RDS connection password |
| `SOURCE_LINK_SECRET` | Signs expiring source-PDF download links |

`SOURCE_LINK_SECRET` was introduced for secure doctor source-document downloads.

It must never be exposed to:

- frontend JavaScript;
- generated PDFs;
- Git repositories;
- application logs.

---

# 9. Application Configuration

Production CloudFormation parameters include:

```text
BackendImageUri
HapiImageUri
BedrockModelId
AppDomainName
HostedZoneId
CloudFrontOriginPrefixListId
```

The backend environment includes:

```text
DB_HOST
DB_USER
DB_NAME
DATABASE_PASSWORD          # injected secret
HAPI_FHIR_URL=http://hapi.clinicforce.internal:8080
S3_BUCKET
S3_KMS_KEY_ID
BEDROCK_MODEL_ID
PUBLIC_BASE_URL=https://app.clinicforce.ai
CORS_ALLOW_ORIGINS=https://app.clinicforce.ai
SOURCE_LINK_SECRET         # injected secret
```

The internal DNS name:

```text
hapi.clinicforce.internal
```

is provided through ECS Service Discovery.

This allows the backend to communicate with HAPI without exposing HAPI publicly.

---

# 10. Clinical Document Ingestion

## 10.1 Overview

The document-ingestion pipeline converts an uploaded PDF into structured clinical information while preserving the relationship between each extracted fact and its original source document.

The high-level flow is:

```text
Patient PDF
   │
   ▼
PDF validation
   │
   ▼
Private S3 storage
   │
   ▼
Text extraction
   │
   ├── Normal PDF text
   │
   └── Scan/image fallback
   │
   ▼
Amazon Bedrock
   │
   ▼
Strict fact-only JSON
   │
   ▼
Backend validation
   │
   ▼
FHIR resources
   │
   ├── Persist structured data in RDS
   │
   └── Persist FHIR resources in HAPI
```

## 10.2 Extraction behavior

The backend:

1. Reads the uploaded PDF.
2. Attempts text extraction.
3. Uses scan/image fallback when necessary.
4. Sends the extracted content to Bedrock.
5. Requests strict fact-only JSON.
6. Validates the returned information.
7. Maps validated facts to FHIR resources.
8. Persists the structured result.
9. Preserves source-document metadata.

The model is not responsible for creating the final FHIR resources. The backend controls the final mapping and persistence.

## 10.3 Source citation model

Each extracted clinical fact can retain:

- original PDF filename;
- page number;
- persisted document ID.

This allows the UI and generated clinical report to associate a clinical statement with the exact source document that supports it.

---

# 11. FHIR Data Model and Persistence

Uploaded PDF extractions are retained as a FHIR `collection` Bundle for the encrypted Smart Health Link.

Before persistence, ClinicForce converts the collection into a FHIR `transaction` Bundle for HAPI's `/fhir` endpoint.

When the extraction references the entered patient ID, the transaction also creates the corresponding Patient resource and uses a transaction-local UUID reference so HAPI can resolve related resources atomically.

Conceptually:

```text
PDF
 │
 ▼
Extracted facts
 │
 ▼
FHIR collection Bundle
 │
 ▼
FHIR transaction Bundle
 │
 ▼
HAPI FHIR
```

This separation allows the system to use an appropriate representation for secure sharing while maintaining compatibility with HAPI's transactional persistence model.

---

# 12. Patient Workflow

## 12.1 Patient uploads a document

1. Patient opens `app.clinicforce.ai`.
2. CloudFront serves the React frontend from private S3.
3. Patient authenticates with workspace ID and password.
4. Patient uploads a PDF.
5. Browser sends the file to:

```text
/api/patient/upload
```

6. CloudFront forwards the request to the ALB.
7. ALB forwards the request to FastAPI.
8. Backend validates:
   - file type is PDF;
   - file is not empty;
   - file is no larger than 15 MB.
9. Backend writes the original PDF to encrypted private S3.
10. Backend extracts clinical facts using Bedrock.
11. Backend stores:
    - document metadata and S3 key in RDS;
    - structured FHIR bundle in RDS;
    - FHIR resources in HAPI.
12. Patient sees the saved document in the workspace.

**No PDF bytes are stored in RDS.**

---

# 13. QR Code and Smart Health Link Workflow

## 13.1 Patient generates a QR code

The QR workflow uses a Smart Health Link (SHL) containing an encrypted clinical bundle.

1. Patient enters an exactly four-digit sharing PIN.
2. Backend loads the patient's structured documents and manual records.
3. Older documents are extracted/reprocessed when required.
4. Backend combines records into a FHIR Bundle.
5. Backend generates a random AES-256 encryption key.
6. Backend encrypts the FHIR Bundle as a JWE.
7. Backend stores:
   - encrypted JWE;
   - salted PIN hash;
   - SHL manifest data
   in RDS.
8. Backend constructs an SHL URL containing:
   - manifest URL;
   - decryption key;
   - display label.
9. Backend renders the SHL URL as a QR code.
10. Patient shares the QR code and PIN with the doctor.

### Security rule

The plaintext PIN is never stored.

---

# 14. Doctor QR Scanning Workflow

1. Doctor opens the clinician scanner.
2. Frontend reads the QR code and extracts the SHL URL.
3. Doctor enters the PIN.
4. Frontend requests the encrypted manifest from:

```text
/manifest/{id}
```

5. Backend verifies the supplied PIN against the stored salted hash.
6. Backend returns the encrypted JWE when verification succeeds.
7. Doctor frontend sends the JWE and SHL decryption key to the summary endpoint.
8. Backend decrypts the FHIR Bundle.
9. Backend builds a deterministic clinical summary.
10. Each clinical fact retains:
    - source PDF filename;
    - source page number;
    - source document ID.
11. Backend generates a signed seven-day source-document download URL for each citation.
12. Frontend renders the doctor dashboard.

The doctor dashboard displays populated clinical categories only.

---

# 15. Doctor Clinical Summary

The doctor dashboard and generated clinical PDF can contain:

- clinical conditions;
- medications;
- allergies;
- care visits;
- measurements and laboratory results;
- charts when there are at least four valid numeric points;
- source citations adjacent to the facts they support.

The report intentionally avoids presenting empty clinical categories.

It also avoids:

- empty historical-value columns;
- detached global source registers;
- global source lists separated from the narrative.

Tables use adaptive layouts so longer clinical values and source links have sufficient space.

---

# 16. Secure Source-Document Downloads

## 16.1 Design

Source-document access uses a signed URL tied to a specific document.

```text
Clinical fact
     │
     ▼
Signed source link
valid for 7 days
     │
     ▼
GET /api/doctor/documents/{document_id}/download
     │
     ├── Verify HMAC signature
     ├── Verify expiration
     ├── Load document metadata from RDS
     ├── Retrieve exact object from private S3
     └── Return PDF as no-cache attachment
```

## 16.2 Security properties

A source link:

- identifies one exact document;
- expires after seven days;
- is protected by HMAC signing;
- cannot be altered without invalidating the signature;
- never exposes the S3 bucket publicly.

An invalid or modified link returns:

```text
HTTP 403
```

The browser never receives direct S3 credentials.

## 16.3 Dashboard citations

A citation such as:

```text
OMC Report Sample - Cardio2.pdf · p. 1
```

can be clicked from the doctor dashboard.

The backend then retrieves the exact document associated with that citation.

## 16.4 PDF citations

The same source-link mechanism is embedded into citations in the generated doctor summary PDF.

This means the doctor can use the same source-document workflow from either:

- the web dashboard; or
- the downloaded clinical summary PDF.

Production source-link generation was verified using a synthetic encrypted FHIR bundle without accessing patient data.

---

# 17. Doctor Summary PDF

The summary PDF is rendered in memory using WeasyPrint.

The workflow is:

```text
Doctor dashboard request
        │
        ▼
Backend builds summary
        │
        ▼
WeasyPrint renders PDF in memory
        │
        ▼
PDF returned to browser
        │
        ▼
Doctor downloads report
```

The generated report is not persisted as a reusable public file.

Citation links inside the PDF point to the signed, document-specific source-download endpoint.

> After the latest changes, patients should generate a fresh QR code before the doctor scans it.

---

# 18. Deployment Architecture

## 18.1 Current source state

Application changes are on GitHub `main`.

Latest referenced application commit:

```text
788ec2d
Link clinical citations to secure source downloads
```

Infrastructure work is on the PR-ready branch:

```text
infra/clinicforce-production-source
```

# 19. Local Backend Development

The backend uses AWS Bedrock for PDF extraction and doctor-summary functionality.

Before starting the backend locally, configure AWS credentials and a region with Bedrock model access.

Example:

```bash
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

Verify Bedrock access:

```bash
aws bedrock list-foundation-models --region "$AWS_REGION"
```

Start the backend:

```bash
uvicorn app.main:app --reload --port 8000
```

The backend uses the standard AWS credential-provider chain, including:

- environment variables;
- shared AWS profiles;
- IAM roles.

> **Credential hygiene:** Do not commit AWS access keys to Git. For production ECS workloads, use the ECS task role instead of long-lived access keys.

---

# 20. ECS Runtime Configuration

The production backend is designed to use its ECS task role for AWS services such as Bedrock and S3.

Do not place long-lived AWS access keys in the ECS task definition.

Representative configuration:

```bash
DATABASE_URL=postgresql://clinicforce_app:<password>@<rds-endpoint>:5432/clinicforce-db?sslmode=require
S3_BUCKET=<private-bucket>
S3_PREFIX=clinicforce
HAPI_FHIR_URL=http://hapi:8080
PUBLIC_APP_URL=https://app.example.com
CORS_ALLOW_ORIGINS=https://app.example.com
AWS_REGION=us-east-1
```

The RDS password should be supplied through Secrets Manager rather than plain-text task-definition configuration.

The application creates metadata tables for:

- patients;
- source-document metadata;
- SHL manifests;
- doctor-report metadata.

PDF bytes are stored in S3 under patient-specific prefixes. RDS stores the exact S3 key so reports and source PDFs remain associated with the correct patient.

---

# 21. ECS Service Discovery

For ECS environments, service discovery should be used instead of hard-coded task IP addresses.

Example backend configuration:

```bash
HAPI_FHIR_URL=http://hapi:8080
PUBLIC_APP_URL=https://app.example.com
CORS_ALLOW_ORIGINS=https://app.example.com
```

Example frontend upstream:

```bash
BACKEND_UPSTREAM=http://backend:8000
```

The frontend uses same-origin:

```text
/api
/manifest
```

requests.

The frontend's Nginx container proxies these requests to:

```text
BACKEND_UPSTREAM
```

This avoids embedding backend task IP addresses into frontend code or container images.

The stable public application URL is important because it is embedded into generated Smart Health Links.

---

# 22. Production Verification and Test Results

The following checks were completed successfully for the current release:

| Check | Result |
|---|---|
| Backend automated tests | 21 passed |
| Frontend ESLint | Passed |
| Frontend production build | Passed |
| CloudFormation cfn-lint | Passed |
| CloudFormation deployment | `UPDATE_COMPLETE` |
| Backend ECS revision 7 | Healthy |
| CloudFront invalidation | Completed |
| Production site | HTTP 200 |
| Signed source-link generation | Verified |

Production source-link verification used a synthetic encrypted FHIR bundle and did not require access to patient data.

---

# 23. Security Model

ClinicForce's security model relies on multiple independent boundaries.

## 23.1 Network boundaries

```text
Internet
   │
   ▼
CloudFront
   │
   ▼
ALB
   │
   ▼
Backend ECS
   │
   ├────► HAPI FHIR
   │
   └────► RDS
```

HAPI and RDS are not public services.

## 23.2 Storage boundaries

```text
Patient PDF
    │
    ▼
Private S3 + KMS

Structured clinical data
    │
    ▼
RDS + HAPI FHIR
```

## 23.3 Application-level protection

- patient PINs are salted and hashed;
- plaintext PINs are not persisted;
- SHL payloads are encrypted using AES-256/JWE;
- source-document links are HMAC signed;
- source-document links expire after seven days;
- source PDFs are never exposed through public S3 URLs;
- source-link secrets remain backend-only;
- RDS is not publicly accessible.

---

# 24. Operational Constraints and Known Limitations

Version 1 intentionally runs with a cost-conscious footprint.

### 24.1 Single ECS task

Backend and HAPI each run one task.

**Impact:** not highly available.

### 24.2 Public-subnet ECS deployment

ECS tasks receive public IPs to avoid NAT Gateway cost.

**Impact:** architecture is less isolated than a fully private-subnet deployment, although security groups restrict application access.

### 24.3 RDS instance size

The current database uses:

```text
db.t4g.micro
```

**Impact:** appropriate for low traffic but should be reviewed before significant scale-up.

### 24.4 Seven-day S3 non-current version retention

Older object versions are expired after seven days.

**Impact:** limits recovery from older S3 object versions beyond the retention window.

### 24.5 Seven-day source links

Doctor source-document links expire after seven days.

**Impact:** an old report citation may require a newly generated signed link rather than remaining permanently accessible.


---

# 25. Developer Quick Reference

## Production

```text
Application:
https://app.clinicforce.ai

AWS Region:
us-east-1

CloudFormation Stack:
clinicforce-production

ECS Cluster:
clinicforce-production

Backend:
FastAPI / ECS Fargate / port 8000

HAPI:
HAPI FHIR / ECS Fargate / port 8080

Database:
PostgreSQL / RDS / port 5432

Frontend:
React / private S3 / CloudFront

Source documents:
Private S3 + KMS

Clinical extraction:
Amazon Bedrock
```

## Important API paths

```text
/api/patient/upload

/manifest/{id}

/api/doctor/documents/{document_id}/download
```

## Important AWS resources

```text
CloudFront:
E2YMS87KI006II

KMS:
alias/clinicforce-production

Static S3:
clinicforce-production-staticbucket-efydnokb3qxa

Document S3:
clinicforce-production-documentsbucket-rhcw4otrqu46

RDS:
clinicforce-production-database-lzyxsxpysai8
```

---

# 26. Troubleshooting Guide

## 26.1 Browser shows `Failed to fetch`

Check the request path first:

```text
Browser
  ↓
CloudFront
  ↓
ALB
  ↓
Backend ECS
```

Verify:

1. CloudFront distribution is active.
2. `/api/*` is mapped to the ALB.
3. ALB security group permits CloudFront origin traffic.
4. Backend ECS security group permits ALB traffic on port `8000`.
5. Backend task is healthy.
6. CORS is configured for:

```text
https://app.clinicforce.ai
```

## 26.2 QR generation returns HTTP 500

Check backend logs and PostgreSQL errors.

The previously identified cause was concurrent schema-changing DDL.

Normal requests must not perform schema-altering operations.

Verify that schema initialization is performed only during backend initialization and that the advisory-lock mechanism remains intact.

## 26.3 Source citation returns HTTP 403

Check:

1. URL signature;
2. expiration timestamp;
3. document ID;
4. backend `SOURCE_LINK_SECRET`;
5. RDS document metadata;
6. S3 object key;
7. backend IAM permission to access the document bucket.

## 26.4 PDF extraction fails

Check:

1. file is actually a PDF;
2. file is not empty;
3. file is ≤ 15 MB;
4. PDF text extraction succeeds;
5. scan/image fallback is functioning;
6. AWS credentials or ECS task role has Bedrock access;
7. configured Bedrock model is available in `us-east-1`;
8. backend logs for extraction and persistence failures.

## 26.5 HAPI cannot be reached

Check:

1. ECS Service Discovery registration;
2. `HAPI_FHIR_URL`;
3. backend security-group rules;
4. HAPI task health;
5. port `8080`.

The backend should use the internal service name rather than a task IP.

---

# 27. Deployment Checklist

Before production deployment:

### Application

- [ ] Backend tests pass.
- [ ] Frontend lint passes.
- [ ] Frontend production build passes.
- [ ] Docker images build successfully.
- [ ] Required environment variables are configured.
- [ ] No secrets are committed.

### Infrastructure

- [ ] CloudFormation template validates.
- [ ] Stack configuration points to the intended production stack.
- [ ] ECS task roles contain only required AWS permissions.
- [ ] RDS remains private.
- [ ] S3 public access remains blocked.
- [ ] KMS encryption remains enabled.
- [ ] CloudFront routes `/api/*` and `/manifest/*` correctly.

### Application behavior

- [ ] Patient login works.
- [ ] PDF upload works.
- [ ] PDF appears in patient workspace.
- [ ] Clinical extraction succeeds.
- [ ] QR generation succeeds.
- [ ] Doctor scanner reads the QR.
- [ ] PIN validation succeeds.
- [ ] Doctor summary renders.
- [ ] Source citations appear beside facts.
- [ ] Source PDF download works.
- [ ] Invalid/expired source links return `403`.
- [ ] Clinical summary PDF generates successfully.

### Post-deployment

- [ ] ECS task is healthy.
- [ ] CloudFront invalidation completes.
- [ ] Production site returns HTTP 200.
- [ ] CloudWatch logs contain no unexpected errors.
- [ ] Temporary deployment resources are removed if no longer required.

---

# 28. Version 1 Change Summary

Version 1 establishes the following production capabilities:

1. CloudFormation is the intended source of truth for ClinicForce production infrastructure.
2. Frontend hosting uses private S3 behind CloudFront.
3. API traffic follows CloudFront → ALB → FastAPI ECS.
4. HAPI FHIR remains internal.
5. RDS remains private and does not store source PDF bytes.
6. Source PDFs are encrypted and stored in private S3.
7. Clinical PDF ingestion uses Bedrock-based extraction.
8. Extracted clinical facts are represented as FHIR resources.
9. QR sharing uses encrypted Smart Health Links.
10. Patient PINs are salted/hashed rather than stored in plaintext.
11. Clinical facts retain source filename, page, and document identifiers.
12. Doctor citations can retrieve the exact source PDF through signed seven-day links.
13. Doctor summaries and source citations are available in the generated PDF.
14. The previous QR PostgreSQL deadlock was removed from normal request processing.
15. Automated backend/frontend/infrastructure validation is in place.
16. Production deployment is healthy.

---

# 29. Architecture Decisions

## ADR-001 — Store source PDFs in S3, not PostgreSQL

**Decision:** Store original PDF bytes in private encrypted S3 and retain only metadata/object keys in RDS.

**Reason:** Separates binary document storage from relational application metadata, reduces database growth, and provides appropriate object-storage controls.

## ADR-002 — Keep HAPI FHIR private

**Decision:** HAPI is reachable only from the backend.

**Reason:** Doctors and patients do not need direct access to HAPI. The backend provides the application-level API and security boundary.

## ADR-003 — Use signed source-document links

**Decision:** Source documents are accessed through backend-generated HMAC-signed, expiring URLs.

**Reason:** A citation should grant access only to the exact document it references without making the S3 bucket public.

## ADR-004 — Avoid schema changes during normal requests

**Decision:** Schema initialization runs during backend initialization rather than per request.

**Reason:** Concurrent DDL caused PostgreSQL deadlocks and QR-generation failures.

## ADR-005 — Use cost-conscious single-task ECS deployment for Version 1

**Decision:** Run one backend task and one HAPI task.

**Reason:** Keeps the current environment affordable while traffic is low.

**Trade-off:** The environment is not highly available.

---



# 30. Version 1 Operational Summary

The current ClinicForce production architecture can be summarized as:

```text
                        PATIENT / DOCTOR
                              │
                              ▼
                    CloudFront / HTTPS
                              │
                ┌─────────────┴─────────────┐
                │                           │
          React static files          API / Manifest
                │                           │
                ▼                           ▼
          Private S3                 ALB → FastAPI
                                            │
                    ┌───────────────────────┼───────────────────────┐
                    │                       │                       │
                    ▼                       ▼                       ▼
                 HAPI FHIR               RDS                  Private S3
                 internal               metadata               PDFs
                    │                       │                       │
                    └───────────────────────┴───────────────────────┘
                                            │
                                            ▼
                                      Bedrock extraction
```

The key architectural principle is that **the backend is the application security and orchestration boundary**. Patients and doctors interact with ClinicForce through the application, while HAPI, RDS, and source-document storage remain protected internal services.