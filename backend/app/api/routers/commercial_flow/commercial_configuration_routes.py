"""Commercial configuration routes workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

@_api.router.post("/ecw/saved-eligibility-probe")
async def saved_eligibility_probe(request: _api.Request) -> dict:
    """Administrator-only eCW saved-evidence check; returns safe OAuth reason codes and card availability, never image bytes."""
    session = await _api._session(request)
    _api._admin(session)
    if not _api.ecw_backend.configured():
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        # This only searches saved resources. It cannot create an eligibility transaction.
        return await _api.ecw_backend.probe_saved_eligibility(_api._ecw_identity(session, _api.date.today().isoformat()))
    except _api.ecw_backend.EcwBackendError as exc:
        raise _api.HTTPException(502, str(exc)) from exc


@_api.router.post("/connections")
async def create_connection(value: _api.ConnectionInput, request: _api.Request) -> dict:
    """Create a draft payer route; activation needs verified scoped participation."""
    session = await _api._session(request); _api._admin(session)
    try: return _api.commercial_store.create_connection(session["practice_id"], value.model_dump())
    except _api.commercial.CommercialError as exc: raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/connections/{connection_id}/activate")
async def activate_connection(connection_id: str, value: _api.ConnectionActivationInput,
                              request: _api.Request) -> dict:
    """Enable one draft payer route after scoped participation and profile review."""
    session = await _api._session(request)
    _api._admin(session)
    try:
        return _api.commercial_store.activate_connection(
            session['practice_id'], connection_id, value.service_type_code,
            value.reference, str(session['staff']['id']), aliases=value.aliases)
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/connections/{connection_id}/aliases")
async def add_connection_aliases(connection_id: str, value: _api.ConnectionAliasInput,
                                 request: _api.Request) -> dict:
    """Add approved eCW payer names to an enabled route; product scopes stay separate."""
    session = await _api._session(request)
    _api._admin(session)
    try:
        return _api.commercial_store.add_connection_aliases(
            session['practice_id'], connection_id, value.aliases,
            value.reference, str(session['staff']['id']))
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(422, str(exc)) from exc


@_api.router.put("/connections/{connection_id}/collection-policy")
async def set_collection_policy(connection_id: str, value: _api.CollectionPolicyInput, request: _api.Request) -> dict:
    """Approve $0 deferral, a fixed copay, or a supported patient-share advance for one payer route."""
    session = await _api._session(request)
    _api._admin(session)
    try:
        return _api.commercial_store.set_collection_policy(
            session['practice_id'], connection_id, value.mode, value.reference,
            str(session['staff']['id']),
            allow_supported_oon_advance=value.allow_supported_oon_advance,
            allow_cms_based_advance=value.allow_cms_based_advance)
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(422, str(exc)) from exc


@_api.router.put("/connections/{connection_id}/agreement-inventory")
async def set_agreement_inventory(connection_id: str, value: _api.AgreementInventoryInput,
                                  request: _api.Request) -> dict:
    """Administrator certifies or revokes complete UHC agreement inventory for scoped visits.

    This does not establish payer-confirmed nonparticipation. New or changed
    Clinical Library agreements invalidate the certification automatically.
    """
    session = await _api._session(request)
    _api._admin(session)
    try:
        return _api.commercial_store.set_agreement_inventory(
            session['practice_id'], connection_id, value.mode, value.reference,
            [scope.model_dump(mode='json') for scope in value.scopes], str(session['staff']['id']))
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/networks")
async def create_network(value: _api.NetworkInput, request: _api.Request) -> dict:
    session = await _api._session(request); _api._admin(session)
    try: return _api.commercial_store.create_network(session["practice_id"], value.model_dump())
    except _api.commercial.CommercialError as exc: raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/profiles")
async def create_profile(value: _api.ProfileInput, request: _api.Request) -> dict:
    session = await _api._session(request); _api._admin(session)
    try: return _api.commercial_store.create_profile(session["practice_id"], value.model_dump())
    except _api.commercial.CommercialError as exc: raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/rates")
async def import_rates(request: _api.Request, payer_connection_id: str, source_type: str, source_reference: str, effective_from: str, effective_to: str | None = None, change_ceiling_bps: int = 0, file: _api.UploadFile = _api.File(...)) -> dict:
    session = await _api._session(request); _api._admin(session)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024: raise _api.HTTPException(413, "Rate imports must be 20 MB or smaller.")
    try:
        rows = _api.commercial.parse_rate_csv(content, file.filename or "rates.csv")
        payer = _api.commercial_store.connection(session['practice_id'], payer_connection_id)
        if not payer or source_type not in _api.commercial.RATE_SOURCE_TYPES or not source_reference.strip() or not 0 <= change_ceiling_bps <= 10000:
            raise _api.commercial.CommercialError('Provide a valid clinic payer, source reference, source type and change ceiling.')
        from app.core import library_files
        if not _api.object_store.enabled():
            raise _api.HTTPException(503, 'Clinic document storage is unavailable.')
        await _api.run_in_threadpool(library_files.save, session['staff'], content, file.filename or 'rates.csv',
            'text/csv', 'fee_schedule', file.filename or 'Contracted rates', payer['display_name'], effective_from, effective_to)
        return _api.commercial_store.import_rates(session["practice_id"], payer_connection_id, source_type, source_reference, _api.hashlib.sha256(content).hexdigest(), effective_from, effective_to, change_ceiling_bps, rows)
    except _api.commercial.CommercialError as exc: raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/rates/{version_id}/activate")
async def activate_rates(version_id: str, request: _api.Request) -> dict:
    session = await _api._session(request); _api._admin(session)
    try: return _api.commercial_store.activate_rates(session["practice_id"], version_id)
    except _api.commercial.CommercialError as exc: raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post("/cases")
async def start_case(value: _api.StartInput, request: _api.Request) -> _api.Any:
    session = await _api._session(request)
    try: _api.date.fromisoformat(value.service_date)
    except ValueError: raise _api.HTTPException(422, "Service date is invalid.") from None
    practice_id = session["practice_id"]
    payer = _api.commercial_store.connection(practice_id, value.payer_connection_id)
    network, profile = _api._network(practice_id, value.provider_network_id), _api.commercial_store.profile(practice_id, value.profile_id)
    if not payer or not profile or profile["payer_connection_id"] != payer["id"] or profile["provider_network_id"] != network["id"]:
        raise _api.HTTPException(422, "Payer, provider network, and estimation profile must be a matching practice configuration.")
    if payer["category"] != "commercial_medical" or payer["status"] == "suspended" or network["status"] != "verified":
        raise _api.HTTPException(409, "This payer/provider configuration is not eligible for commercial estimation.")
    workflow = {"source_mode": "commercial", "commercial": {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "profile_id": profile["id"], "service_date": value.service_date, "service_category": profile["service_category"], "lines": [], "evidence_id": None, "request_state": "not_requested"}, "review_tasks": ["Confirm expected CPT/HCPCS codes and units before checking eligibility."]}
    binding = _api.hashlib.sha256(f"commercial-v1|{payer['id']}|{network['id']}|{profile['id']}|{value.service_date}".encode()).hexdigest()
    try:
        case = await _api.run_in_threadpool(_api.store.create_case, session["patient_id"], "", binding, workflow, expected_intake_updated_at=session["intake"]["updated_at"], **_api._scope(session))
    except _api.store.StaffCaseConflict as exc: raise _api.HTTPException(409, str(exc)) from exc
    return _api._response(_api._public(case))


@_api.router.get("/cases/{case_id}")
async def get_case(case_id: str, request: _api.Request) -> _api.Any:
    return _api._response(_api._public(await _api.run_in_threadpool(_api._load, case_id, await _api._session(request))))


@_api.router.post("/cases/{case_id}/services")
async def set_services(case_id: str, value: _api.ServicesInput, request: _api.Request) -> _api.Any:
    await _api._session(request)
    raise _api.HTTPException(409, "This automatic estimate uses only services coded on the selected eClinicalWorks appointment.")


@_api.router.post("/cases/{case_id}/evidence")
async def record_evidence(case_id: str, value: _api.EvidenceInput, request: _api.Request) -> _api.Any:
    await _api._session(request)
    raise _api.HTTPException(409, "Automatic insured estimates require a direct Stedi response for the selected eClinicalWorks visit.")


@_api.router.post("/cases/{case_id}/eligibility")
async def stedi_eligibility(case_id: str, request: _api.Request) -> _api.Any:
    session = await _api._session(request); case = await _api.run_in_threadpool(_api._load, case_id, session); data = case["commercial"]
    if data.get("request_state") != "not_requested":
        raise _api.HTTPException(409, "A Stedi eligibility request already exists for this case. Check its saved result before retrying.")
    if case.get("review_tasks"):
        raise _api.HTTPException(409, "Resolve the eClinicalWorks coverage, coding, or contract review before a paid Stedi check.")
    if not data.get("lines") or any(line.get("allowed_cents") is None for line in data["lines"]):
        raise _api.HTTPException(409, "Confirm the expected CPT/HCPCS codes, units, and matching clinic rates before a paid Stedi check.")
    if _api.os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or _api.os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        raise _api.HTTPException(409, "Stedi eligibility is disabled. Set STEDI_EXECUTION_MODE=commercial_pilot and STEDI_LIVE_ELIGIBILITY_ENABLED=true only when this clinic is ready for paid production checks.")
    if not _api.object_store.enabled():
        raise _api.HTTPException(503, "Protected evidence storage must be configured before a live Stedi request.")
    try:
        current_source = await _api._assert_ecw_current(case, session)
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    api_key = _api.stedi_financial.credentials(session["practice_id"])
    if not api_key: raise _api.HTTPException(503, "Stedi production credentials are not configured on the backend.")
    payer, network = _api.commercial_store.connection(session["practice_id"], data["payer_connection_id"]), _api._network(session["practice_id"], data["provider_network_id"])
    profile = _api.commercial_store.profile(session["practice_id"], data["profile_id"])
    if not payer or not profile or payer["status"] != "staff_guidance_enabled" or network["status"] != "verified" or profile["status"] != "staff_guidance_enabled":
        raise _api.HTTPException(409, "Payer, provider network, and estimation profile must be approved before Stedi eligibility.")
    if not network["effective_from"] <= data["service_date"] <= (network.get("effective_to") or "9999-12-31"):
        raise _api.HTTPException(409, "Provider-network evidence is not effective for this appointment.")
    rules = payer["rules"]
    service_day = _api.date.fromisoformat(data["service_date"])
    if rules.get("future_dos_current_month") and service_day > _api.date.today() and (service_day.year, service_day.month) != (_api.date.today().year, _api.date.today().month):
        raise _api.HTTPException(422, "This payer permits future eligibility dates only through the end of the current month.")
    stc = rules.get("service_type_code")
    if not isinstance(stc, str) or not stc: raise _api.HTTPException(422, "Configure exactly one Stedi service-type code for this payer/profile before eligibility.")
    try:
        rendering = current_source.get("rendering_provider") or {}
        stedi_network = {**network, "rendering_first": rendering.get("first_name"),
                         "rendering_last": rendering.get("last_name")}
        body = _api.stedi_request_body(payer, stedi_network, case["ecw_coverage"], data["service_date"], stc,
                                       place_of_service=network.get('place_of_service'))
        basic_body = _api.stedi_request_body(payer, stedi_network, case["ecw_coverage"], data["service_date"], stc,
                                        include_identity_hints=False, place_of_service=network.get('place_of_service'))
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    data["request_state"] = "pending"; case = await _api.run_in_threadpool(_api._save, case, session, "stedi-request-pending")
    started = _api.time.monotonic()
    _api.log_info("Commercial estimator", "Stedi eligibility started: case=%s service_lines=%d", case_id, len(data["lines"]))
    try:
        async with _api.httpx.AsyncClient(timeout=_api.httpx.Timeout(30, connect=5)) as client:
            response, payload, _ = await _api._post_stedi_eligibility(
                client, {"Authorization": api_key, "Content-Type": "application/json"}, body, basic_body)
    except (_api.httpx.HTTPError, ValueError):
        _api.log_warning("Commercial estimator", "Stedi eligibility indeterminate: case=%s duration_ms=%d", case_id, (_api.time.monotonic() - started) * 1000)
        case = await _api.run_in_threadpool(_api._load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi request outcome is indeterminate; check Stedi activity before retrying."]
        return _api._response(_api._public(await _api.run_in_threadpool(_api._save, case, session, "stedi-indeterminate")))
    if response.is_error or not isinstance(payload, dict):
        _api.log_warning("Commercial estimator", "Stedi eligibility rejected: case=%s http_status=%s duration_ms=%d", case_id, response.status_code, (_api.time.monotonic() - started) * 1000)
        case = await _api.run_in_threadpool(_api._load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi/payer did not return usable eligibility. Review payer response in the protected audit record."]
        return _api._response(_api._public(await _api.run_in_threadpool(_api._save, case, session, "stedi-error")))
    _api.log_info("Commercial estimator", "Stedi eligibility response: case=%s http_status=%s transaction_id=%s duration_ms=%d", case_id, response.status_code, payload.get("id"), (_api.time.monotonic() - started) * 1000)
    raw_key = None
    if _api.object_store.enabled(): raw_key = await _api.run_in_threadpool(_api.object_store.put_staff_document, session["practice_id"], case_id, "stedi-eligibility.json", _api.json.dumps(payload).encode(), "application/json")
    # The normalizer only accepts Stedi's documented individual, remaining,
    # in-network benefit rows. Missing/ambiguous values stay review-required.
    benefits = _api.commercial.normalize_stedi_271(payload, stc, True, case["ecw_coverage"].get("active_coverages", 0) > 1)
    returned_member = (payload.get("subscriber") or {}).get("memberId") if isinstance(payload.get("subscriber"), dict) else None
    if returned_member and _api.re.sub(r"\W", "", str(returned_member)).casefold() != _api.re.sub(r"\W", "", str(case["ecw_coverage"]["member_id"])).casefold():
        benefits["requires_review"] = True
        benefits["normalization_notes"].append("Stedi returned a different member ID.")
    evidence = _api.EvidenceInput(source="stedi", response_reference=str(payload.get("id") or "Stedi eligibility response"), requested_dos=data["service_date"], payer_checked_at=_api._now(), expires_at=(_api.datetime.now(_api.timezone.utc) + _api.timedelta(hours=24)).isoformat(), benefits=benefits)
    _api._record_evidence(case, session, evidence.model_dump(), raw_key)
    return _api._response(_api._public(await _api.run_in_threadpool(_api._save, case, session, "stedi-evidence-recorded")))


@_api.router.post("/cases/{case_id}/calculate")
async def calculate(case_id: str, request: _api.Request) -> _api.Any:
    session = await _api._session(request); case = await _api.run_in_threadpool(_api._load, case_id, session); data = case["commercial"]
    if not data.get("evidence_id"): raise _api.HTTPException(409, "Record usable eligibility evidence before calculating.")
    if case.get("review_tasks"):
        raise _api.HTTPException(409, "Resolve the returned authorization or referral requirement before calculating.")
    try:
        await _api._assert_ecw_current(case, session)
        practice_id = session["practice_id"]
        evidence = _api.commercial_store.evidence(practice_id, data["evidence_id"])
        profile = _api.commercial_store.profile(practice_id, data["profile_id"])
        payer = _api.commercial_store.connection(practice_id, data["payer_connection_id"])
        network = _api._network(practice_id, data["provider_network_id"])
        if not evidence or not profile or not payer or evidence["source"] != "stedi":
            raise _api.commercial.CommercialError("A direct, protected Stedi response is required for an automatic insured estimate.")
        if payer["status"] != "staff_guidance_enabled" or network["status"] != "verified" or profile["status"] != "staff_guidance_enabled":
            raise _api.commercial.CommercialError("Payer, network, or estimation profile is no longer approved.")
        expiry = evidence.get("expires_at")
        if not expiry or _api.datetime.fromisoformat(expiry) <= _api.datetime.now(_api.timezone.utc):
            raise _api.commercial.CommercialError("Stedi benefits are stale. Start a new visit review.")
        expected = _api.commercial.context_fingerprint(_api.os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), _api._evidence_context(case, session, network))
        if evidence["case_id"] != case_id or evidence["context_hash"] != expected or evidence["requested_dos"] != data["service_date"]:
            raise _api.commercial.CommercialError("Stedi evidence does not match this patient, policy, provider, and appointment.")
        case["estimate"] = _api.commercial.estimate(data["lines"], evidence["normalized"], profile["rules"]); case["review_tasks"] = []
        _api.log_info("Commercial estimator", "Estimate recalculated: case=%s estimate_available=true", case_id)
        return _api._response(_api._public(await _api.run_in_threadpool(_api._save, case, session, "estimate-calculated")))
    except _api.commercial.CommercialError as exc:
        case["estimate"] = None; case["review_tasks"] = [str(exc)]
        _api.log_warning("Commercial estimator", "Estimate requires review: case=%s", case_id)
        return _api._response(_api._public(await _api.run_in_threadpool(_api._save, case, session, "estimate-review-required")))


@_api.router.post('/cash-schedules')
async def upload_cash_schedule(request: _api.Request, effective_from: _api.date, file: _api.UploadFile = _api.File(...)) -> dict:
    """Import a draft cash schedule with supported final CPT, session and named package prices; activation is separate."""
    session = await _api._session(request); _api._admin(session)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise _api.HTTPException(413, 'Price schedules must be 20 MB or smaller.')
    try:
        from app.core import library_files
        filename = file.filename or ''
        await _api.run_in_threadpool(_api.practice_prices.read_rows, content, filename)
        if not _api.object_store.enabled():
            raise _api.HTTPException(503, 'Clinic document storage is unavailable.')
        content_type = {'.csv': 'text/csv', '.pdf': 'application/pdf', **library_files.OFFICE_TYPES}[_api.Path(filename).suffix.lower()]
        if _api.Path(filename).suffix.lower() in library_files.OFFICE_TYPES:
            try:
                await _api.run_in_threadpool(library_files.office_preview, content, filename)
            except Exception:
                raise _api.commercial.CommercialError('Upload a readable Word document without macros.') from None
        await _api.run_in_threadpool(library_files.save, session['staff'], content, filename, content_type,
                               'self_pay', filename, '', effective_from.isoformat())
        return await _api.run_in_threadpool(_api.practice_prices.create, session['practice_id'], session['staff']['id'], content, filename, effective_from.isoformat())
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(422, str(exc)) from exc


@_api.router.post('/cash-schedules/{version_id}/activate')
async def activate_cash_schedule(version_id: str, request: _api.Request) -> dict:
    """Activate a clinic-owned cash schedule used on or after its effective date."""
    session = await _api._session(request); _api._admin(session)
    try:
        return await _api.run_in_threadpool(_api.practice_prices.activate, session['practice_id'], version_id)
    except _api.commercial.CommercialError as exc:
        raise _api.HTTPException(404, str(exc)) from exc
