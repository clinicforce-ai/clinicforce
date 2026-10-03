# ClinicForce application instructions

## Live documentation

Every new feature and every change to existing behavior, API contracts, user flows, data, integrations, deployment, or operations must update the relevant section of the live engineering guide in the same work. The guide is `backend/app/api/developer_docs.html`, published at `/api/docs` and `/api/redoc`. Update FastAPI route/model descriptions and `backend/app/api/developer_docs.py` as needed for API contract changes so `/api/openapi.json` stays accurate. Explain the resulting behavior and relevant setup, failure states, data handling, or operational checks. Use synthetic examples and omit patient data and secrets. Verify the affected live section and API reference after deployment. A change log or local note alone does not satisfy this rule.
