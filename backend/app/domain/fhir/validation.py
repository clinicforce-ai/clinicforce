"""Clinical-exchange validation policy."""


def has_matchable_patient_demographics(patient: dict) -> bool:
    """Require name and birth date before creating an externally matched share."""
    names = patient.get("name")
    has_name = isinstance(names, list) and any(
        isinstance(name, dict)
        and isinstance(name.get("family"), str)
        and bool(name["family"].strip())
        and any(isinstance(given, str) and given.strip() for given in name.get("given", []))
        for name in names
    )
    return has_name and isinstance(patient.get("birthDate"), str) and bool(patient["birthDate"].strip())
