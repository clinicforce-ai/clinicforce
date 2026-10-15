"""Public engineering guide with session-gated Swagger execution."""

from pathlib import Path

import psycopg
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core import patient_store, staff_store
from app.api.settings import PATIENT_SESSION_COOKIE, PUBLIC_BASE_URL, ALLOWED_ORIGINS

from fastapi.responses import HTMLResponse


OPENAPI_DESCRIPTION = """Start with the [ClinicForce developer guide](/api/docs#getting-started) for diagrams, setup instructions and searchable explanations. Browse all operations below. To execute a request, sign in to the patient or clinic workspace on this same site, then refresh the guide. Your existing permissions still apply. Execute performs a real action in this environment."""

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


def required_session(path: str) -> str:
    """Swagger audience only; each API retains its own finer authorization."""
    if not path.startswith("/api/"):
        return "reference"
    if path.startswith(("/api/staff/", "/api/doctor/")):
        return "clinician"
    if path.startswith("/api/patient/") or path == "/api/insurance-upload/account":
        return "patient"
    return "either"


async def docs_sessions(request: Request) -> dict[str, bool]:
    # No credentials in the response and no patient workspace/record loading.
    patient_token = request.cookies.get(PATIENT_SESSION_COOKIE, "")
    staff_token = request.cookies.get("clinicforce_staff_session", "")
    return {
        "patient": bool(await run_in_threadpool(patient_store.get_patient_session, patient_token)) if patient_token else False,
        "clinician": bool(await run_in_threadpool(staff_store.get_session, staff_token)) if staff_token else False,
    }


async def guard_docs_execution(request: Request, call_next):
    """Extra gate for Swagger requests, never a replacement for route auth."""
    if "__clinicforce_docs" in request.query_params:
        headers = {"Cache-Control": "no-store"}
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin", "").rstrip("/") not in {PUBLIC_BASE_URL.rstrip("/"), *(o.rstrip("/") for o in ALLOWED_ORIGINS)}:
            return JSONResponse({"detail": "Swagger changes must come from the ClinicForce site"}, status_code=403, headers=headers)
        try:
            sessions = await docs_sessions(request)
        except psycopg.Error:
            return JSONResponse({"detail": "Unable to verify your session; try again later"}, status_code=503, headers=headers)
        audience = required_session(request.url.path)
        allowed = any(sessions.values()) if audience == "either" else sessions.get(audience, False)
        if not allowed:
            return JSONResponse({"detail": "Sign in to the matching patient or clinic workspace in the main app, then refresh Swagger"}, status_code=403 if any(sessions.values()) else 401, headers=headers)
    return await call_next(request)


def enrich_openapi(schema: dict) -> dict:
    """Give the existing operations readable groups without changing their routes."""
    schema["tags"] = [{"name": name, "description": description} for name, description in TAG_GUIDES]
    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation["x-clinicforce-session"] = required_session(path)
            operation["tags"] = [TAG_NAMES.get(tag, tag) for tag in operation.get("tags", [])] or [_tag_for(path)]
    return schema


_SWAGGER_SCRIPT = Path(__file__).with_name("developer_docs.js").read_text(encoding="utf-8")

_TEMPLATE = Path(__file__).with_name("developer_docs.html").read_text(encoding="utf-8")

_GUIDE_SECTION_DIR = Path(__file__).with_name("developer_guide_sections")
_GUIDE_SECTIONS = "".join((_GUIDE_SECTION_DIR / name).read_text(encoding="utf-8")
                          for name in ('01-architecture-setup.html', '02-journeys-estimate.html', '03-data-insurance.html', '04-data-inspection.html', '05-debugging-infrastructure.html', '06-release.html'))


_GUIDE_STYLE = Path(__file__).with_name("developer_guide.css").read_text(encoding="utf-8")
_GUIDE_SCRIPT = Path(__file__).with_name("developer_guide.js").read_text(encoding="utf-8")


def render_docs() -> HTMLResponse:
    viewer = """<div class="viewer-fallback"><p id="swagger-session-status" role="status">Checking your main-app session…</p>
<p><a href="/" target="_blank" rel="noopener">Sign in in the main app</a> · <button type="button" onclick="location.reload()">Refresh session</button></p>
<label for="swagger-intake">Clinic intake ID (only when required by an insurance operation)</label>
<input id="swagger-intake" type="text" autocomplete="off" maxlength="200" placeholder="Intake ID from your clinic workspace">
<p>Execute sends a real request. Existing clinic permissions, patient ownership and workflow requirements still apply.</p></div>
<div id="swagger-ui" aria-label="Swagger API reference"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.0/swagger-ui-bundle.js"></script>
<script>""" + _SWAGGER_SCRIPT + "</script>"
    html = (_TEMPLATE.replace("{{GUIDE_SECTIONS}}", _GUIDE_SECTIONS)
            .replace("{{VIEWER}}", viewer)
            .replace("{{GUIDE_STYLE}}", _GUIDE_STYLE)
            .replace("{{GUIDE_SCRIPT}}", _GUIDE_SCRIPT))
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
