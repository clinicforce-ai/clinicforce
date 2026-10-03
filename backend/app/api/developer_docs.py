"""Read-only developer guide and OpenAPI navigation for the deployed API."""

from pathlib import Path

from fastapi.responses import HTMLResponse


OPENAPI_DESCRIPTION = """ClinicForce serves patient records, clinic intake, and insurance workflows through a same-origin API. Start with the [guided engineering map](/api/docs#getting-started) or the [ReDoc reading view](/api/redoc#getting-started) before using an operation. The guide explains the request path, data ownership, clinical evidence, deployed infrastructure, and worked user flows. This reference is read-only: do not enter patient data, passwords, or integration secrets here."""

TAG_NAMES = {
    "staff": "Clinic workspace",
    "staff-registration": "Clinic setup and invitations",
    "patient insurance upload": "Insurance card upload",
    "patient-idme": "Patient identity",
    "doctor-insurance": "Insurance V1: documents",
    "doctor-insurance-v2": "Insurance V2: payer transactions",
    "doctor-commercial-estimator": "Commercial visit estimate",
}

TAG_GUIDES = [
    ("Patient account", "Register, sign in, inspect the current session, and edit the patient profile. A portal patient is a different identity from a clinic's `staff_patients` row."),
    ("Patient records and files", "Upload or remove patient documents, add manual records, choose S3 or encrypted Google Drive storage, and read the workspace. PostgreSQL holds metadata and selected FHIR JSON; object storage holds file bytes."),
    ("EHR connections", "Authorize Epic/MyChart or eClinicalWorks/healow, handle callbacks, inspect live records, and import selected data. The backend owns OAuth state and calls the EHR; the browser does not receive provider credentials."),
    ("Health links and clinician view", "Create a patient-controlled SMART Health Link, verify its PIN, and render the clinician summary. External links have a separate import path. A share is not a clinic insurance intake."),
    ("Clinic setup and invitations", "Create a clinic, set its public NPI, invite staff, and accept an invitation. Invitation tokens and administration are distinct from routine staff sessions."),
    ("Clinic workspace", "Staff sign-in, patient search, intake confirmation, document library, saved checks, appointment history, and guarded file reads. Clinic access is scoped by practice and intake."),
    ("Insurance card upload", "Create an expiring link, let a patient submit a card without an account, return a receipt, and hand the intake to staff for identity review."),
    ("Patient identity", "ID.me authorization, callback, status, and unlink. The deployed environment may be sandbox; a callback and an identity link are separate lifecycle steps."),
    ("Insurance V1: documents", "Document-based evidence and staged review. Its findings do not prove a payer transaction completed."),
    ("Insurance V2: payer transactions", "Create a scoped case, confirm services and requirements, submit a payer resource, then refresh a pending result or retry a confirmed access rejection. The coverage result comes before later resource checks."),
    ("Commercial visit estimate", "Read the eCW visit, resolve coverage, check eligibility, match clinic rates, calculate a previsit estimate, and read frozen history or later financial data. Eligibility and price have different sources."),
    ("Diagnostics", "Synthetic integration diagnostics used by tests. This is not a patient or clinic user journey."),
]


def _tag_for(path: str) -> str:
    if path.startswith("/api/patient/idme"):
        return "Patient identity"
    if path.startswith("/api/patient/epic") or path.startswith("/api/patient/ecw") or path.startswith("/api/patient/google-"):
        return "EHR connections"
    if path.startswith("/api/patient/documents") or path in {"/api/patient/upload", "/api/patient/records", "/api/patient/storage-preference"}:
        return "Patient records and files"
    if path.startswith("/api/patient/generate-link") or path.startswith("/api/patient/external-shl") or path.startswith("/api/doctor/decrypt-and-summarize") or path.startswith("/api/doctor/documents") or path.startswith("/manifest/") or path.startswith("/api/doctor/insurance/staff-intake"):
        return "Health links and clinician view"
    if path.startswith("/api/test/"):
        return "Diagnostics"
    return "Patient account"


def enrich_openapi(schema: dict) -> dict:
    """Give the existing operations readable groups without changing their routes."""
    schema["tags"] = [{"name": name, "description": description} for name, description in TAG_GUIDES]
    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation["tags"] = [TAG_NAMES.get(tag, tag) for tag in operation.get("tags", [])] or [_tag_for(path)]
    return schema


_TEMPLATE = Path(__file__).with_name("developer_docs.html").read_text(encoding="utf-8")


def render_docs(view: str) -> HTMLResponse:
    if view == "swagger":
        viewer = """<div id="swagger-ui" aria-label="Swagger API reference"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url:'/api/openapi.json',dom_id:'#swagger-ui',deepLinking:true,filter:true,docExpansion:'none',supportedSubmitMethods:[],presets:[SwaggerUIBundle.presets.apis,SwaggerUIBundle.SwaggerUIStandalonePreset],layout:'BaseLayout'});</script>"""
    elif view == "redoc":
        viewer = """<redoc spec-url="/api/openapi.json"></redoc>
<script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script>"""
    else:
        raise ValueError("Unknown documentation view")
    html = (_TEMPLATE.replace("{{VIEWER}}", viewer)
            .replace("{{VIEWER_CSS}}", '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">' if view == "swagger" else "")
            .replace("{{SWAGGER_CURRENT}}", 'aria-current="page"' if view == "swagger" else "")
            .replace("{{REDOC_CURRENT}}", 'aria-current="page"' if view == "redoc" else "")
            .replace("{{VIEW_NAME}}", "Swagger" if view == "swagger" else "ReDoc"))
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
