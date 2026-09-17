"""Read one scheduled patient's insurance inputs from an activated eCW backend app."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from app.ingestion import ecw


# This clinic's eCW backend registration does not grant ChargeItem. The query
# remains optional below and degrades to missing charge data on a 403.
SCOPES = (
    "system/Patient.read", "system/Coverage.read", "system/Encounter.read",
    "system/ServiceRequest.read", "system/Condition.read", "system/RelatedPerson.read", "system/Organization.read",
    "system/Practitioner.read", "system/Location.read",
)


class EcwBackendError(RuntimeError):
    pass


def settings() -> tuple[str, str]:
    client_id = os.getenv("ECW_BACKEND_CLIENT_ID", "").strip()
    issuer = os.getenv("ECW_BACKEND_ISSUER", "").strip().rstrip("/")
    parsed = urlparse(issuer)
    if not client_id or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EcwBackendError("The eClinicalWorks practice connection is not configured yet.")
    if not (ecw.ECW_KMS_KEY_ID or ecw.ECW_CLIENT_JWK):
        raise EcwBackendError("The eClinicalWorks signing key is not configured yet.")
    return client_id, issuer


def configured() -> bool:
    try:
        settings()
        return True
    except EcwBackendError:
        return False


def binding() -> str:
    try:
        return "|".join(settings())
    except EcwBackendError:
        return ""


def _https(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EcwBackendError(f"eClinicalWorks did not publish a valid {label}.")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EcwBackendError(f"eClinicalWorks did not publish a valid {label}.")
    return value.rstrip("/")


def _token_endpoint(metadata: dict[str, Any]) -> str:
    for rest in metadata.get("rest", []):
        security = rest.get("security", {}) if isinstance(rest, dict) else {}
        for outer in security.get("extension", []):
            if not isinstance(outer, dict) or "oauth-uris" not in str(outer.get("url", "")):
                continue
            for inner in outer.get("extension", []):
                if isinstance(inner, dict) and inner.get("url") == "token":
                    return _https(inner.get("valueUri") or inner.get("valueUrl"), "token endpoint")
    raise EcwBackendError("The eClinicalWorks metadata does not include its backend token endpoint.")


async def _response_json(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EcwBackendError(message) from exc
    if not isinstance(payload, dict):
        raise EcwBackendError(message)
    return payload


async def _access_token(client: httpx.AsyncClient, client_id: str, issuer: str) -> str:
    metadata = await _response_json(
        await client.get(f"{issuer}/metadata", params={"_format": "json"}, headers={"Accept": "application/fhir+json"}),
        "The activated eClinicalWorks practice metadata is unavailable.",
    )
    endpoint = _token_endpoint(metadata)
    token = await _response_json(
        await client.post(endpoint, data={
            "grant_type": "client_credentials",
            "scope": " ".join(SCOPES),
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": ecw._client_assertion(endpoint, client_id),
        }, headers={"Accept": "application/json"}),
        "eClinicalWorks rejected the backend app. Confirm that this practice activated the app and that its client ID, issuer, scopes and JWKS match.",
    )
    value = token.get("access_token")
    if not isinstance(value, str) or not value:
        raise EcwBackendError("eClinicalWorks did not return a backend access token.")
    return value


def _entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [resource for entry in bundle.get("entry", []) if isinstance(entry, dict)
            and isinstance((resource := entry.get("resource")), dict)
            and resource.get("resourceType") != "OperationOutcome"]


async def _search(client: httpx.AsyncClient, issuer: str, token: str, resource: str,
                  params: dict[str, str] | list[tuple[str, str]]) -> list[dict[str, Any]]:
    response = await client.get(f"{issuer}/{resource}", params=params,
                                headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"})
    if response.status_code in {403, 404}:
        return []
    return _entries(await _response_json(response, f"eClinicalWorks could not return {resource} data for this patient."))


async def _read(client: httpx.AsyncClient, issuer: str, token: str, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, str) or not re.fullmatch(r"(?:Patient|RelatedPerson|Organization|Practitioner|Location)/[A-Za-z0-9.\-]{1,200}", reference):
        return {}
    response = await client.get(f"{issuer}/{reference}", headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"})
    if response.status_code in {403, 404}:
        return {}
    return await _response_json(response, "eClinicalWorks could not resolve the appointment's provider, payer or subscriber.")


async def _bounded_read(client: httpx.AsyncClient, issuer: str, token: str,
                        reference: str) -> tuple[dict[str, Any], str | None]:
    try:
        return await asyncio.wait_for(_read(client, issuer, token, reference), timeout=5), None
    except asyncio.TimeoutError:
        return {}, reference.split("/", 1)[0]


async def _cached_patient(client: httpx.AsyncClient, issuer: str, token: str,
                          patient_id: str, profile: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    fallback = {
        "resourceType": "Patient", "id": patient_id,
        "name": [{"use": "official", "given": [str(profile.get("patient_first_name") or "")],
                  "family": str(profile.get("patient_last_name") or "")}],
        "birthDate": str(profile.get("patient_birth_date") or ""),
        "gender": {"M": "male", "F": "female"}.get(profile.get("gender"), "unknown"),
    }
    try:
        patient = await asyncio.wait_for(_read(client, issuer, token, f"Patient/{patient_id}"), timeout=5)
    except asyncio.TimeoutError:
        return fallback, "Patient"
    if not patient:
        raise EcwBackendError("The saved eClinicalWorks patient link no longer exists.")
    return patient, None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _name(resource: dict[str, Any]) -> tuple[str | None, str | None]:
    names = [item for item in resource.get("name", []) if isinstance(item, dict)]
    selected = next((item for item in names if item.get("use") in {"official", "usual"}), names[0] if names else {})
    given = selected.get("given", [])
    return (str(given[0]) if isinstance(given, list) and given else None,
            str(selected.get("family")) if selected.get("family") else None)


def _phone(resource: dict[str, Any]) -> str | None:
    value = next((item.get("value") for item in resource.get("telecom", [])
                  if isinstance(item, dict) and item.get("system") == "phone" and item.get("value")), None)
    digits = re.sub(r"\D", "", str(value or ""))[-10:]
    return digits if len(digits) == 10 else None


def _reference(value: Any) -> str | None:
    return value.get("reference") if isinstance(value, dict) and isinstance(value.get("reference"), str) else None


def _period_contains(resource: dict[str, Any], service_date: str) -> bool:
    period = resource.get("period", {}) if isinstance(resource.get("period"), dict) else {}
    return (not period.get("start") or str(period["start"])[:10] <= service_date) and (not period.get("end") or str(period["end"])[:10] >= service_date)


def _appointment_date(resource: dict[str, Any]) -> str:
    return str((resource.get("period") or {}).get("start") or "")[:10]


def _coding(resource: Any, kind: str) -> list[str]:
    found: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            code, system = value.get("code"), str(value.get("system", "")).lower()
            compact = str(code or "").replace(".", "").upper()
            if kind == "procedure" and re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", compact) and (not system or "cpt" in system or "hcpcs" in system):
                found.append(compact)
            if kind == "diagnosis" and re.fullmatch(r"[A-Z][0-9][A-Z0-9]{1,5}", compact) and "icd" in system:
                found.append(compact)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(resource)
    return list(dict.fromkeys(found))


def _npi(resource: dict[str, Any]) -> str | None:
    for identifier in resource.get("identifier", []):
        if not isinstance(identifier, dict):
            continue
        value = str(identifier.get("value", "")).strip()
        label = " ".join((str(identifier.get("system", "")), str(identifier.get("type", "")))).lower()
        if re.fullmatch(r"[1-4]\d{9}", value) and ("npi" in label or not identifier.get("system")):
            return value
    return None


def _display(resource: dict[str, Any]) -> str | None:
    if resource.get("name") and isinstance(resource.get("name"), str):
        return resource["name"]
    given, family = _name(resource)
    return " ".join(x for x in (given, family) if x) or None


def _concept_labels(value: Any) -> list[str]:
    concepts = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        if concept.get("text"):
            labels.append(str(concept["text"]))
        for coding in concept.get("coding") or []:
            if isinstance(coding, dict) and (coding.get("display") or coding.get("code")):
                labels.append(str(coding.get("display") or coding["code"]))
    return labels


def _visit_context(visit: dict[str, Any], orders: list[dict[str, Any]], conditions: list[dict[str, Any]]) -> str | None:
    """Keep exact eCW visit details together for clinician-reviewed CPT suggestions."""
    details: list[str] = []
    fields = (("Reason", visit.get("reasonCode")), ("Visit type", visit.get("type")),
              ("Service", visit.get("serviceType")))
    for label, value in fields:
        details.extend(f"{label}: {text}" for text in _concept_labels(value))
    for order in orders:
        details.extend(f"Planned service: {text}" for text in _concept_labels(order.get("code")))
        details.extend(f"Order reason: {text}" for text in _concept_labels(order.get("reasonCode")))
    for condition in conditions:
        details.extend(f"Linked condition: {text}" for text in _concept_labels(condition.get("code")))
    unique = list(dict.fromkeys(" ".join(detail.split()) for detail in details if detail.strip()))
    return "; ".join(unique)[:2000] or None


def _payer_id(payer: dict[str, Any], aliases: Any) -> str | None:
    if not isinstance(aliases, dict):
        return None
    candidates = [_display(payer), *(item.get("value") for item in payer.get("identifier", []) if isinstance(item, dict))]
    normalized = {_norm(key): value for key, value in aliases.items() if isinstance(key, str) and isinstance(value, str)}
    return next((normalized[_norm(candidate)] for candidate in candidates if _norm(candidate) in normalized), None)


def _relationship(coverage: dict[str, Any]) -> str | None:
    values = [str(c.get("code", "")).casefold() for c in (coverage.get("relationship") or {}).get("coding", []) if isinstance(c, dict)]
    mapping = {"self": "18", "18": "18", "spouse": "01", "01": "01", "child": "19", "19": "19", "other": "G8", "g8": "G8"}
    return next((mapping[value] for value in values if value in mapping), None)


def _group(coverage: dict[str, Any]) -> str | None:
    for item in coverage.get("class", []):
        if not isinstance(item, dict):
            continue
        codes = [str(c.get("code", "")).casefold() for c in (item.get("type") or {}).get("coding", []) if isinstance(c, dict)]
        if "group" in codes:
            return str(item.get("value")) if item.get("value") else None
    return None


def _number(value: Any, *, money: bool = False) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0 or number > (Decimal("1000000") if money else Decimal("10000")):
        return None
    if money:
        rounded = number.quantize(Decimal("0.01"))
        return f"{rounded:.2f}" if rounded == number else None
    return format(number.normalize(), "f")


def _source_references(resource: dict[str, Any]) -> list[str]:
    values: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("reference"), str):
                values.append(value["reference"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(resource)
    return list(dict.fromkeys(values))


async def fetch_inputs(profile: dict[str, Any], practice: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[dict, list[str], dict]:
    """Fetch and reduce eCW data; never infer codes, payer IDs, prices or provider roles."""
    client_id, issuer = settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await _access_token(client, client_id, issuer)
            cached_patient_id = str(profile.get("ecw_patient_id") or "")
            if cached_patient_id:
                if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", cached_patient_id):
                    raise EcwBackendError("The saved eClinicalWorks patient link is invalid.")
                patient, patient_timeout = await _cached_patient(client, issuer, token, cached_patient_id, profile)
                patient_rows = [patient]
            else:
                patient_timeout = None
                patient_rows = await _search(client, issuer, token, "Patient", {
                    "given": str(profile.get("patient_first_name") or ""),
                    "family": str(profile.get("patient_last_name") or ""),
                    "birthdate": str(profile.get("patient_birth_date") or ""),
                })
            matches = [row for row in patient_rows if _norm(_name(row)[0]) == _norm(profile.get("patient_first_name"))
                       and _norm(_name(row)[1]) == _norm(profile.get("patient_last_name"))
                       and row.get("birthDate") == profile.get("patient_birth_date")]
            if len(matches) != 1:
                raise EcwBackendError("eClinicalWorks did not return exactly one patient matching the confirmed patient name and birth date.")
            patient = matches[0]
            patient_id = str(patient.get("id", ""))
            if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", patient_id):
                raise EcwBackendError("eClinicalWorks returned an invalid patient identifier.")
            query = {"patient": patient_id}
            today_date = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York"))).date()
            today = today_date.isoformat()
            # ponytail: Bound eCW's slow open-ended search; widen if clinics schedule over a year ahead.
            encounter_dates = [("date", f"ge{today}"), ("date", f"le{today_date + timedelta(days=365)}")]
            coverage_rows, encounters, orders, conditions = await asyncio.gather(
                _search(client, issuer, token, "Coverage", query),
                _search(client, issuer, token, "Encounter", [("patient", patient_id), *encounter_dates]),
                _search(client, issuer, token, "ServiceRequest", query),
                _search(client, issuer, token, "Condition", query),
            )
            scheduled = [row for row in encounters
                         if _appointment_date(row) >= today and row.get("status") not in {"cancelled", "entered-in-error"}]
            next_date = min((_appointment_date(row) for row in scheduled), default="")
            next_visits = [row for row in scheduled if _appointment_date(row) == next_date]
            visit = next_visits[0] if len(next_visits) == 1 else {}
            service_date = _appointment_date(visit) or today
            eligible = [row for row in coverage_rows if row.get("status") == "active" and _period_contains(row, service_date)]
            ordered = sorted(eligible, key=lambda row: row.get("order", 999999))
            coverage = ordered[0] if ordered and (len(ordered) == 1 or ordered[0].get("order") != ordered[1].get("order")) else {}

            refs = [ref for ref in dict.fromkeys(_source_references(visit) + _source_references(coverage))
                    if ref != f"Patient/{patient_id}"]
            resolved_results = await asyncio.gather(*(_bounded_read(client, issuer, token, ref) for ref in refs))
            resolved_list = [row for row, _ in resolved_results]
            reference_timeouts = sorted({resource for _, resource in resolved_results if resource} | ({patient_timeout} if patient_timeout else set()))
            resolved = {f"{row.get('resourceType')}/{row.get('id')}": row for row in resolved_list if row.get("resourceType") and row.get("id")}

            subscriber_ref = _reference(coverage.get("subscriber"))
            subscriber_resource = patient if subscriber_ref == f"Patient/{patient_id}" else resolved.get(subscriber_ref or "", {})
            relationship = _relationship(coverage)
            if relationship == "18" and not subscriber_resource:
                subscriber_resource = patient
            sub_given, sub_family = _name(subscriber_resource)
            payer_ref = next((_reference(item) for item in coverage.get("payor", []) if _reference(item)), None)
            payer = resolved.get(payer_ref or "", {})

            provider_refs = [ref for ref in refs if ref.startswith("Practitioner/")]
            provider_resources = [resolved[ref] for ref in provider_refs if ref in resolved]
            providers = practice.get("providers", {}) if isinstance(practice.get("providers"), dict) else {}
            candidate_npis = [_npi(row) for row in provider_resources]
            rendering_npi = next((value for value in candidate_npis if value and value in providers), None)

            location_ref = next((ref for ref in refs if ref.startswith("Location/")), None)
            location = resolved.get(location_ref or "", {})
            locations = practice.get("locations", {}) if isinstance(practice.get("locations"), dict) else {}
            location_config = locations.get(location_ref or "") or locations.get(str(location.get("id") or "")) or locations.get(str(location.get("name") or "")) or {}
            billing_ref = _reference(visit.get("serviceProvider"))
            billing_npi = _npi(resolved.get(billing_ref or "", {})) or location_config.get("billing_provider_npi")
            billing = providers.get(billing_npi, {}) if billing_npi else {}
            rendering = providers.get(rendering_npi, {}) if rendering_npi else {}

            visit_refs = set(_source_references(visit))
            encounter_id = str(visit.get("id", ""))
            encounter_ref = f"Encounter/{encounter_id}" if encounter_id else ""
            charges = await _search(client, issuer, token, "ChargeItem", {
                "subject": f"Patient/{patient_id}", "context": encounter_ref,
            }) if encounter_ref else []
            relevant_orders = [row for row in orders if f"ServiceRequest/{row.get('id')}" in visit_refs or
                               _reference(row.get("encounter")) == encounter_ref or
                               str(row.get("occurrenceDateTime") or (row.get("occurrencePeriod") or {}).get("start") or "")[:10] == service_date]
            relevant_charges = [row for row in charges if _reference(row.get("context")) == encounter_ref]
            clinical_refs = set(ref for row in [visit, *relevant_orders, *relevant_charges] for ref in _source_references(row))
            relevant_conditions = [row for row in conditions if f"Condition/{row.get('id')}" in clinical_refs]
            diagnoses = list(dict.fromkeys(code for row in [visit, *relevant_orders, *relevant_charges, *relevant_conditions] for code in _coding(row, "diagnosis")))[:12]
            lines: dict[str, dict[str, Any]] = {}
            classified = ([(row, "scheduled") for row in relevant_charges] +
                          [(row, "scheduled" if f"ServiceRequest/{row.get('id')}" in visit_refs or
                            _reference(row.get("encounter")) == encounter_ref else "possible") for row in relevant_orders] +
                          [(visit, "scheduled")])
            for row, service_status in classified:
                row_diagnoses = _coding(row, "diagnosis")
                for code in _coding(row, "procedure"):
                    item = lines.setdefault(code, {"code": code, "units": None, "charge": None, "modifiers": [],
                                                  "diagnosis": None, "service_status": service_status})
                    if service_status == "scheduled":
                        item["service_status"] = "scheduled"
                    if len(row_diagnoses) == 1:
                        item["diagnosis"] = row_diagnoses[0]
                    quantity = row.get("quantity") or row.get("quantityQuantity") or {}
                    if isinstance(quantity, dict) and quantity.get("value") is not None:
                        item["units"] = _number(quantity["value"])
                    price = row.get("priceOverride") or {}
                    if isinstance(price, dict) and price.get("value") is not None:
                        item["charge"] = _number(price["value"], money=True)
            if len(diagnoses) == 1:
                for item in lines.values():
                    item["diagnosis"] = item["diagnosis"] or diagnoses[0]

            first, family = _name(patient)
            gender = {"male": "M", "female": "F"}.get(str(patient.get("gender", "")).lower(), "U")
            inputs = {
                "patient": {"patient_first_name": first, "patient_last_name": family,
                            "patient_birth_date": patient.get("birthDate"), "gender": gender},
                "subscriber": {"firstName": sub_given, "lastName": sub_family,
                               "birthDate": subscriber_resource.get("birthDate"), "memberId": coverage.get("subscriberId")},
                "relationship": relationship, "member_id": coverage.get("subscriberId"),
                "payer_id": _payer_id(payer, practice.get("payer_aliases")), "group_number": _group(coverage),
                "provider": {**billing, "npi": billing_npi}, "submitter_id": practice.get("submitter_id"),
                "rendering_provider": {**rendering, "npi": rendering_npi},
                "service_type": location_config.get("service_type"), "service_date": service_date,
                "place_of_service": location_config.get("place_of_service"), "diagnoses": diagnoses,
                "patient_phone": _phone(patient), "reason": _visit_context(visit, relevant_orders, relevant_conditions),
                "lines": list(lines.values()),
            }
    except httpx.HTTPError as exc:
        raise EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc

    coverage_missing: list[str] = []
    if len(next_visits) > 1:
        coverage_missing.append("eClinicalWorks returned more than one appointment on the next service date; an appointment-specific identifier is required to choose safely.")
    elif not visit:
        coverage_missing.append("eClinicalWorks did not return a scheduled appointment or encounter for this patient.")
    if not coverage:
        coverage_missing.append("eClinicalWorks did not identify one active primary coverage for this appointment.")
    if not inputs["member_id"] or not relationship or not all(inputs["subscriber"].values()):
        coverage_missing.append("The eClinicalWorks coverage is missing the subscriber identity, relationship or member ID required by Availity.")
    if not inputs["payer_id"]:
        coverage_missing.append(f"Administrator: map the exact eClinicalWorks payer name or identifier ({_display(payer) or 'not returned'}) to its verified Availity payer ID.")
    if not billing_npi or not billing:
        coverage_missing.append("The appointment's billing provider NPI is not in the approved Availity practice configuration.")
    if not practice.get("submitter_id"):
        coverage_missing.append("Administrator: add the Availity-issued submitter ID to the practice configuration.")
    review_missing = list(coverage_missing)
    if not inputs["lines"]:
        review_missing.append("eClinicalWorks did not return clinician-coded CPT/HCPCS services for this appointment.")
    if not diagnoses:
        review_missing.append("eClinicalWorks did not return clinician-coded ICD-10 diagnoses for these services.")
    if any(not row.get("diagnosis") or row["diagnosis"] not in diagnoses for row in inputs["lines"]):
        review_missing.append("eClinicalWorks did not associate every coded service with one of the returned ICD-10 diagnoses.")
    if any(not row.get("units") for row in inputs["lines"]):
        review_missing.append("eClinicalWorks did not return the intended units for every coded service.")
    if not rendering_npi:
        review_missing.append("The appointment's rendering provider NPI is not in the approved Availity practice configuration.")
    if not inputs["place_of_service"]:
        review_missing.append("Administrator: map this eClinicalWorks location to its two-digit place-of-service code.")
    if not inputs["service_type"]:
        review_missing.append("Administrator: map this appointment to the Availity service type used for service reviews.")
    if reference_timeouts:
        review_missing.append("eClinicalWorks did not respond in time while resolving " + ", ".join(reference_timeouts) + "; retry to complete those details.")
    missing = list(review_missing)
    if not inputs["patient_phone"]:
        missing.append("eClinicalWorks did not return the patient's phone number required by Availity PCE.")
    if any(row.get("charge") is None for row in inputs["lines"]):
        missing.append("eClinicalWorks FHIR did not return each billed line amount required by Availity PCE. Enable the practice-management/billing API field for this app; Availity does not supply this request input.")
    inputs["coverage_missing"] = list(dict.fromkeys(coverage_missing))
    inputs["review_missing"] = list(dict.fromkeys(review_missing))
    source = {"system": "eClinicalWorks", "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "_patient_id": patient_id,
              "appointment_date": service_date, "appointment_status": visit.get("status"),
              "payer": _display(payer), "location": location.get("name"),
              "timed_out_resources": reference_timeouts,
              "resources": {"Coverage": len(coverage_rows), "Encounter": len(encounters),
                            "ServiceRequest": len(orders), "ChargeItem": len(charges)}}
    return inputs, list(dict.fromkeys(missing)), source
