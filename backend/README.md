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
```

`DATABASE_URL` is required and must target the PostgreSQL/RDS database named
`clinicforce-db`. The application creates its metadata tables
on startup: patients, source-document metadata, SHL manifests, and doctor-report
metadata. The PDF bytes are stored in S3 under patient-specific prefixes; RDS stores
the exact S3 key, so a report and its source PDFs stay associated with one patient.

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
