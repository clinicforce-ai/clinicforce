"""Approved patient-scoped eCW resource families and search categories."""

# These are the patient-scoped resource families ClinicForce requests from
# healow. A practice may grant only some of them; those denials are surfaced in
# the review screen rather than being mistaken for an empty chart.
USCDI_RESOURCE_TYPES = (
    "Patient", "AllergyIntolerance", "CarePlan", "CareTeam", "Condition", "Coverage",
    "DiagnosticReport", "DocumentReference", "Encounter", "Goal", "Immunization",
    "MedicationDispense", "MedicationRequest", "Observation", "Procedure",
    "Provenance", "ServiceRequest",
)

# Healow requires category searches; survey includes SDOH assessments.
OBSERVATION_CATEGORIES = (
    "laboratory", "vital-signs", "social-history", "survey", "exam", "obstetrics-gynecology",
)
