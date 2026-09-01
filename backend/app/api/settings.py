"""Runtime configuration and immutable exchange policy."""

import os

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", PUBLIC_BASE_URL).split(",") if origin.strip()]
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_INSURANCE_CARD_BYTES = 10 * 1024 * 1024
MAX_BULK_DOWNLOAD_DOCUMENTS = 50
MAX_BULK_DOWNLOAD_BYTES = 150 * 1024 * 1024
PATIENT_SESSION_COOKIE = "clinicforce_patient_session"
PATIENT_SESSION_MAX_AGE = 30 * 24 * 60 * 60
CLINICIAN_SHARE_COOKIE = "clinicforce_doctor_share"
CLINICIAN_SHARE_MAX_AGE = 15 * 60
EPIC_OAUTH_STATE_COOKIE = "clinicforce_epic_oauth"
EPIC_LIVE_SESSION_COOKIE = "clinicforce_epic_live"
ECW_OAUTH_STATE_COOKIE = "clinicforce_ecw_oauth"
ECW_LIVE_SESSION_COOKIE = "clinicforce_ecw_live"
EPIC_ENDPOINT_SYNC_ENABLED = os.getenv("EPIC_ENDPOINT_SYNC_ENABLED", "").lower() == "true"
EPIC_ENDPOINT_SYNC_INTERVAL_SECONDS = 7 * 24 * 60 * 60
SHL_SHARE_TTL_SECONDS = max(1, int(os.getenv("SHL_SHARE_TTL_SECONDS", "900")))
FHIR_SOURCE_TAG_SYSTEM = "https://clinicforce.example/fhir/source"
FHIR_VERIFICATION_TAG_SYSTEM = "https://clinicforce.example/fhir/verification"
EXCHANGE_RESOURCE_TYPES = {"AllergyIntolerance", "MedicationStatement", "MedicationRequest", "Condition", "Observation", "Immunization", "Procedure", "Coverage", "DiagnosticReport", "DocumentReference"}
ECW_IMPORT_RESOURCE_TYPES = EXCHANGE_RESOURCE_TYPES | {"CarePlan", "CareTeam", "Device", "Encounter", "Goal", "MedicationDispense", "ServiceRequest"}
SOURCE_TAGS = {"patient-entered": "Patient-entered", "document-extracted": "Extracted from patient-uploaded document", "epic-mychart": "Epic / MyChart", "ecw-healow": "eClinicalWorks / healow", "external-shl": "Imported from an external SMART Health Link"}


def insurance_beta_enabled() -> bool:
    return os.getenv("INSURANCE_BETA_ENABLED", "false").lower() == "true"
