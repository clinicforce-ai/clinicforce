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

Uploaded PDF extractions are kept as a FHIR `collection` Bundle for the encrypted
Smart Health Link. Before persistence, ClinicForce converts that collection to a
FHIR `transaction` Bundle, which is the type accepted by HAPI's `/fhir` endpoint.
When the extraction references the entered patient ID, the transaction also creates
that Patient and uses a transaction-local UUID reference, so HAPI can resolve it
atomically.
