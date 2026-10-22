"""Commercial ecw evidence workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

def _ecw_coded_lines(practice_id: str, profile: dict, network: dict, service_date: str,
                     inputs: dict) -> tuple[list[dict[str, _api.Any]], list[str]]:
    """Use only explicit eCW codes and units; never turn visit text into a charge."""
    try:
        planned = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled"]
        if any(row.get("modifiers") for row in planned):
            return [], ["The eClinicalWorks service has a modifier that needs a matching contract rate. Review before estimating."]
        selected = [_api.ServiceLine(code=str(row.get("code") or "").upper(), units=int(str(row.get("units") or "")))
                    for row in planned]
    except (TypeError, ValueError, _api.ValidationError):
        return [], ["eClinicalWorks returned a coded service without a whole-number unit. Update the appointment coding before estimating."]
    if not selected:
        return [], ["eClinicalWorks did not return a CPT/HCPCS-coded service for this appointment. Add the planned service in eClinicalWorks before estimating."]
    try:
        matched = _api.commercial_store.matching_rates(practice_id, profile, network, service_date,
                                                  [line.model_dump() for line in selected])
    except _api.commercial.CommercialError as exc:
        return [], [str(exc)]
    by_code = {row["code"]: row for row in matched}
    lines = [{"code": line.code, "units": line.units, "rate_id": by_code.get(line.code, {}).get("id"),
              "allowed_cents": by_code.get(line.code, {}).get("allowed_cents"),
              "cash_cents": by_code.get(line.code, {}).get("cash_cents")} for line in selected]
    missing = [line["code"] for line in lines if line["allowed_cents"] is None]
    return lines, ([f"No active contracted rate matches: {', '.join(missing)}."] if missing else [])


def _ecw_identity(session: dict, service_date: str) -> dict[str, str]:
    patient = session["intake"]["patient"]
    values = {"patient_first_name": patient.get("given_name"), "patient_middle_initial": session["intake"]["fields"].get("middle_initial", ""), "patient_last_name": patient.get("family_name"),
              "patient_birth_date": str(patient.get("birth_date") or ""), "ecw_appointment_date": service_date}
    if patient.get("ecw_patient_id"):
        values["ecw_patient_id"] = str(patient["ecw_patient_id"])
    if not all(values[key] for key in ("patient_first_name", "patient_last_name", "patient_birth_date")):
        raise _api.HTTPException(422, "Confirm patient name and birth date before reading eClinicalWorks.")
    return {key: str(value) for key, value in values.items()}


def _coverage_snapshot(inputs: dict, source: dict) -> dict:
    subscriber, patient = inputs.get("subscriber") or {}, inputs.get("patient") or {}
    relationship = inputs.get("relationship")
    snapshot = {"patient_id": source.get("_patient_id"), "coverage_id": source.get("coverage_id"),
                "appointment_id": source.get("appointment_id"), "active_coverages": source.get("active_coverages", 0),
                "plan": source.get("plan"), "payer_id": inputs.get("payer_id"), "group_number": inputs.get("group_number"),
                "plan_number": inputs.get("plan_number") or source.get("plan_number"),
                "member_id": inputs.get("member_id"), "relationship": relationship,
                "member_source": (source.get('subscriber_evidence') or {}).get('member_id_source'),
                "subscriber_patient_linked": (source.get('subscriber_evidence') or {}).get('patient_linked') is True,
                "subscriber_first": subscriber.get("firstName"), "subscriber_last": subscriber.get("lastName"),
                "subscriber_birth_date": subscriber.get("birthDate"),
                "patient_first": patient.get("patient_first_name"), "patient_last": patient.get("patient_last_name"),
                "patient_birth_date": patient.get("patient_birth_date"), "patient_gender": patient.get("gender"),
                "patient_address": patient.get("address")}
    saved_response = (source.get('saved_eligibility') or {}).get('_response') or {}
    if any(row.get('inforce') is True and _api.ecw_backend._reference(row.get('coverage')) !=
           'Coverage/' + str(source.get('coverage_id')) for row in saved_response.get('insurance', []) if isinstance(row, dict)):
        snapshot['other_active_coverage'] = True
    if relationship == "18" and (source.get('subscriber_evidence') or {}).get('reference') in {
            None, 'Patient/' + str(source.get('_patient_id'))}:
        snapshot["subscriber_first"] = snapshot["subscriber_first"] or snapshot["patient_first"]
        snapshot["subscriber_last"] = snapshot["subscriber_last"] or snapshot["patient_last"]
        snapshot["subscriber_birth_date"] = snapshot["subscriber_birth_date"] or snapshot["patient_birth_date"]
    return snapshot


def _ecw_conflicts(case: dict, session: dict, inputs: dict, source: dict, *,
                   allow_cob_inquiry: bool = False) -> list[str]:
    data, fields = case["commercial"], session["intake"]["fields"]
    network = _api._network(session["practice_id"], data["provider_network_id"])
    checks = (("service date", inputs.get("service_date"), data["service_date"]),
              ("payer", inputs.get("payer_id"), data["payer_connection_id"]),
              ("billing provider NPI", source.get("billing_provider", {}).get("npi"), network["billing_npi"]),
              ("rendering provider NPI", inputs.get("rendering_provider", {}).get("npi"), network["rendering_npi"]),
              ("place of service", inputs.get("place_of_service"), network["place_of_service"]),
              ("member ID", inputs.get("member_id"), fields.get("member_id")))
    conflicts = [f"eClinicalWorks {label} does not match the confirmed intake." for label, observed, expected
                 in checks if (label != "member ID" or expected not in {None, "", "unknown"})
                 and (not observed or not expected or str(observed).strip().casefold() != str(expected).strip().casefold())]
    coverage = _api._coverage_snapshot(inputs, source)
    if not coverage["coverage_id"] or not coverage["member_id"] or not coverage["relationship"]:
        conflicts.append("eClinicalWorks did not return one identifiable coverage with member and relationship details.")
    if coverage["active_coverages"] != 1 and not (allow_cob_inquiry and coverage["active_coverages"] > 1
                                                  and coverage["coverage_id"]):
        conflicts.append("Multiple or no active eClinicalWorks coverages require coordination-of-benefits review.")
    if not all(coverage.get(key) for key in ("subscriber_first", "subscriber_last")):
        conflicts.append("eClinicalWorks did not return the policyholder identity needed for Stedi.")
    if fields.get("subscriber_birth_date") and str(fields["subscriber_birth_date"]) != str(coverage["subscriber_birth_date"]):
        conflicts.append("The card policyholder birth date differs from eClinicalWorks coverage.")
    if fields.get("subscriber_name") and _api.re.sub(r"\W", "", fields["subscriber_name"]).casefold() != _api.re.sub(r"\W", "", f"{coverage['subscriber_first']} {coverage['subscriber_last']}").casefold():
        conflicts.append("The card policyholder name differs from eClinicalWorks coverage.")
    # eCW may omit charge items, phone, or historical visits. Those inputs are
    # needed by other integrations, but are not prerequisites for this Stedi
    # estimate.  Only the explicit appointment/coverage comparison can block it.
    return list(dict.fromkeys(conflicts))


def _evidence_context(case: dict, session: dict, network: dict) -> dict:
    data, coverage = case["commercial"], case["ecw_coverage"]
    return {"practice_id": session["practice_id"], "patient_id": session["patient_id"],
            "coverage_id": session["intake"]["id"], "subscriber_relationship": coverage["relationship"],
            "payer_connection_id": data["payer_connection_id"], "service_date": data["service_date"],
            "rendering_npi": network["rendering_npi"], "place_of_service": network["place_of_service"],
            "service_category": data["service_category"], "appointment_id": coverage["appointment_id"],
            "member_id": coverage["member_id"], "ecw_coverage_id": coverage["coverage_id"]}


async def _assert_ecw_current(case: dict, session: dict) -> dict:
    data, saved = case["commercial"], case.get("ecw_coverage")
    if not saved or not saved.get("appointment_id"):
        raise _api.commercial.CommercialError("Start a new estimate from a confirmed eClinicalWorks appointment.")
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(
            _api._ecw_identity(session, data["service_date"]), _api._ecw_practice_config(session["practice_id"]), saved["appointment_id"])
        payer, network, profile = _api._practice_path(session["practice_id"], inputs, source)
    except (_api.ecw_backend.EcwBackendError, _api.HTTPException) as exc:
        raise _api.commercial.CommercialError("The eClinicalWorks appointment or coverage could not be reverified. Refresh this visit before estimating.") from exc
    if (_api._coverage_snapshot(inputs, source) != saved or _api._ecw_conflicts(case, session, inputs, source)
            or payer["id"] != data["payer_connection_id"] or network["id"] != data["provider_network_id"]
            or profile["id"] != data["profile_id"] or inputs["service_date"] != data["service_date"]):
        raise _api.commercial.CommercialError("The eClinicalWorks appointment, coverage, or contract changed. Start a new review.")
    lines, tasks = _api._ecw_coded_lines(session["practice_id"], profile, network, data["service_date"], inputs)
    if tasks or lines != data["lines"]:
        raise _api.commercial.CommercialError("The scheduled services or contracted rates changed. Start a new review.")
    return source


def _stedi_address(value: object) -> dict[str, str] | None:
    """Translate eCW's FHIR-ish address names to Stedi's JSON names."""
    if not isinstance(value, dict):
        return None
    address = {
        "addressLine1": str(value.get("addressLine1") or value.get("address1") or "").strip(),
        "city": str(value.get("city") or "").strip(),
        "state": str(value.get("state") or "").strip(),
        "postalCode": str(value.get("postalCode") or value.get("postal_code") or "").strip(),
    }
    if not all(address.values()):
        return None
    line2 = str(value.get("addressLine2") or value.get("address2") or "").strip()
    if line2:
        address["addressLine2"] = line2
    return address


def stedi_request_body(payer: dict, network: dict, coverage: dict, service_date: str, stc: str | list[str],
                       *, include_identity_hints: bool = True, allow_demographic: bool = False,
                       inquiry_only: bool = False, place_of_service: str | None = None) -> dict:
    if (coverage.get('subscriber_patient_linked') and coverage.get('relationship') in {'18', 'self'}
            and coverage.get('subscriber_first') and coverage.get('patient_first')
            and _api.insurance_identity.normalized(coverage['subscriber_first']) != _api.insurance_identity.normalized(coverage['patient_first'])):
        # A linked different subscriber needs a dependent inquiry, not the patient's DOB in their slot.
        coverage = {**coverage, 'relationship': 'dependent'}
    if inquiry_only and coverage.get('active_coverages', 0) <= 1:
        # An inquiry slot is not a policyholder assertion. Only the returned
        # patient/member identity can create verified coverage.
        coverage = dict(coverage)
        if coverage.get('relationship') in {None, '', 'unknown'}:
            coverage.update(relationship='18', subscriber_first=coverage.get('patient_first'),
                subscriber_last=coverage.get('patient_last'), subscriber_birth_date=coverage.get('patient_birth_date'))
        coverage['active_coverages'] = 1
    needed = ("relationship", "subscriber_first", "subscriber_last",
              "patient_first", "patient_last", "patient_birth_date")
    if (any(not coverage.get(key) for key in needed) or not coverage.get("active_coverages")
            or (not coverage.get('member_id') and not allow_demographic)
            or (coverage["active_coverages"] > 1 and not coverage.get("coverage_id"))):
        raise _api.commercial.CommercialError("eClinicalWorks did not return one usable primary coverage with member and policyholder details.")
    def iso_birth_date(value: str | None) -> str | None:
        if not value:
            return None
        normalized = _api.insurance_identity.birth_date(value)
        try:
            if _api.date.fromisoformat(normalized).isoformat() == normalized:
                return normalized
        except ValueError:
            pass
        raise _api.commercial.CommercialError("eClinicalWorks returned an invalid birth date for Stedi eligibility.")
    subscriber_birth_date = iso_birth_date(coverage.get('subscriber_birth_date'))
    patient_birth_date = iso_birth_date(coverage['patient_birth_date'])
    if network.get("rendering_first") and network.get("rendering_last") and network.get("rendering_npi"):
        provider = {"npi": network["rendering_npi"], "name": {"person": {
            "firstName": network["rendering_first"], "lastName": network["rendering_last"]}}}
    elif network.get("billing_npi") and network.get("organization_name"):
        provider = {"npi": network["billing_npi"], "name": {"organization": network["organization_name"]}}
    else:
        raise _api.commercial.CommercialError("eClinicalWorks did not return one matching provider name and NPI for the Stedi inquiry.")
    body = {"payerId": payer["stedi_payer_id"], "provider": provider,
            "subscriber": {"memberId": coverage.get("member_id"), "dateOfBirth": subscriber_birth_date,
                           "name": {"person": {"firstName": coverage["subscriber_first"], "lastName": coverage["subscriber_last"]}}},
            "encounter": {"dates": {"service": {"start": service_date}}, "services": [{"system": "STC", "value": code} for code in ([stc] if isinstance(stc, str) else stc)]}}
    # The eCW visit's mapped setting can narrow payer benefit rows. An absent or
    # malformed mapping must not be represented as a confirmed office visit.
    pos = str(place_of_service or '').strip()
    if _api.re.fullmatch(r'\d{2}', pos):
        body['encounter']['placeOfService'] = {'code': pos}
    body['subscriber'] = {key: value for key, value in body['subscriber'].items() if value}
    if coverage["relationship"] not in {"18", "self"}:
        body["dependent"] = {"dateOfBirth": patient_birth_date,
                             "name": {"person": {"firstName": coverage["patient_first"], "lastName": coverage["patient_last"]}}}
    # Stedi recommends beginning with its small, documented eligibility request.
    # eCW's group/plan/address/gender can improve a few payer matches, but must
    # never make a valid core check fail validation.  The caller retries once
    # without these optional hints when Stedi rejects their request shape.
    if include_identity_hints:
        subscriber = body['subscriber']
        if coverage.get('group_number'):
            subscriber['groupNumber'] = coverage['group_number']
        if coverage.get('plan_number'):
            subscriber['planNumber'] = coverage['plan_number']
        patient_identity = body.get('dependent') or subscriber
        address = _api._stedi_address(coverage.get('patient_address'))
        if address:
            patient_identity['address'] = address
        gender = {"M": "MALE", "MALE": "MALE", "F": "FEMALE", "FEMALE": "FEMALE"}.get(
            str(coverage.get('patient_gender') or '').upper())
        if gender:
            patient_identity['gender'] = gender
    return body


def _request_validation(payload: object) -> bool:
    return isinstance(payload, dict) and _api.commercial.eligibility_failure(payload)['kind'] == 'request_validation'


async def _post_stedi_eligibility(client: _api.httpx.AsyncClient, headers: dict[str, str], body: dict,
                                  basic_body: dict | None = None) -> tuple[_api.httpx.Response, dict, bool]:
    """Send one eligibility request, falling back once from optional eCW hints."""
    response = await client.post(_api.STEDI_URL, headers=headers, json=body)
    payload = response.json() if response.content else {}
    retried = False
    if basic_body and basic_body != body and _api._request_validation(payload):
        response = await client.post(_api.STEDI_URL, headers=headers, json=basic_body)
        payload = response.json() if response.content else {}
        retried = True
    # AAA 73 rejects the name, not necessarily the supplied ID. The API permits
    # a member-ID + DOB lookup. Never substitute guessed names or dependents.
    if isinstance(payload, dict) and '73' in _api.commercial.eligibility_failure(payload)['codes'] and not body.get('dependent'):
        subscriber = body.get('subscriber') or {}
        if subscriber.get('memberId') and subscriber.get('dateOfBirth'):
            retry = _api.deepcopy(basic_body or body)
            retry['subscriber'] = {key: subscriber[key] for key in ('memberId', 'dateOfBirth')}
            response = await client.post(_api.STEDI_URL, headers=headers, json=retry)
            payload = response.json() if response.content else {}
            return response, payload, True
    return response, payload, retried


def _record_evidence(case: dict, session: dict, data: dict[str, _api.Any], raw_object_key: str | None = None) -> dict:
    commercial_case = case["commercial"]
    if data["requested_dos"] != commercial_case["service_date"]:
        raise _api.commercial.CommercialError("Eligibility evidence must use the case service date.")
    network = _api._network(session["practice_id"], commercial_case["provider_network_id"])
    context = _api._evidence_context(case, session, network)
    if raw_object_key:
        data["raw_object_key"] = raw_object_key
    evidence = _api.commercial_store.save_evidence(session["practice_id"], case["id"], _api.commercial.context_fingerprint(_api.os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), context), data)
    commercial_case["evidence_id"] = evidence["id"]; commercial_case["request_state"] = "complete"
    benefits = data["benefits"]
    commercial_case["benefits_summary"] = {key: benefits.get(key) for key in (
        "active", "network_status", "deductible_remaining_cents", "oop_remaining_cents", "copay_cents",
        "coinsurance_percent", "authorization_signal", "referral_signal", "requires_review")}
    case["review_tasks"] = (["Prior authorization is required for the returned benefit. Confirm approval before estimating."] if benefits.get("authorization_signal") == "required" else
                            ["The payer could not determine prior-authorization requirements. Verify before estimating."] if benefits.get("authorization_signal") == "unknown" else
                            ["A referral requirement was returned for the selected benefit. Confirm it before estimating."] if benefits.get("referral_signal") == "required" else
                            ["The payer did not confirm that a referral is unnecessary. Verify the plan requirement before estimating."] if benefits.get("referral_signal") == "unknown" else [])
    return evidence


def _subscriber_identity(fields: dict[str, _api.Any], patient: dict[str, _api.Any]) -> tuple[str, str, str, bool]:
    relationship = str(fields.get("relationship") or "self")
    is_self = relationship in {"self", "18"}
    name = str(fields.get("subscriber_name") or "").strip() or f"{patient.get('given_name') or ''} {patient.get('family_name') or ''}".strip()
    parts = name.split()
    birth_date = str(patient.get("birth_date") if is_self else fields.get("subscriber_birth_date") or "")
    if len(parts) < 2 or not birth_date:
        raise _api.HTTPException(422, "Confirm the card member ID, policyholder first and last name, and policyholder birth date before sending Stedi eligibility.")
    return parts[0], " ".join(parts[1:]), birth_date, is_self
