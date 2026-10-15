"""Read saved eligibility, claims and insurance card evidence from eCW."""
from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from types import ModuleType
from typing import Any
from urllib.parse import urljoin

import httpx

from app.insurance.ecw.ecw_fields import _concept_labels, _name, _norm, _reference
from app.insurance.ecw.ecw_coverage import saved_eligibility_summary



async def fetch_claim_identifiers(backend: ModuleType, source: dict, *, transport: httpx.AsyncBaseTransport | None = None) -> list[str]:
    """Correlate remittances to this eCW patient, including same-name dependents."""
    if not source.get('_patient_id'):
        return []
    try:
        client_id, issuer = backend.settings()
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer, backend.SCOPES + ('system/Claim.read',))
            rows, _, _ = await backend._optional_search(client, issuer, token, 'Claim', {'patient': source['_patient_id']}, timeout_seconds=8)
        return list({str(item['value']) for row in rows
                     if _reference(row.get('patient')) == 'Patient/' + source['_patient_id']
                     and row.get('status') not in {'cancelled', 'entered-in-error'}
                     for item in row.get('identifier', []) if item.get('value')})
    except (backend.EcwBackendError, httpx.HTTPError):
        return []

async def fetch_saved_eligibility(backend: ModuleType, source: dict, service_date: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Read optional saved evidence without letting missing scopes block a visit."""
    fallback = {'status': 'check_with_stedi', 'message': 'The saved eCW check could not be read through this connection. Use Stedi verification; ask the eCW administrator to review saved eligibility access if this persists.'}
    if not source.get('_patient_id') or not source.get('coverage_id'):
        return {**fallback, 'message': 'Match an eCW patient and policy before reading saved eligibility. Continue with Stedi coverage recovery.'}
    try:
        client_id, issuer = backend.settings()
        async with httpx.AsyncClient(timeout=backend.SEARCH_TIMEOUT_SECONDS, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer, ('system/CoverageEligibilityResponse.read',))
            rows = await backend._search(client, issuer, token, 'CoverageEligibilityResponse', {'patient': source['_patient_id']})
        primary = saved_eligibility_summary(rows, source['_patient_id'], source['coverage_id'], service_date)
        primary['other_coverages'] = {row['id']: {k: v for k, v in
            saved_eligibility_summary(rows, source['_patient_id'], row['id'], service_date).items()
            if not k.startswith('_')}
            for row in source.get('coverage_records', []) if row.get('id') and row['id'] != source['coverage_id']}
        return primary
    except (backend.EcwBackendError, httpx.HTTPError):
        return fallback

async def probe_saved_eligibility(backend: ModuleType, profile: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Read-only proof that eCW exposes saved eligibility evidence.

    It never submits a 270 or fetches document Binary content.  Access denial is
    a meaningful capability result, never silently treated as no evidence.
    """
    client_id, issuer = backend.settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer)
            metadata = await backend._response_json(
                await client.get(f"{issuer}/metadata", params={"_format": "json"}, headers={"Accept": "application/fhir+json"}),
                "The eClinicalWorks capability statement is unavailable.",
            )
            resources = {str(row.get("type")) for rest in metadata.get("rest", []) if isinstance(rest, dict)
                         for row in rest.get("resource", []) if isinstance(row, dict) and row.get("type")}
            patients = await backend._search(client, issuer, token, "Patient", {
                "given": str(profile.get("patient_first_name") or ""),
                "family": str(profile.get("patient_last_name") or ""),
                "birthdate": str(profile.get("patient_birth_date") or ""),
            })
            matches = [row for row in patients if _norm(_name(row)[0]) == _norm(profile.get("patient_first_name"))
                       and _norm(_name(row)[1]) == _norm(profile.get("patient_last_name"))
                       and row.get("birthDate") == profile.get("patient_birth_date")]
            if len(matches) != 1 or not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", str(matches[0].get("id", ""))):
                raise backend.EcwBackendError("eClinicalWorks did not return exactly one confirmed patient for the saved-evidence probe.")
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
                    scopes = (f'system/{resource}.read',)
                    evidence_token = await backend._access_token(client, client_id, issuer, scopes)
                except backend.EcwBackendError:
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
                    bundle = await backend._response_json(response, f"eClinicalWorks could not return {resource} evidence.")
                    result["resources"][resource] = {"status": "available", "count": sum(row.get('resourceType') == resource for row in backend._entries(bundle))}
    except httpx.HTTPError as exc:
        raise backend.EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc
    return result

async def insurance_cards(backend: ModuleType, patient_id: str, *, document_id: str | None = None,
                          transport: httpx.AsyncBaseTransport | None = None):
    """Read only explicitly labeled insurance cards belonging to this patient."""
    client_id, issuer = backend.settings()
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport) as client:
        # Listing and inline cards need only DocumentReference. A denied Binary
        # scope must not hide otherwise accessible cards.
        token = await backend._access_token(client, client_id, issuer, ('system/DocumentReference.read',))
        # eCW's card and patient-document endpoints require the type parameter;
        # a patient-only search can return only CCDs and clinical notes.
        groups = []
        for document_type in ('64290-0', '103140-0'):
            try:
                groups.append(await backend._search(client, issuer, token, 'DocumentReference', {'patient': patient_id, 'type': document_type}))
            except (backend.EcwBackendError, httpx.HTTPError):
                if not groups or document_type == '64290-0':
                    raise
                # Optional patient-document access must not hide a returned insurance card.
        rows = [row for group in groups for row in group]
        if not any(row.get('resourceType') == 'DocumentReference' for row in rows):
            groups = [await backend._search(client, issuer, token, 'DocumentReference', {'patient': patient_id})]
            rows = groups[0]
        binaries = {row.get('id'): row for row in rows if row.get('resourceType') == 'Binary' and row.get('id')}
        unique_included = {}
        for group in groups:
            documents = [row for row in group if row.get('resourceType') == 'DocumentReference']
            images = [row for row in group if row.get('resourceType') == 'Binary']
            if len(documents) == len(images) == 1 and len(documents[0].get('content', [])) == 1:
                unique_included[documents[0].get('id')] = images[0]
        cards = []
        seen = set()
        for row in rows:
            if (row.get('resourceType') != 'DocumentReference' or row.get('status') != 'current'
                    or _reference(row.get('subject')) != 'Patient/' + patient_id):
                continue
            if any(c.get('code') == '53245-7' for c in row.get('type', {}).get('coding', [])):
                continue
            for index, content in enumerate(row.get('content', [])):
                attachment = content.get('attachment') or {}
                label = ' '.join([str(row.get('description') or ''), *_concept_labels(row.get('type')), str(attachment.get('title') or '')])
                mime = attachment.get('contentType')
                card_type = any(c.get('code') == '64290-0' and c.get('system') == 'http://loinc.org' for c in row.get('type', {}).get('coding', []))
                if (not card_type and not re.search(r'\b(?:insurance|payer|member|health plan)\s*(?:id\s*)?card\b', label, re.I)) or mime not in {'image/png', 'image/jpeg', 'application/pdf'}:
                    continue
                key = str(row.get('id')) + ':' + str(index)
                if key in seen:
                    continue
                seen.add(key)
                if document_id is None:
                    cards.append({'id': key, 'title': attachment.get('title') or 'eCW insurance card', 'content_type': mime})
                elif key == document_id:
                    data = attachment.get('data')
                    if not data:
                        url = urljoin(issuer + '/', str(attachment.get('url') or ''))
                        # Never forward credentials to external attachment hosts or redirects.
                        if not url.startswith(issuer + '/Binary/') or '?' in url or '#' in url or '/..' in url:
                            raise backend.EcwBackendError('This card attachment is not available through the authorized eCW connection.')
                        resource = binaries.get(url.rsplit('/', 1)[-1])
                        # eCW can include a Binary with a different ID from the attachment URL.
                        # Pair it only with the sole document in that search response.
                        if resource is None:
                            included = unique_included.get(row.get('id')) or {}
                            if included.get('contentType') == mime:
                                resource = included
                        if resource is None:
                            binary_token = await backend._access_token(client, client_id, issuer, ('system/Binary.read',))
                            with backend._stage('resource_read', resource='binary') as facts:
                                response = await client.get(url, headers={'Authorization': 'Bearer ' + binary_token, 'Accept': 'application/fhir+json'})
                                facts['http_status'] = response.status_code
                                response.raise_for_status()
                                resource = response.json()
                        if resource.get('resourceType') != 'Binary' or resource.get('contentType') != mime:
                            raise backend.EcwBackendError('The eCW card attachment has an unexpected format.')
                        data = resource.get('data')
                    if not isinstance(data, str) or len(data) > 14_000_000:
                        raise backend.EcwBackendError('The card attachment exceeds the supported size.')
                    try:
                        return base64.b64decode(data, validate=True), mime
                    except ValueError as exc:
                        raise backend.EcwBackendError('The eCW card attachment could not be read.') from exc
        if document_id is not None:
            raise backend.EcwBackendError('No matching insurance card was returned for this patient.')
        return cards
