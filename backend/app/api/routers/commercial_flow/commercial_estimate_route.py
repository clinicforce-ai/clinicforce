"""Commercial estimate route workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_saved import reuse_saved_eligibility
from app.api.routers.commercial_flow.commercial_previsit_reuse import reuse_previsit_case
from app.api.routers.commercial_flow.commercial_previsit_submit import submit_previsit_eligibility

@_api.router.post("/appointments/previsit", description=(
    "Estimate a patient-bound eCW visit. Compatible saved payer evidence is reused without another "
    "eligibility request. A recent single discovery candidate with an address conflict can trigger "
    "one automatic member-specific payer inquiry; only a matching active 271 supplies benefits. "
    "A booked EMG/NCS visit without a study count returns one provisional "
    "scenario with mutually exclusive CPT levels; an exact suggested CPT plan is priced immediately "
    "from patient-bound benefits and clinic rates, and clinician edits recalculate it. A booked unilateral "
    "cervical radiculopathy EMG can preview 95909 plus 95886 when no study count or usable model plan exists; "
    "the study and muscle counts remain clinician-review assumptions. A distinct planned "
    "follow-up may add provisional established CPT 99213 when office history supports that status; "
    "final MDM or time and modifier need clinician review. An uncoded "
    "established-patient office visit without a supported level starts with provisional CPT 99213, "
    "which requires clinician review before billing. A skin-biopsy "
    "booking without a site count starts with an editable three-site CPT 11104 + 11105 x2 scenario. A supported "

    "fixed office copay is the visit-time amount unless an applicable individual in-network "
    "out-of-pocket limit reduces the covered visit share to zero. A payer-reported zero balance "
    "whose medical scope cannot be confirmed withholds a positive advance and sets "
    "collection_guidance.unconfirmed_zero_oop. Later costs are estimated separately. "
    "collection_guidance.office_visit_copay_advance contains the approved single-office-visit "
    "advance after network, member, requirement and collection-policy checks; otherwise it is null. A "
    "fresh payer check includes service categories from saved selected CPTs. For BCBSTX route QDTRP, "
    "each service category uses its own 270 inquiry; linked responses must match the member, medical "
    "plan and visit date before combined pricing. A missing second response leaves collection unresolved. "
    "A matching exact-CPT "
    "Stedi inquiry automatically resolves authorization for the bound member and visit setting. A signed "
    "same-payer clinic agreement may set benefits.network_source to clinic_group_agreement and "
    "return mismatch_warnings for differing product or provider details. For NMCT (practice_id primary), "
    "clinic_nmct_policy selects the in-network tier for every insurer and product unless a "
    "provider-specific payer statement reports out-of-network participation. evidence_resolution reports "
    "unresolved facts; an in-network tier alone does not establish an amount to collect."
))
async def previsit_estimate(value: _api.PrevisitInput, request: _api.Request, background_tasks: _api.BackgroundTasks = None) -> dict:
    """Estimate suggested or edited visit CPTs from current eligibility and clinic prices.

    Uses applicable saved eCW or Stedi benefits, verified network mappings and
    active clinic rate schedules. A disclosed clinic-price reference may support
    a provisional advance when no contracted allowance is available. The optional
    eCW saved-eligibility read reports an allowlisted OAuth reason code when its
    separate scope is rejected; a successful Stedi 271 remains valid benefit
    evidence. Missing eligibility or secondary-benefit coordination does not
    imply zero liability.
    Explicit visit-linked eCW billing notes can supply a collection instruction
    or a fallback office copay; their source and timestamp accompany the result.
    They do not establish payer eligibility.
    Before any new Stedi request, patient-bound card OCR compares with eCW and
    fills only missing matched inquiry fields. A single conflicting card with
    matching patient identity can use one uniquely supported card payer route;
    its response must verify identity and active coverage before use. Other
    conflicts and multiple card sets hold the request for review. Neither path
    edits eCW or repeats an indeterminate paid request. card_check excludes image bytes
    and reports ecw_no_matching_card_document when the eCW card search returns
    no qualifying patient-bound insurance card.
    payment_explanation gives one doctor-only sentence, a reason code, network
    tier, pricing basis, unpriced CPTs and an optional resolved-mismatch note.
    encounter_summary.coinsurance_rounded_per_line is true when individual CPT
    rounding prevents one accurate percentage-of-combined-base explanation.
    A supported fixed office copay is the visit-time collection amount; the
    complete estimated patient share may include later deductible and coinsurance.
    An inconsistent saved CPT coinsurance formula withholds a positive advance.
    A cash fallback names an exact product or clinic contract mapping blocker
    when that blocker prevented an insured amount. For UnitedHealthcare, an
    unresolved insured amount stays in insurance mode and gives a short reason.
    Without an approved collection policy, missing collection inputs return a
    null amount with status action_required and basis missing_collection_inputs.
    If no supported copay or patient-share estimate exists, an administrator-
    approved payer collection policy can give a $0 check-in advance with basis
    claim_after_service. This does not waive later adjudicated patient charges.
    A scoped Blue Cross Clinical Library CPT price can appear in
    temporary_rate_reference as a disclosed planning reference only; it does
    not establish UnitedHealthcare participation or a collection amount.
    UnitedHealthcare Medicare Advantage is checked through the UHC payer route;
    the separate Original Medicare integration is outside this workflow.
    benefits.oop_annual_cents reports only a unique individual plan-wide
    out-of-pocket maximum, separate from the remaining balance.
    benefits.oop_annual_conflict_ids and oop_remaining_conflict_ids retain
    same-scope payer row paths when those individual amounts conflict.
    insurance_diagnosis.pricingStatus is PARTIAL_AMOUNT when a known office
    copay is shown but a separately planned CPT remains unpriced.
    A single source-backed suggested CPT plan appears in selected_ids and
    the priced code list together. The doctor may edit it later; pending or
    unknown referral or authorization status does not by itself withhold an
    otherwise supported amount. Explicit denied or expired status still does. A generic EMG
    booking does not select a nerve-study count or muscle examination.
    An explicit unilateral cervical radiculopathy EMG can use a provisional
    five-to-six-study NCS and complete one-extremity needle-EMG scenario when
    no actual count or usable model proposal exists. No separate E/M is added
    without a documented distinct office visit.
    An uncoded new or established consultation leaves the E/M level unselected
    and prices its mutually exclusive code family as alternatives; a common
    supported office copay is collected once, never once per alternative.
    A single explicit office E/M CPT in the selected eCW Encounter.serviceType
    can supply one planned unit when no other visit-linked coded line exists.
    Out-of-network benefit terms are retained; a percentage without a verified
    payer allowed amount never becomes a dollar total. Self-pay uses recorded cash mode or the terminal coverage-recovery fallback
    and configured cash prices, without built-in clinic prices or plan-name
    benefit overrides. Earlier snapshots are unchanged.

    Missing policy/routing or failed identity checks start bounded background
    recovery through linked saved policies, card/OCR, uniquely routed eCW hints,
    discovery and, for eligible enrolled Original Medicare cases, MBI lookup.
    Applicable saved/discovered 271 benefits are reused before another request.
    A unique patient/address/active medical match continues automatically;
    multiple active policies never select a primary without evidence.
    For a matched active UHC member with reported overlapping coverage, a
    COB-supported route may send one separate Stedi COB check. The request uses
    the returned 271 member identity and only a current or past supported
    service date. A future appointment keeps the precise date blocker; reopening
    it on the visit date retries COB from a fresh saved 271 without another 270.
    A missing 271 birth date may use the corresponding matched
    eCW Coverage or Patient date, with its source retained. Its payer roles
    come only from medical other-payer rows and are evidence, not automatic policy ordering
    or proof of the secondary plan's payment. A mismatched identity, unsupported
    route or indeterminate prior COB submission never triggers a paid retry.
    Recovery may try up to two recorded prior addresses
    after completed no-match responses, never guessing identifiers or replaying
    an indeterminate submission. Pending results are read from /cases/{id}/previsit.
    recovery.coverage_status is VERIFIED, REVIEW_REQUIRED, or NOT_FOUND;
    NOT_FOUND describes a completed empty electronic search with no earlier
    active payer response, not uninsured. An earlier active 271 with an empty
    follow-up search remains REVIEW_REQUIRED until identity is reconciled.
    Returned policies rejected by identity filtering remain REVIEW_REQUIRED.
    Labeled appointment-note payer/member candidates are retained without a
    Coverage record; they never supply an inferred subscriber or eligibility.
    An eCW patient no-match/ambiguity returns HTTP 409 with code
    ecw_patient_selection_required before payer requests; staff must confirm
    a patient through the lookup workflow.

    Terminal and unavailable searches do not imply recovery is still pending.
    recovery.exhausted remains false until every applicable stage has finished;
    recovery.steps records safe outcomes and unavailable prerequisites.
    Completed recovery without usable coverage, or completed insurance processing
    without a supported insurance amount, falls back to configured cash pricing
    unless staff explicitly retained insurance mode or the payer is UnitedHealthcare.
    Missing cash rates leave
    collection_guidance.amount_cents null; no price is invented.
    Office estimates reconcile corroborated Cigna base/suffix identifiers, use the
    rendering provider specialty and visit setting, scope named TMJ exclusions to
    linked clinical context, and recognize provider-specific network evidence.
    identity.reconciliation_rule and benefits.excluded_service_evidence_ids retain
    the applied rules; appointment_context.rendering_provider.specialty_evidence
    identifies an optional public NPPES fallback. Unknown ledger values remain
    unknown, and missing cards or contract rates do not alone block a verified
    fixed office copay.
    Coverage source is benefit_source for returned eligibility, otherwise the
    eCW policy or discovery state. network_evidence includes document_id,
    document_label and locator only when the applied clinic network profile
    traces to the same practice's uploaded PDF contract. The document route
    requires a staff session and serves the original PDF.
    A current signed same-payer agreement may set network_source to
    clinic_group_agreement only with a member-network link and exact verified
    rendering, billing and office scope. A Texas BlueCard PPO link can combine
    a patient-bound distinct Blue home-plan verifier with the signed agreement's
    BlueCard clause and Blue Choice PPO attachment; network_evidence retains
    the clause page and home-plan evidence path. Product, provider and office
    mismatches leave the tier unresolved. A selected provisional CPT suggestion
    remains editable and is not final coding.
    Its decision distinguishes participating, nonparticipating, exclusion from
    the available agreement, and lack of applicable evidence. A separate
    agreement_exclusion retains the source-linked excluded agreement when
    another source establishes the provider tier.
    payer_source identifies eCW Coverage.payor, appointment notes, card OCR or
    verified Stedi discovery when that source is known. A stored clinic network
    candidate remains separate from patient-specific participation; a broad PPO
    description does not activate an exact contract product mapping. An explicit
    Stedi planNetwork name can resolve a generic PPO/HMO label only when it
    matches the dated provider contract and eligibility identity/date checks.

    insurance_diagnosis separately reports identity, eligibility and pricing status,
    raw/retained/verified candidate counts, enrollment state and specific blockers.
    A missing out-of-network payer allowance is separate from a missing
    in-network contract rate and an unsourced provider network label.
    A prior enrollment failure does not block a current active Stedi 271.
    eCW Coverage.subscriber references are resolved to patient-linked identities.
    Dated payer evidence can automatically reconcile a stale subscriber/relationship
    using the same full member ID and corroborating patient, address or policy
    evidence. A one-edit surname difference additionally requires matching DOB,
    first name, full member ID and address. benefits.policy_match records applied
    policy differences; original eCW evidence remains unchanged. There is no
    policy-reconciliation input form or write endpoint. Neither API is treated
    as a roster of all members enrolled in a plan.
    Protected recovery.candidates retain partial subscriber/dependent identities;
    absent subscriber DOB stays null and does not discard a dependent's policy.
    Library rules are prepared automatically before pricing. library_pricing
    retains source processing summaries and missing dependencies. Candidates
    expose identity_status, group_number and dated coverage_evidence separately.
    Multiple policy/payer suggestions are optional; choosing one for display
    does not submit eligibility or postpone terminal automatic cash fallback.
    requirement_evidence separately reports referral and authorization status,
    reason, source, workbook verification channel/contact and portal URL, and
    an applicable original contract PDF page. Referral referral_obtained and
    authorization pa_approval report member-specific clearance separately from
    the published requirement; legacy approval_status remains for older clients.
    Official rule sources include URL, version, effective dates, verification
    date and applied product/CPT scope. Its procedures array reports
    the same conservative decision for each returned planned or conditional CPT; a visit with any
    established required selected procedure remains Required while other CPTs can lack
    a sourced rule. candidate_requirements reports each unselected suggested or recommended CPT
    separately and does not affect the selected-visit summary. An unselected
    E/M alternative may show a conditional exact-CPT rule; differing or missing
    alternatives do not become a visit-wide Yes or No. The current case identity
    takes precedence over older nested benefit identity before contract, matrix
    or payer rules establish an applicable answer. A generic payer label such as
    United Healthcare cannot match an exact-product rule. The Texas Choice Plus
    no-referral rule requires all specific product labels to identify Choice Plus.
    Source entries include an
    original Library document ID or an official manual/retained Stedi check URL
    when available. A link to a check that does not state a
    requirement is context, not a yes/no rule. encounter_summary separates
    collect_today_cents from the
    complete patient total and later balance; unavailable amounts remain null.
    Its complete component breakdown includes the coinsurance percentage and
    post-deductible base. allowed_cap_adjustment_cents reports the reduction to
    the payer allowed amount separately from oop_adjustment_cents, which reports
    the reduction under the applicable remaining out-of-pocket limit. The saved
    amount card itemizes these values only when they reconcile to collection.
    encounter_summary.integrity_error names a detectable mismatch between
    selected-CPT shares, their component amounts, and the saved collection after
    recorded payment; the unsafe positive advance is withheld in both collection
    guidance and encounter summary, with payment_explanation.reason_code set to
    calculation_conflict for a one-line staff action.
    Each price_range line reports a typed rate_resolution status and the exact
    candidate rows supplied to the calculator, without claiming an unqueried
    payer schedule was searched.
    Exact plan and CPT research comes from the bundled three-sheet NMCT matrix
    or a valid current Clinical Library plan_matrix upload; research is labeled
    and never implies a successful portal check or authorization approval.
    A generic PPO/HMO description cannot defeat the unique scoped clinic
    contract. A current, explicit payer requirement for a selected CPT is Yes
    even when static research lacks the exact member product; absent payer
    indicators do not establish No.
    A second automatic Stedi inquiry can supply exact selected-CPT and
    place-of-service authorization terms for the same bound member. Its
    matched terms take precedence over broad matrix research.
    Conflicting or incomplete evidence remains verification required. These
    card findings do not change the negotiated rate or collection calculation.
    """
    session = await _api._session(request)
    library_documents = await _api.run_in_threadpool(_api.library_pricing.prepare, session['practice_id'])
    session['_library_documents'] = library_documents
    session['library_pricing'] = [{'document_id': row['id'], 'label': row['label'], 'payer': row.get('payer'), 'summary': row.get('extraction_summary')} for row in library_documents]
    _api.collection_event('previsit_started', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     staff_id=(session.get('staff') or {}).get('id'), stage='appointment')
    if not _api.ecw_backend.configured():
        _api.collection_event('previsit_blocked', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='ecw_not_configured')
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured.")
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(
            _api._ecw_identity(session, _api.date.today().isoformat()),
            _api._ecw_practice_config(session["practice_id"]), value.appointment_id)
    except _api.ecw_backend.EcwBackendError as exc:
        _api.collection_event('previsit_failed', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='ecw_error')
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return _api.JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise _api.HTTPException(502, str(exc)) from exc
    if source.get("appointment_id") != value.appointment_id:
        _api.collection_event('previsit_failed', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='appointment_mismatch')
        raise _api.HTTPException(409, "The selected upcoming eClinicalWorks appointment could not be verified.")
    source['_request_ip_chain'] = _api._request_ip_chain(request)
    _api.collection_event('previsit_appointment_loaded', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     stage='appointment', count=len(inputs.get('lines') or []), source='ecw')

    try:
        source['saved_eligibility'] = await _api.asyncio.wait_for(
            _api.ecw_backend.fetch_saved_eligibility(source, inputs['service_date']), timeout=6)
    except _api.asyncio.TimeoutError:
        source['saved_eligibility'] = {'status': 'check_with_stedi', 'message': 'The saved eCW check took too long to load. Continue with Stedi verification and retry eCW later.'}

    codes = _api._previsit_codes(inputs)
    result: dict[str, _api.Any] = {"appointment_id": value.appointment_id, "service_date": inputs["service_date"],
        "payer": source.get("payer"), "plan": source.get("plan"), "visit_type": inputs.get("visit_type"),
        "reason": inputs.get("appointment_reason") or inputs.get("appointment_type") or inputs.get("reason"), "codes": codes, "benefits": None, "price_range": None,
        "suggestions": None, "issues": [], "checked_at": None,
        "ecw_saved_eligibility": {k: v for k, v in source['saved_eligibility'].items() if not k.startswith('_')}, "ecw_coverage_active": source.get('active_coverages', 0) > 0}
    coverage = _api._coverage_snapshot(inputs, source)

    async def incomplete_result(terminal_reason: str | None = None):
        if terminal_reason:
            for prior in prior_cases:
                if (prior.get('previsit_context', {}).get('source', {}).get('appointment_id') == value.appointment_id
                        and any(prior.get(key, {}).get('request_state') in {'pending', 'indeterminate'}
                                for key in ('previsit', 'previsit_discovery'))):
                    held = _api.deepcopy(prior)
                    held['previsit_discovery'] = {**held.get('previsit_discovery', {}),
                        'request_state': 'indeterminate', 'outcome': None, 'exhausted': False}
                    return _api._previsit_result(held, session['practice_id'])
        binding = _api._visit_context_token(inputs, source)
        case = await _api.run_in_threadpool(_api.store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'],
            workflow_slot='commercial-unresolved:' + binding, **_api._scope(session))
        result['check_id'] = case['id']
        result['benefits'] = _api._previsit_benefits({}, coverage, inputs['service_date'])
        await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, None, session, '')
        if terminal_reason:
            case['previsit_discovery'] = {'request_state': 'complete', 'outcome': 'not_available',
                'exhausted': True, 'reason_code': terminal_reason, 'stage': 'not_available',
                'steps': {'payer_checks': terminal_reason}, 'checked_at': _api._now()}
            if terminal_reason == 'stedi_unavailable' and _api.object_store.enabled():
                case['previsit_discovery']['identity_binding'] = _api._recovery_binding(inputs, source)
                await _api._recover_saved_policy(case, session, provider, allow_query=False)
        return _api._previsit_result(await _api.run_in_threadpool(_api._save, case, session, 'previsit-awaiting-coverage'), session['practice_id'])

    check_from = ((source.get('coverage_period') or {}).get('start') or '')[:10] or None
    workflows = list(session['intake'].get('workflows', {}).values())
    prior_cases = []
    if workflows:
        prior_cases = await _api.run_in_threadpool(_api.store.get_cases, workflows, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
        holds = [row['visit_review']['insurance_check_from'] for row in prior_cases
                 if row.get('previsit_context', {}).get('source', {}).get('appointment_id') == value.appointment_id
                 and row.get('visit_review', {}).get('insurance_check_from')]
        if holds:
            check_from = max([*holds, *([check_from] if check_from else [])])
    inputs['_selected_service_types'] = _api._reviewed_service_types(
        prior_cases, value.appointment_id, inputs['service_date'],
        _api._visit_context_token(inputs, source))
    timing = _api._eligibility_timing(inputs['service_date'], check_from)
    if timing:
        result['eligibility_timing'] = timing
        _api.collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='outside_check_window')
        return await incomplete_result()
    billing_note = _api.ecw_billing.collection_note(source.get('appointment_fields') or [])
    if source.get('self_pay_recorded') or (billing_note or {}).get('kind') == 'self_pay':
        _api.collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='self_pay_recorded')
        return await incomplete_result()

    try:
        card = await _api.asyncio.wait_for(_api._read_card_hints(session, source.get('_patient_id')), timeout=8)
    except _api.asyncio.TimeoutError:
        card = {'status': 'unavailable', 'fields': {}, 'count': 0}
    result['card_check'] = _api._compare_card_hints(card, inputs, source)
    coverage = _api._coverage_snapshot(inputs, source)
    result['payer'] = source.get('payer')
    _api.collection_event('card_precheck', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     stage='before_eligibility', outcome=result['card_check']['status'], count=card.get('count', 0))
    if result['card_check']['status'] in {'conflict', 'multiple_cards'}:
        conflicting = result['card_check']['status'] == 'conflict'
        kind = 'card_conflict' if conflicting else 'multiple_cards'
        result.update(eligibility_error=kind, eligibility_failure={
            'kind': kind, 'title': 'Card and eCW policy differ' if conflicting else 'Multiple card sets need review',
            'detail': ('The card member ID, group, or insurer differs from the eCW policy.' if conflicting
                       else 'More than one saved card set or more than two card pages were found.'),
            'action': ('ClinicForce will check the card with its unique supported payer route; review the result before changing eCW.' if conflicting and result['card_check'].get('alternative_supported') else
                       'Review the current card and correct the eCW policy before requesting eligibility.' if conflicting
                       else 'Confirm the current insurance card before requesting eligibility.'),
            'codes': [], 'attempted_at': _api._now()})
        result['issues'].append(result['eligibility_failure']['detail'])
        if conflicting and result['card_check'].get('alternative_supported'):
            return await _api._begin_coverage_discovery(result, session, inputs, source, provider, background_tasks,
                result['eligibility_failure']['detail'], refresh_benefits=value.refresh_benefits)
        return await incomplete_result()

    saved_result = await reuse_saved_eligibility(_api, result, session, inputs, source, coverage)
    if saved_result is not None:
        return saved_result

    rendering = source.get("rendering_provider") or {}
    billing = source.get("billing_provider") or {}
    rendering_npi = str(rendering.get("npi") or (inputs.get("rendering_provider") or {}).get("npi") or "")
    billing_npi = str(billing.get("npi") or "")
    organization_name = str(billing.get("name") or session.get("staff", {}).get("practice_name") or "")
    rendering_first, rendering_last = str(rendering.get("first_name") or ""), str(rendering.get("last_name") or "")
    provider = {"rendering_npi": rendering_npi, "rendering_first": rendering_first,
                "rendering_last": rendering_last, "billing_npi": billing_npi,
                "organization_name": organization_name}
    if not ((_api.re.fullmatch(r"\d{10}", rendering_npi) and rendering_first and rendering_last) or
            (_api.re.fullmatch(r"\d{10}", billing_npi) and organization_name)):
        result["issues"].append("eCW did not return a matched provider name and NPI needed for the payer check.")
        _api.collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='provider_identity_missing')
        return await incomplete_result('provider_identity_missing')
    api_key = _api.stedi_financial.credentials(session["practice_id"])
    if (_api.os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or
        _api.os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true" or
        not api_key or not _api.object_store.enabled()):
        result["issues"].append("Live Stedi eligibility or protected response storage is not configured.")
        _api.collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='stedi_unavailable')
        return await incomplete_result('stedi_unavailable')
    service_day = _api.date.fromisoformat(inputs["service_date"])
    usable_coverage = (coverage['active_coverages'] >= 1 and coverage['coverage_id'] and
                       coverage['relationship'] and all(coverage.get(key) for key in ('subscriber_first', 'subscriber_last')))
    if not usable_coverage or not source.get('payer'):
        detail = ('eCW did not return one usable primary policy with member and policyholder details for this visit.'
                  if not usable_coverage else 'eCW did not return an insurer name for the selected policy.')
        return await _api._begin_coverage_discovery(result, session, inputs, source, provider, background_tasks, detail,
                                               refresh_benefits=value.refresh_benefits)
    payer_routes = []
    try:
        payer = await _api.stedi_payers.search_payer(str(source["payer"]), source.get("payer_identifiers") or [], api_key, plan=source.get("plan"), candidates=payer_routes)
    except (_api.httpx.HTTPError, ValueError):
        return await _api._begin_coverage_discovery(result, session, inputs, source, provider, background_tasks,
            'The payer directory is unavailable. Continuing with other automatic coverage sources.',
            refresh_benefits=value.refresh_benefits)
    if not payer:
        return await _api._begin_coverage_discovery(result, session, inputs, source, provider, background_tasks,
            'The eCW payer name and ID did not identify one Stedi route. ClinicForce is checking the same policy through insurance discovery.',
            refresh_benefits=value.refresh_benefits, payer_routes=payer_routes)
    payer_id = payer["stediId"]
    requested_stc = _api._previsit_service_types(inputs)
    if payer_id == 'QDTRP':
        # BCBSTX accepts one service type per 270 inquiry.
        requested_stc = [code for code in requested_stc if code != '30'] or ['30']
    result['requested_service_types'] = requested_stc
    result["stedi_payer"] = {"name": payer.get("displayName"), "id": payer_id}
    try:
        inquiry_stc = requested_stc[0] if payer_id == 'QDTRP' else requested_stc
        body = _api.stedi_request_body({"stedi_payer_id": payer_id}, provider, coverage, inputs["service_date"], inquiry_stc, allow_demographic=not coverage.get('member_id'), place_of_service=inputs.get('place_of_service'))
        basic_body = _api.stedi_request_body({"stedi_payer_id": payer_id}, provider, coverage, inputs["service_date"], inquiry_stc,
                                        include_identity_hints=False, allow_demographic=not coverage.get('member_id'),
                                        place_of_service=inputs.get('place_of_service'))
    except _api.commercial.CommercialError as exc:
        result["issues"].append(str(exc))
        return await incomplete_result()
    if service_day == _api.date.today():
        body["encounter"].pop("dates", None)
        basic_body["encounter"].pop("dates", None)
    binding = _api.hashlib.sha256(_api.json.dumps({"coverage": coverage, "payer": payer_id,
        "provider": provider, "service_date": inputs["service_date"]}, sort_keys=True).encode()).hexdigest()
    slot = "commercial-previsit:" + _api.hashlib.sha256(f"{value.appointment_id}:{binding}".encode()).hexdigest()
    try:
        case = await _api.run_in_threadpool(_api.store.create_case, session["patient_id"], "", binding,
            {"source_mode": "commercial_previsit", "previsit": {"request_state": "not_requested"}},
            expected_intake_updated_at=session["intake"]["updated_at"], workflow_slot=slot, **_api._scope(session))
    except _api.store.StaffCaseConflict as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    result["check_id"] = case["id"]
    if (case.get('previsit_context') or {}).get('context_token') == _api._visit_context_token(inputs, source):
        result['suggestions'] = case['previsit_context']['result'].get('suggestions')
    elif inputs.get("reason"):
        result["suggestions"] = await _api.run_in_threadpool(
            _api.cpt_suggester.suggest_preliminary_cpt_codes,
            str(inputs["reason"])[:8000] + f"\nPatient visit status from eCW: {inputs.get('visit_type') or 'unknown'}.",
            source.get("recent_completed_appointments"))
    reused_result = await reuse_previsit_case(
        _api, case=case, result=result, inputs=inputs, source=source,
        session=session, coverage=coverage, provider=provider,
        background_tasks=background_tasks, requested_stc=requested_stc,
        payer_id=payer_id, refresh_benefits=value.refresh_benefits,
    )
    if reused_result is not None:
        return reused_result
    return await submit_previsit_eligibility(
        _api, payer=payer, provider=provider, api_key=api_key,
        session=session, case=case, result=result, coverage=coverage,
        inputs=inputs, source=source, background_tasks=background_tasks,
        requested_stc=requested_stc, body=body, basic_body=basic_body,
    )
