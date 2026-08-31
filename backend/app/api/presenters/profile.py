"""Patient-profile response projection."""

from app.core.patient_models import PatientProfile, PatientProfileSuggestions


def patient_profile_payload(
    profile: PatientProfile | None,
    suggestions: PatientProfileSuggestions | None = None,
) -> dict[str, str | None]:
    return {
        "given_name": profile.given_name if profile else None,
        "family_name": profile.family_name if profile else None,
        "birth_date": profile.birth_date if profile else None,
        "administrative_gender": profile.administrative_gender if profile else None,
        "suggested_given_name": suggestions.given_name if suggestions else None,
        "suggested_family_name": suggestions.family_name if suggestions else None,
    }
