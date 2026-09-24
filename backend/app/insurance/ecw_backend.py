"""Read one scheduled patient's insurance inputs from an activated eCW backend app."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo

import httpx

from app.ingestion import ecw
from app.insurance.stedi_discovery import patient_address


SCOPES = (
    "system/Patient.read", "system/Coverage.read", "system/Encounter.read",
    "system/ServiceRequest.read", "system/Procedure.read", "system/Condition.read",
    "system/RelatedPerson.read", "system/Organization.read", "system/Practitioner.read",
    "system/PractitionerRole.read", "system/Location.read", "system/ChargeItem.read",
)
# These are intentionally separate from normal appointment reads. A tenant must
# approve them explicitly; a missing optional entitlement must not break intake.
EVIDENCE_SCOPES = (
    "system/CoverageEligibilityResponse.read", "system/DocumentReference.read", "system/Binary.read",
)
REFERENCE_TIMEOUT_SECONDS = 4
SEARCH_TIMEOUT_SECONDS = 6
HISTORY_TIMEOUT_SECONDS = 3
HISTORY_DAYS = 365
HISTORY_VISIT_LIMIT = 12


class EcwBackendError(RuntimeError):
    pass


class EcwResourceUnavailable(EcwBackendError):
    """A resource is not available to this activated eCW app."""


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


async def _access_token(client: httpx.AsyncClient, client_id: str, issuer: str,
                        scopes: tuple[str, ...] = SCOPES) -> str:
    metadata = await _response_json(
        await client.get(f"{issuer}/metadata", params={"_format": "json"}, headers={"Accept": "application/fhir+json"}),
        "The activated eClinicalWorks practice metadata is unavailable.",
    )
    endpoint = _token_endpoint(metadata)
    token = await _response_json(
        await client.post(endpoint, data={
            "grant_type": "client_credentials",
            "scope": " ".join(scopes),
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
    url, rows, seen = f"{issuer}/{resource}", [], set()
    origin = urlparse(issuer)
    for _ in range(10):
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or not parsed.path.startswith(origin.path.rstrip('/') + '/') or url in seen:
            raise EcwBackendError(f"eClinicalWorks returned an invalid pagination link for {resource}.")
        seen.add(url)
        response = await client.get(url, params=params,
                                    headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"})
        if response.status_code in {403, 404}:
            raise EcwResourceUnavailable(resource)
        bundle = await _response_json(response, f"eClinicalWorks could not return {resource} data for this patient.")
        rows.extend(_entries(bundle))
        next_links = [link.get('url') for link in bundle.get('link', []) if link.get('relation') == 'next']
        if not next_links:
            return rows
        if len(next_links) != 1 or not isinstance(next_links[0], str):
            raise EcwBackendError(f"eClinicalWorks returned ambiguous pagination for {resource}.")
        url, params = urljoin(str(response.url), next_links[0]), None
    raise EcwBackendError(f"eClinicalWorks {resource} results exceeded the supported page limit; the result is incomplete.")


async def _bounded_search(client: httpx.AsyncClient, issuer: str, token: str, resource: str,
                          params: dict[str, str] | list[tuple[str, str]], *, attempts: int = 1,
                          timeout_seconds: int = SEARCH_TIMEOUT_SECONDS) -> tuple[list[dict[str, Any]], str | None]:
    for _ in range(attempts):
        try:
            return await asyncio.wait_for(
                _search(client, issuer, token, resource, params), timeout=timeout_seconds,
            ), None
        except (asyncio.TimeoutError, httpx.TimeoutException):
            pass
    return [], resource


async def _optional_search(client: httpx.AsyncClient, issuer: str, token: str, resource: str,
                           params: dict[str, str] | list[tuple[str, str]], *, timeout_seconds: int = SEARCH_TIMEOUT_SECONDS
                           ) -> tuple[list[dict[str, Any]], str | None, str | None]:
    try:
        rows, timed_out = await _bounded_search(client, issuer, token, resource, params, timeout_seconds=timeout_seconds)
        return rows, timed_out, None
    except EcwResourceUnavailable:
        return [], None, resource
    except EcwBackendError:
        # A transient FHIR response problem is not evidence that this app lacks
        # the resource permission. Keep the appointment usable and avoid
        # reporting a misleading access failure to clinical staff.
        return [], None, resource + " (read failed)"


async def _read(client: httpx.AsyncClient, issuer: str, token: str, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, str) or not re.fullmatch(r"(?:Patient|RelatedPerson|Organization|Practitioner|PractitionerRole|Location|Condition|Encounter)/[A-Za-z0-9.\-]{1,200}", reference):
        return {}
    response = await client.get(f"{issuer}/{reference}", headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"})
    if response.status_code in {403, 404}:
        return {}
    return await _response_json(response, "eClinicalWorks could not resolve an appointment-linked resource.")


async def _bounded_read(client: httpx.AsyncClient, issuer: str, token: str,
                        reference: str) -> tuple[dict[str, Any], str | None]:
    resource, resource_id = reference.split("/", 1)
    try:
        return await asyncio.wait_for(_read(client, issuer, token, reference), timeout=REFERENCE_TIMEOUT_SECONDS), None
    except (asyncio.TimeoutError, httpx.TimeoutException):
        # eCW direct reads intermittently stall while the equivalent supported
        # `_id` search succeeds. Try that once without extending the API timeout.
        try:
            rows = await asyncio.wait_for(
                _search(client, issuer, token, resource, {"_id": resource_id}),
                timeout=REFERENCE_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return {}, resource
        except EcwResourceUnavailable:
            return {}, None
        return (rows[0] if len(rows) == 1 else {}), None


async def _cached_patient(client: httpx.AsyncClient, issuer: str, token: str,
                          patient_id: str, profile: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    patient, timed_out = await _bounded_read(client, issuer, token, f"Patient/{patient_id}")
    if timed_out:
        raise EcwBackendError("eClinicalWorks timed out while verifying the saved patient. Retry the lookup.")
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


def _patient_matches(resource: dict[str, Any], profile: dict[str, Any]) -> bool:
    first, family = _name(resource)
    words = str(first or "").split()
    if not words or _norm(words[0]) != _norm(profile.get("patient_first_name")) or _norm(family) != _norm(profile.get("patient_last_name")):
        return False
    names = [item for item in resource.get("name", []) if isinstance(item, dict)]
    selected = next((item for item in names if item.get("use") in {"official", "usual"}), names[0] if names else {})
    given = selected.get("given", [])
    middle = str(given[1]) if isinstance(given, list) and len(given) > 1 else words[1] if len(words) > 1 else ""
    expected = str(profile.get("patient_middle_initial") or "").strip()
    return (not expected or not middle or _norm(middle[0]) == _norm(expected[0])) and resource.get("birthDate") == profile.get("patient_birth_date")


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


def _visit_context(visit: dict[str, Any], orders: list[dict[str, Any]], procedures: list[dict[str, Any]],
                   conditions: list[dict[str, Any]]) -> str | None:
    """Keep exact eCW visit details together for clinician-reviewed CPT suggestions."""
    details: list[str] = []
    fields = (("Reason", visit.get("reasonCode")), ("Visit type", visit.get("type")),
              ("Service", visit.get("serviceType")))
    for label, value in fields:
        details.extend(f"{label}: {text}" for text in _concept_labels(value))
    for order in orders:
        details.extend(f"Planned service: {text}" for text in _concept_labels(order.get("code")))
        details.extend(f"Order reason: {text}" for text in _concept_labels(order.get("reasonCode")))
    for procedure in procedures:
        details.extend(f"Procedure: {text}" for text in _concept_labels(procedure.get("code")))
        details.extend(f"Procedure reason: {text}" for text in _concept_labels(procedure.get("reasonCode")))
    for condition in conditions:
        details.extend(f"Linked condition: {text}" for text in _concept_labels(condition.get("code")))
    unique = list(dict.fromkeys(" ".join(detail.split()) for detail in details if detail.strip()))
    return "; ".join(unique)[:2000] or None


def _visit_type(visit: dict[str, Any]) -> str | None:
    """Use only an explicit eCW appointment label; history is not a safe proxy."""
    labels = _concept_labels(visit.get("type")) + _concept_labels(visit.get("serviceType"))
    text = " ".join(labels).casefold()
    if re.search(r"\bnew\s+(?:patient|visit)\b", text):
        return "new"
    if re.search(r"\bestablished\s+(?:patient|visit)\b", text):
        return "established"
    return None


def _history_summary(visit: dict[str, Any], procedures: list[dict[str, Any]], orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a compact history card; never make a past service part of the new request."""
    encounter_ref = f"Encounter/{visit.get('id')}"
    related = [*([row for row in procedures if _reference(row.get("encounter")) == encounter_ref]),
               *([row for row in orders if _reference(row.get("encounter")) == encounter_ref])]
    labels = []
    for field in (visit.get("reasonCode"), visit.get("type"), visit.get("serviceType")):
        labels.extend(_concept_labels(field))
    # Some eCW builds keep a completed visit's code on the Encounter rather
    # than on a linked Procedure or ServiceRequest.
    codes = list(dict.fromkeys([*_coding(visit, "procedure"),
                                *(code for row in related for code in _coding(row, "procedure"))]))[:12]
    return {"date": _appointment_date(visit), "status": str(visit.get("status") or "not_returned"),
            "visit_type": _visit_type(visit) or "not_returned",
            "reason": "; ".join(dict.fromkeys(" ".join(label.split()) for label in labels if label.strip()))[:500] or None,
            "coded_services": codes}


def _payer_id(payer: dict[str, Any], aliases: Any) -> str | None:
    if not isinstance(aliases, dict):
        return None
    candidates = [_display(payer), *(item.get("value") for item in payer.get("identifier", []) if isinstance(item, dict))]
    observed = {_norm(candidate) for candidate in candidates if candidate}
    matches = {value for key, value in aliases.items()
               if isinstance(key, str) and isinstance(value, str) and _norm(key) in observed}
    return next(iter(matches)) if len(matches) == 1 else None


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


def _plan_details(coverage: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in coverage.get("class", []):
        if not isinstance(item, dict):
            continue
        for coding in (item.get("type") or {}).get("coding", []):
            key = str(coding.get("code") or "").casefold()
            value = str(item.get("value") or item.get("name") or "").strip()
            if key and value and value not in result.setdefault(key, []):
                result[key].append(value)
    return result


def _plan(coverage: dict[str, Any]) -> str | None:
    details = _plan_details(coverage)
    for kind in ("plan", "subplan", "network"):
        values = details.get(kind, [])
        if values:
            return values[0] if len(values) == 1 else None
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


async def _patient_for_profile(client: httpx.AsyncClient, issuer: str, token: str, profile: dict[str, Any]) -> tuple[dict, str, str | None]:
    """Resolve exactly one confirmed patient without exposing a broad patient search."""
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
        if not any(_patient_matches(row, profile) for row in patient_rows):
            patient_rows = await _search(client, issuer, token, "Patient", {
                "family": str(profile.get("patient_last_name") or ""),
                "birthdate": str(profile.get("patient_birth_date") or ""),
            })
    matches = {str(row.get("id") or ""): row for row in patient_rows if _patient_matches(row, profile)}
    if not matches:
        raise EcwBackendError("No eClinicalWorks patient matched the entered name and date of birth. Check the patient's eCW record before continuing.")
    if len(matches) != 1:
        raise EcwBackendError("Multiple eClinicalWorks patients matched the entered name and date of birth. Resolve the identity in eCW before continuing.")
    patient = next(iter(matches.values()))
    patient_id = str(patient.get("id", ""))
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", patient_id):
        raise EcwBackendError("eClinicalWorks returned an invalid patient identifier.")
    return patient, patient_id, patient_timeout


async def lookup_patient_and_appointments(profile: dict[str, Any], practice: dict[str, Any], *,
                                          transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Verify one eCW patient and return only current/future appointments; no eligibility request."""
    client_id, issuer = settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await _access_token(client, client_id, issuer)
            patient, patient_id, _ = await _patient_for_profile(client, issuer, token, profile)
            today = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York"))).date()
            encounters, timed_out = await _bounded_search(
                client, issuer, token, "Encounter",
                [("patient", patient_id), ("date", f"ge{today}"), ("date", f"le{today + timedelta(days=365)}")],
            )
            if timed_out:
                raise EcwBackendError("eClinicalWorks timed out while loading the patient's scheduled encounters. Reload the appointment to retry.")
    except EcwResourceUnavailable as exc:
        raise EcwBackendError(f"eClinicalWorks did not authorize ClinicForce to read {exc} data. Confirm the activated app scope and practice activation.") from exc
    except httpx.HTTPError as exc:
        raise EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc
    appointments = []
    for visit in encounters:
        appointment_id, start = str(visit.get("id") or ""), str((visit.get("period") or {}).get("start") or "")
        if (_reference(visit.get("subject")) != f"Patient/{patient_id}"
                or not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", appointment_id)
                or not start or _appointment_date(visit) < today.isoformat()):
            continue
        if str(visit.get("status") or "").casefold() in {"cancelled", "entered-in-error", "finished", "completed"}:
            continue
        appointments.append({"id": appointment_id, "start": start, "status": str(visit.get("status") or "scheduled"),
                             "reason": _visit_context(visit, [], [], []) or ""})
    first, family = _name(patient)
    return {"patient": {"id": patient_id, "given_name": first, "family_name": family,
                        "birth_date": patient.get("birthDate")},
            "appointments": sorted(appointments, key=lambda row: (row["start"], row["id"]))}


async def list_appointments(profile: dict[str, Any], practice: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, str]]:
    return (await lookup_patient_and_appointments(profile, practice, transport=transport))["appointments"]


async def list_completed_appointments(profile: dict[str, Any], practice: dict[str, Any], *,
                                      transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """Return recent completed visits for display only; never send eligibility."""
    client_id, issuer = settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await _access_token(client, client_id, issuer)
            _, patient_id, _ = await _patient_for_profile(client, issuer, token, profile)
            today = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York"))).date()
            encounters, timed_out = await _bounded_search(
                client, issuer, token, "Encounter",
                [("patient", patient_id), ("date", f"ge{today - timedelta(days=HISTORY_DAYS)}"),
                 ("date", f"le{today - timedelta(days=1)}")],
                timeout_seconds=HISTORY_TIMEOUT_SECONDS,
            )
            if timed_out:
                raise EcwBackendError("eClinicalWorks timed out while loading the patient's completed visit history. Reload to retry.")
            procedure_result, order_result = await asyncio.gather(
                _optional_search(client, issuer, token, "Procedure", {"patient": patient_id}),
                _optional_search(client, issuer, token, "ServiceRequest", {"patient": patient_id}),
            )
    except EcwResourceUnavailable as exc:
        raise EcwBackendError(f"eClinicalWorks did not authorize ClinicForce to read {exc} data. Confirm the activated app scope and practice activation.") from exc
    except httpx.HTTPError as exc:
        raise EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc
    procedures, orders = procedure_result[0], order_result[0]
    completed = sorted(
        (visit for visit in encounters if _appointment_date(visit) < today.isoformat()
         and str(visit.get("status") or "").casefold() in {"finished", "completed"}),
        key=lambda visit: (_appointment_date(visit), str(visit.get("id") or "")), reverse=True,
    )[:HISTORY_VISIT_LIMIT]
    return [{"id": str(visit.get("id") or ""), "start": str((visit.get("period") or {}).get("start") or ""),
             **_history_summary(visit, procedures, orders)} for visit in completed
            if re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", str(visit.get("id") or ""))]


async def fetch_inputs(profile: dict[str, Any], practice: dict[str, Any], appointment_id: str | None = None,
                       *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[dict, list[str], dict]:
    """Fetch and reduce eCW data; never infer codes, payer IDs, prices or provider roles."""
    client_id, issuer = settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await _access_token(client, client_id, issuer)
            patient, patient_id, patient_timeout = await _patient_for_profile(client, issuer, token, profile)
            query = {"patient": patient_id}
            today_date = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York"))).date()
            today = today_date.isoformat()
            preferred_date = str(profile.get("ecw_appointment_date") or "")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", preferred_date) or preferred_date < today:
                preferred_date = ""
            # eCW serves an exact date quickly but its date-range search can stall.
            # Reuse the last verified appointment date when refreshing an existing case.
            if appointment_id:
                if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", appointment_id):
                    raise EcwBackendError("The selected eClinicalWorks appointment identifier is invalid.")
                selected, encounter_timeout = await _bounded_read(client, issuer, token, f"Encounter/{appointment_id}")
                if not selected or _reference(selected.get("subject")) != f"Patient/{patient_id}":
                    raise EcwBackendError("The selected eClinicalWorks appointment does not belong to the confirmed patient.")
                encounters = [selected]
            elif preferred_date:
                encounters, encounter_timeout = await _bounded_search(
                    client, issuer, token, "Encounter", {"patient": patient_id, "date": preferred_date}, attempts=2,
                )
            else:
                # ponytail: Bound eCW's slow open-ended search; widen if clinics schedule over a year ahead.
                encounter_dates = [("date", f"ge{today}"), ("date", f"le{today_date + timedelta(days=365)}")]
                encounters, encounter_timeout = await _bounded_search(
                    client, issuer, token, "Encounter", [("patient", patient_id), *encounter_dates],
                )
            if encounter_timeout:
                raise EcwBackendError("eClinicalWorks timed out while loading the patient's scheduled encounters. Reload the appointment to retry.")
            scheduled = [row for row in encounters
                         if _appointment_date(row) >= today
                         and str(row.get("status") or "").casefold() not in {"cancelled", "entered-in-error", "finished", "completed"}]
            next_date = min((_appointment_date(row) for row in scheduled), default="")
            next_visits = [row for row in scheduled if _appointment_date(row) == next_date]
            visit = next_visits[0] if len(next_visits) == 1 else {}
            service_date = _appointment_date(visit) or today
            encounter_id = str(visit.get("id", ""))
            encounter_ref = f"Encounter/{encounter_id}" if encounter_id else ""
            # History is context only. A short independent query avoids letting
            # an eCW history stall block the appointment insurance workflow.
            history_start = (today_date - timedelta(days=HISTORY_DAYS)).isoformat()
            history_rows, history_timeout, history_unavailable = await _optional_search(
                client, issuer, token, "Encounter",
                [("patient", patient_id), ("date", f"ge{history_start}"), ("date", f"le{today_date - timedelta(days=1)}")],
                timeout_seconds=HISTORY_TIMEOUT_SECONDS,
            )
            completed_history = sorted(
                (row for row in history_rows if _appointment_date(row) < today
                 and str(row.get("status") or "").casefold() in {"finished", "completed"}),
                key=_appointment_date, reverse=True,
            )[:5]
            # eCW can stall unrelated patient searches when Procedure is requested
            # concurrently; collect it first so it can still enrich the visit.
            procedure_result = await _optional_search(client, issuer, token, "Procedure", query)
            coverage_result, order_result, charge_result = await asyncio.gather(
                _optional_search(client, issuer, token, "Coverage", query),
                _optional_search(client, issuer, token, "ServiceRequest", query),
                _optional_search(client, issuer, token, "ChargeItem", {
                    "subject": f"Patient/{patient_id}", "context": encounter_ref,
                }) if encounter_ref else asyncio.sleep(0, result=([], None, None)),
            )
            coverage_rows, orders, procedures, charges = (result[0] for result in
                                                          (coverage_result, order_result, procedure_result, charge_result))
            query_timeouts = {result[1] for result in (coverage_result, order_result, procedure_result, charge_result)
                              if result[1]}
            query_unavailable = {result[2] for result in (coverage_result, order_result, procedure_result, charge_result)
                                 if result[2]}
            eligible = [row for row in coverage_rows if row.get("status") == "active" and _period_contains(row, service_date)]
            ordered = sorted(eligible, key=lambda row: row.get("order", 999999))
            coverage = ordered[0] if ordered and (len(ordered) == 1 or ordered[0].get("order") != ordered[1].get("order")) else {}

            visit_refs = set(_source_references(visit))
            relevant_orders = [row for row in orders if f"ServiceRequest/{row.get('id')}" in visit_refs or
                               _reference(row.get("encounter")) == encounter_ref]
            relevant_procedures = [row for row in procedures if f"Procedure/{row.get('id')}" in visit_refs or
                                   _reference(row.get("encounter")) == encounter_ref]
            resolvable = ("Patient/", "RelatedPerson/", "Organization/", "Practitioner/", "PractitionerRole/",
                          "Location/", "Condition/")
            refs = [ref for ref in dict.fromkeys(
                ref for row in [visit, coverage, *relevant_orders, *relevant_procedures] for ref in _source_references(row)
            ) if ref != f"Patient/{patient_id}" and ref.startswith(resolvable)]
            resolved_results = await asyncio.gather(*(_bounded_read(client, issuer, token, ref) for ref in refs))
            resolved_list = [row for row, _ in resolved_results]
            timeout_resources = ({resource for _, resource in resolved_results if resource} | query_timeouts |
                                 ({patient_timeout} if patient_timeout else set()))
            resolved = {f"{row.get('resourceType')}/{row.get('id')}": row for row in resolved_list if row.get("resourceType") and row.get("id")}

            provider_refs = [ref for ref in refs if ref.startswith("Practitioner/")]
            role_searches = await asyncio.gather(*(
                _bounded_read(client, issuer, token, f"PractitionerRole/{role_id}")
                for role_id in {
                    ref.split("/", 1)[1] for ref in refs if ref.startswith("PractitionerRole/") and ref not in resolved
                }
            ))
            timeout_resources.update(resource for _, resource in role_searches if resource)
            for row, _ in role_searches:
                if row.get("resourceType") and row.get("id"):
                    resolved[f"{row['resourceType']}/{row['id']}"] = row
            role_resources = [row for row in resolved.values() if row.get("resourceType") == "PractitionerRole"]
            role_provider_refs = [ref for row in role_resources if (ref := _reference(row.get("practitioner")))]
            unresolved_provider_refs = [ref for ref in role_provider_refs if ref not in resolved]
            provider_reads = await asyncio.gather(*(
                _bounded_read(client, issuer, token, ref) for ref in unresolved_provider_refs
            ))
            timeout_resources.update(resource for _, resource in provider_reads if resource)
            for row, _ in provider_reads:
                if row.get("resourceType") and row.get("id"):
                    resolved[f"{row['resourceType']}/{row['id']}"] = row
            reference_timeouts = sorted(timeout_resources)
            conditions = list({
                f"Condition/{row.get('id')}": row for row in resolved.values()
                if row.get("resourceType") == "Condition" and row.get("id")
            }.values())

            subscriber_ref = _reference(coverage.get("subscriber"))
            subscriber_resource = patient if subscriber_ref == f"Patient/{patient_id}" else resolved.get(subscriber_ref or "", {})
            relationship = _relationship(coverage)
            if relationship == "18" and not subscriber_resource:
                subscriber_resource = patient
            sub_given, sub_family = _name(subscriber_resource)
            payer_ref = next((_reference(item) for item in coverage.get("payor", []) if _reference(item)), None)
            payer = resolved.get(payer_ref or "", {})

            provider_refs = list(dict.fromkeys([*provider_refs, *role_provider_refs]))
            provider_resources = [resolved[ref] for ref in provider_refs if ref in resolved]
            providers = practice.get("providers", {}) if isinstance(practice.get("providers"), dict) else {}
            candidate_npis = [_npi(row) for row in provider_resources]
            ecw_rendering_npi = next((value for value in candidate_npis if value), None)
            rendering_resource = next((row for row in provider_resources if _npi(row) == ecw_rendering_npi),
                                      provider_resources[0] if provider_resources else {})
            rendering_npi = next((value for value in candidate_npis if value and value in providers), None)

            location_ref = next((ref for ref in refs if ref.startswith("Location/")), None)
            location = resolved.get(location_ref or "", {})
            locations = practice.get("locations", {}) if isinstance(practice.get("locations"), dict) else {}
            location_config = locations.get(location_ref or "") or locations.get(str(location.get("id") or "")) or locations.get(str(location.get("name") or "")) or {}
            billing_ref = _reference(visit.get("serviceProvider"))
            billing_resource = resolved.get(billing_ref or "", {})
            ecw_billing_npi = _npi(billing_resource)
            billing_npi = ecw_billing_npi or location_config.get("billing_provider_npi")
            billing = providers.get(billing_npi, {}) if billing_npi else {}
            rendering = providers.get(rendering_npi, {}) if rendering_npi else {}

            relevant_charges = [row for row in charges if _reference(row.get("context")) == encounter_ref]
            recent_completed_appointments = [_history_summary(row, procedures, orders) for row in completed_history]
            clinical_refs = set(ref for row in [visit, *relevant_orders, *relevant_procedures, *relevant_charges] for ref in _source_references(row))
            relevant_conditions = [row for row in conditions if _reference(row.get("encounter")) == encounter_ref
                                   or f"Condition/{row.get('id')}" in clinical_refs]
            diagnoses = list(dict.fromkeys(code for row in [visit, *relevant_orders, *relevant_procedures,
                                                            *relevant_charges, *relevant_conditions]
                                            for code in _coding(row, "diagnosis")))[:12]
            lines: dict[str, dict[str, Any]] = {}
            classified = ([(row, "scheduled") for row in relevant_charges] +
                          [(row, "scheduled") for row in relevant_procedures] +
                          [(row, "scheduled" if f"ServiceRequest/{row.get('id')}" in visit_refs or
                            _reference(row.get("encounter")) == encounter_ref else "possible") for row in relevant_orders] +
                          [(visit, "scheduled")])
            for row, service_status in classified:
                row_diagnoses = _coding(row, "diagnosis")
                for code in _coding(row, "procedure"):
                    line_id = f"{row.get('resourceType', 'service')}:{row.get('id', 'visit')}:{code}"
                    item = lines.setdefault(line_id, {"id": line_id, "code": code, "units": None, "charge": None, "modifiers": [],
                                                  "diagnosis": None, "service_status": service_status})
                    description = next((label for label in _concept_labels(row.get("code"))
                                        if _norm(label) != _norm(code)), None)
                    if description:
                        item["description"] = description
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
                        if item["charge"] is not None:
                            item["charge_source"] = "visit"
            counts = {code: sum(item["code"] == code for item in lines.values()) for code in {item["code"] for item in lines.values()}}
            for item in lines.values():
                item["needs_confirmation"] = counts[item["code"]] > 1
            if len(diagnoses) == 1:
                for item in lines.values():
                    item["diagnosis"] = item["diagnosis"] or diagnoses[0]

            first, family = _name(patient)
            gender = {"male": "M", "female": "F"}.get(str(patient.get("gender", "")).lower(), "U")
            inputs = {
                "patient": {"patient_first_name": first, "patient_last_name": family,
                            "patient_birth_date": patient.get("birthDate"), "gender": gender, "address": patient_address(patient)},
                "subscriber": {"firstName": sub_given, "lastName": sub_family,
                               "birthDate": subscriber_resource.get("birthDate"), "memberId": coverage.get("subscriberId")},
                "relationship": relationship, "member_id": coverage.get("subscriberId"),
                "payer_id": _payer_id(payer, practice.get("payer_aliases")), "group_number": _group(coverage),
                "provider": {**billing, "npi": billing_npi}, "submitter_id": practice.get("submitter_id"),
                "rendering_provider": {**rendering, "npi": rendering_npi},
                "service_type": location_config.get("service_type"), "service_date": service_date,
                "place_of_service": location_config.get("place_of_service"), "diagnoses": diagnoses,
                "patient_phone": _phone(patient),
                "reason": _visit_context(visit, relevant_orders, relevant_procedures, relevant_conditions),
                # Do not infer new-versus-established from an incomplete FHIR
                # history search. eCW must label the appointment explicitly.
                "visit_type": _visit_type(visit),
                "lines": list(lines.values()),
            }
    except EcwResourceUnavailable as exc:
        raise EcwBackendError(f"eClinicalWorks did not authorize ClinicForce to read {exc} data. Confirm the activated app scope and practice activation.") from exc
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

    ecw_missing: list[str] = []
    if not visit:
        ecw_missing.append("No single upcoming eClinicalWorks encounter could be selected for this patient.")
    if not coverage:
        ecw_missing.append("No single active eClinicalWorks coverage was linked to this appointment.")
    if coverage and (not inputs["member_id"] or not relationship or not all(inputs["subscriber"].values())):
        ecw_missing.append("The eClinicalWorks coverage is missing subscriber identity, relationship, or member ID.")
    if coverage and not _display(payer) and "Organization" not in reference_timeouts:
        ecw_missing.append("The eClinicalWorks coverage did not return its payer organization.")
    if visit and not inputs["reason"]:
        ecw_missing.append("The eClinicalWorks encounter has no reason, visit type, service, order, procedure, or linked condition.")
    if visit and not inputs["lines"]:
        ecw_missing.append("No CPT/HCPCS-coded ServiceRequest, Procedure, ChargeItem, or Encounter service is linked to this appointment in eClinicalWorks.")
    if visit and not diagnoses:
        ecw_missing.append("No ICD-10 diagnosis is linked to this eClinicalWorks appointment, order, or procedure.")
    if "Patient" not in reference_timeouts and not inputs["patient_phone"]:
        ecw_missing.append("The eClinicalWorks Patient record did not return a phone number.")
    if "Practitioner" not in reference_timeouts and not ecw_rendering_npi:
        ecw_missing.append("The eClinicalWorks appointment did not return a practitioner NPI.")
    if billing_ref and "Organization" not in reference_timeouts and not ecw_billing_npi:
        ecw_missing.append("The eClinicalWorks service-provider organization did not return an NPI.")
    if visit and "Location" not in reference_timeouts and not location:
        ecw_missing.append("The eClinicalWorks appointment did not return its location details.")
    if reference_timeouts:
        ecw_missing.append("eClinicalWorks timed out while retrieving or resolving " + ", ".join(reference_timeouts) + "; reload the appointment to retry.")
    if history_timeout:
        ecw_missing.append("eClinicalWorks did not respond in time while loading recent completed appointment history; reload the appointment to retry.")
    elif history_unavailable:
        ecw_missing.append("eClinicalWorks rejected or could not return recent completed appointment history for this app.")
    if "ChargeItem" in query_unavailable:
        ecw_missing.append("ClinicForce cannot read eClinicalWorks charge items for this appointment. Confirm system/ChargeItem.read is active for this practice.")
    remaining_unavailable = sorted(query_unavailable - {"ChargeItem"})
    if remaining_unavailable:
        ecw_missing.append("eClinicalWorks rejected or could not return " + ", ".join(remaining_unavailable) + " for this appointment.")

    role_labels = list(dict.fromkeys(
        label for role in role_resources for field in (role.get("code"), role.get("specialty"))
        for label in _concept_labels(field)
    ))
    source = {"system": "eClinicalWorks", "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "_patient_id": patient_id,
              "appointment_id": encounter_id, "coverage_id": str(coverage.get("id") or ""),
              "active_coverages": len(eligible), "plan": _plan(coverage), "plan_details": _plan_details(coverage),
              "appointment_date": service_date, "appointment_status": visit.get("status"),
              "visit_type": inputs.get("visit_type") or "not_returned",
              "recent_completed_appointments": recent_completed_appointments,
              "history_status": "timed_out" if history_timeout else "unavailable" if history_unavailable else "loaded",
              "payer": _display(payer),
              "payer_identifiers": [str(item["value"]) for item in (payer.get("identifier") or [])
                                    if isinstance(item, dict) and item.get("value") and
                                    ("payer" in str(item.get("system") or "").casefold() or
                                     "x12" in str(item.get("system") or "").casefold())],
              "location": location.get("name"), "location_id": str(location.get("id") or location_ref or ""),
              "billing_provider": {"name": _display(billing_resource), "npi": ecw_billing_npi},
              "rendering_provider": {"name": _display(rendering_resource), "npi": ecw_rendering_npi,
                                     "first_name": _name(rendering_resource)[0],
                                     "last_name": _name(rendering_resource)[1],
                                     "roles": role_labels[:10]},
              "missing": list(dict.fromkeys(ecw_missing)),
              "timed_out_resources": reference_timeouts,
              "unavailable_resources": sorted(query_unavailable),
              "resources": {"Coverage": len(coverage_rows), "Encounter": len(encounters),
                            "PastEncounter": len(completed_history),
                            "ServiceRequest": len(relevant_orders), "Procedure": len(relevant_procedures),
                            "Condition": len(conditions), "PractitionerRole": len(role_resources),
                            "ChargeItem": len(charges)}}
    return inputs, list(dict.fromkeys(missing)), source


async def probe_saved_eligibility(profile: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Read-only proof that eCW exposes saved eligibility evidence.

    It never submits a 270 or fetches document Binary content.  Access denial is
    a meaningful capability result, never silently treated as no evidence.
    """
    client_id, issuer = settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await _access_token(client, client_id, issuer)
            metadata = await _response_json(
                await client.get(f"{issuer}/metadata", params={"_format": "json"}, headers={"Accept": "application/fhir+json"}),
                "The eClinicalWorks capability statement is unavailable.",
            )
            resources = {str(row.get("type")) for rest in metadata.get("rest", []) if isinstance(rest, dict)
                         for row in rest.get("resource", []) if isinstance(row, dict) and row.get("type")}
            patients = await _search(client, issuer, token, "Patient", {
                "given": str(profile.get("patient_first_name") or ""),
                "family": str(profile.get("patient_last_name") or ""),
                "birthdate": str(profile.get("patient_birth_date") or ""),
            })
            matches = [row for row in patients if _norm(_name(row)[0]) == _norm(profile.get("patient_first_name"))
                       and _norm(_name(row)[1]) == _norm(profile.get("patient_last_name"))
                       and row.get("birthDate") == profile.get("patient_birth_date")]
            if len(matches) != 1 or not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", str(matches[0].get("id", ""))):
                raise EcwBackendError("eClinicalWorks did not return exactly one confirmed patient for the saved-evidence probe.")
            patient_id = str(matches[0]["id"])
            result: dict[str, Any] = {
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "capability_resources": sorted(resources & {"CoverageEligibilityResponse", "DocumentReference", "Binary"}),
                "resources": {},
            }
            for resource in ("CoverageEligibilityResponse", "DocumentReference"):
                # One unsupported optional scope must not hide other authorized
                # evidence resources or report that the working app is rejected.
                try:
                    evidence_token = await _access_token(client, client_id, issuer, SCOPES + (f"system/{resource}.read",))
                except EcwBackendError:
                    result['resources'][resource] = {'status': 'scope_denied_or_unavailable', 'count': None}
                    continue
                response = await client.get(f"{issuer}/{resource}", params={"patient": patient_id},
                                            headers={"Authorization": f"Bearer {evidence_token}", "Accept": "application/fhir+json"})
                if response.status_code in {401, 403}:
                    result["resources"][resource] = {"status": "forbidden", "count": None}
                elif response.status_code == 404:
                    result["resources"][resource] = {"status": "not_supported_or_not_found", "count": None}
                elif response.is_error:
                    result['resources'][resource] = {'status': 'search_unavailable', 'count': None}
                else:
                    bundle = await _response_json(response, f"eClinicalWorks could not return {resource} evidence.")
                    result["resources"][resource] = {"status": "available", "count": sum(row.get('resourceType') == resource for row in _entries(bundle))}
    except httpx.HTTPError as exc:
        raise EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc
    return result
