"""Commercial previsit routes workflow."""
from __future__ import annotations

import base64

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_reuse import (
    SAVED_RESPONSE_UNAVAILABLE, saved_benefits_for_review,
)

@_api.router.get('/cases/{case_id}/previsit')
async def saved_previsit(case_id: str, request: _api.Request) -> dict:
    """Read saved recovery without submitting or retrying a paid transaction.

    recovery.coverage_status is VERIFIED, REVIEW_REQUIRED, or NOT_FOUND.
    NOT_FOUND means a completed search returned no policies and no earlier
    active payer response was retained. A retained active response with an
    empty follow-up search is REVIEW_REQUIRED, never an uninsured conclusion.
    recovery.state separately reports pending/indeterminate transport state.
    Coverage verification does not imply that a price is available.
    evidence_resolution reports each field's source findings and precise
    remaining action after the shared estimator evaluates saved evidence.
    Saved reads project current network rules; an intact protected 2026 FEP Blue Standard response can refresh a Preferred $40 office copay and deductible waiver under current clinic policy without a payer call or write.
    benefits.reported_secondary_payer names one dated, patient-bound medical
    271 secondary payer; it does not verify Medicare entitlement or claim order.
    Fresh saved Cigna responses are reparsed without treating an exact
    "Attribution Status - Not applicable" STC 30 exclusion as inactive coverage.
    Card-printed tiered office copays remain reference-only until the visit's
    coverage, network, service and collection checks pass.
    A saved Cigna OAP specialist video visit can reapply the current member-bound
    payer office copay with zero office deductible and coinsurance without a new
    Stedi call; a readable matched card must agree with the payer copay.
    A matched UHC HealthSelect MA PPO group 13546 virtual visit can show a $0
    arrival amount from zero payer copay/coinsurance and the published no-deductible
    rule even when a saved line records other coverage or an unpriced conditional
    scenario; billed code, payer order, and final claim liability stay open.
    Flat Stedi 271 ZZ industry codes are interpreted as benefit places of
    service; appointment_context.place_of_service reports the planned setting.
    Older saved NMCT Cigna office checks with no copay are read-only reprojected
    from their hash-verified protected 271 before collection is recalculated.
    An NMCT same-day Cigna specialist office response can return one POS 11
    copay as a refundable advance with basis cigna_refundable_office_copay
    while the provider network remains unknown. The advance is separate from
    final patient liability and is reconciled and refunded after the claim.
    NMCT (practice_id primary) uses a current Clinical Library insurer contract
    for its HMO, PPO and Medicare Advantage products' in-network tier.
    A current patient-bound Cigna PPO office visit can use NMCT’s dated clinic attestation when Stedi, visit and clinic rendering NPIs agree. Its network_source is clinic_attested_cigna_ppo, not payer confirmation; saved reads recheck the hash-verified response. Other Cigna products without participation evidence remain network unknown; other payer paths can use scoped clinic rules.
    Old clinic-derived out-of-network prices are cleared for recalculation.
    Other practices still use dated group-network contracts; absent Blue
    agreements can select clinic_no_blue_contract. Network tier alone does not
    establish a collectible amount.
    If the protected raw payer response cannot be reopened, same-context saved
    benefit terms may remain with benefits.historical_only and a source error;
    they cannot authorize an insured collection. A changed policy context does
    not inherit them.

    Parseable candidates include protected member/subscriber/dependent details,
    nullable subscriber DOB, source and verification reason_codes. Only this
    authenticated patient/practice scope may read them. A completed legacy
    response may be reparsed and its diagnosis saved, without payer submission
    or automatic adoption. Historical snapshots are unchanged.

    insurance_diagnosis separates raw/retained/verified response counts from
    activePolicyCount, the distinct active medical-policy identity count.
    Copies of one policy with conflicting applicable evidence return
    same_policy_evidence_conflict for review, without asserting COB.
    The diagnosis also separates
    identityStatus, eligibilityStatus, pricingStatus, reasonCodes, states,
    pricingReasons and enrollment status. identityStatus is PAYER_RESPONSE_MISSING
    before a dated payer benefit response exists. reasonCodes distinguishes an unverified
    provider network from an in-network contract-rate gap and an out-of-network
    payer-allowance gap. A prior enrollment failure is retained for audit but
    is not a current blocker after a current active Stedi 271. An active,
    patient-bound 271 stays verified after inconclusive discovery; its recovery
    status is separate. Active payer benefits and a conflict between active
    coverage and a service-specific restriction stay in insurance review,
    including older automatic cash saves, unless staff explicitly chose cash.
    A differing eCW and payer member ID starts recovery on a fresh or
    recalculated check; this saved read never submits a payer transaction. Missing
    collection inputs return collection_guidance with a null
    amount, status action_required and basis missing_collection_inputs; the reason
    names the next staff action. A supported diagnostic per-day copay without an
    approved positive-advance policy returns diagnostic_copay_reference_cents
    for UI display while amount_cents stays null. An administrator-approved
    policy can give a $0 check-in advance
    with basis claim_after_service. It does not waive the later patient share or
    change coverage evidence. Saved reads apply the current practice policy.
    For one active, patient-bound 2026-27 BCBSTX TRS-ActiveCare HD office
    CPT 99203 visit with 30% coinsurance and no returned copay or remaining
    deductible/out-of-pocket balance, collection_guidance may return the
    scoped clinic fee reference less recorded payments as a refundable deposit
    with basis bcbstx_refundable_advance. Patient responsibility remains
    unresolved until claim adjudication.
    For one NMCT Texas Blue PPO POS 11 office E/M and/or selected 95908/95886
    diagnostic pair with matched clinic pricing, family remaining deductible/OOP,
    and 20% coinsurance on each CPT but no individual balances, saved reads may
    return an automatic deductible-first refundable quote with collection_rule.mode
    blue_ppo_family_deductible_quote. Payments are subtracted; no staff lookup is
    needed, and the processed claim controls final share.
    An unlinked payer-reported zero individual out-of-pocket balance sets
    collection_guidance.unconfirmed_zero_oop and withholds a positive advance.
    A linked, applicable zero balance can return basis out_of_pocket_limit
    and a $0 covered office-visit collection.
    A patient-bound office E/M response reporting Original Medicare Part B as
    secondary returns a $0 check-in advance under cms_msp_part_b_deferral,
    while preserving its copay and leaving final liability to the claims.
    A patient-bound 2026 UHC group 16615 saved plan can project the published
    G.E.H.A High PSHB Medicare Advantage zero office benefit on read, without
    a new eligibility call or a change to the historical saved snapshot. For
    one covered in-network office E/M line with matching zero payer terms and
    no conflicting evidence, collection_guidance uses basis
    published_geha_pshb_zero_office and amount_cents 0; the missing 271
    deductible/out-of-pocket rows remain missing member-specific evidence.
    temporary_rate_reference identifies a dated CPT planning source: scoped
    Blue Cross Clinical Library prices for eligible cross-payer routes,
    including the exact Choice POS II two-code diagnostic scenario, or draft
    Aetna MEPPO portal values for the exact Signature PPO diagnostic scenario.
    A Blue Cross reference never becomes an Aetna allowance or collect-now amount.
    The NMCT Cigna PPO single-CPT 99203 refundable office quote may equal the matched clinic fee less prior payments when same-plan deductible and out-of-pocket balances exceed the fee and the 20% office benefit has no copay; it is not a Cigna allowance or final liability.
    For a Cigna office E/M visit, temporary_rate_reference.alternatives lists matching BCBSTX fee rows
    for available, unselected levels in the same E/M family. These are separate
    planning scenarios and are excluded from the selected visit total.
    When Cigna explicitly reports family-only medical accumulators with an
    unambiguous general coinsurance term, cigna_office_fee_scenarios applies
    deductible then coinsurance to each fee reference and caps the conditional
    share at the remaining family out-of-pocket balance. It does not alter
    collection_guidance or authorize charging without Cigna's estimate.
    For an active patient-bound UHC D-SNP office visit with a $0 copay, $0
    remaining individual deductible and a payer-reported coinsurance rate,
    fee_proxy_estimate may show one new-patient E/M planning scenario. A
    selected CPT 99202-99205 uses its scoped BCBSTX fee or the dated published
    BCBSTX fee; without a selected CPT, 99203 is labeled an unselected pricing
    scenario. The proxy applies coinsurance and the reported individual OOP
    remaining balance. It is a pre-Medicaid planning share, not the UHC
    allowance or an instruction to collect; the table holds check-in at $0
    while member-specific dual cost-sharing protection is unresolved.
    A matched in-network UHC Choice Plus hand-EMG scenario may propose editable
    CPT 95908 and 95886 and expose a provisional BCBSTX fee proxy. It applies
    the returned individual deductible before coinsurance, then the reported
    remaining out-of-pocket cap; the UHC allowance remains unknown. Referral
    and authorization decisions are shown per CPT with current official sources.
    The exact two-code Aetna diagnostic case also exposes a $100 planning share
    from a likely published $50-per-test benefit, capped by the draft fee
    reference and reported remaining out-of-pocket balance. This is not an
    authorized collection amount; saved scenarios are recalculated on read.
    botox_preview, when present, shows the sole priced BCBSTX office-injection
    candidate from a scoped clinic fee and the returned injection term, even
    when other suggested candidates remain unpriced.
    It is a conditional planning amount, not a selected CPT or collection order.
    For Cigna, separate exact-CPT
    Stedi inquiries may supply authorization and financial terms. When every
    selected line has compatible terms, fee_proxy_estimate shows a provisional
    BCBS-priced share; an absent copay is treated as $0 in that calculation.
    For a single in-network Cigna OAP office E/M CPT, an explicit family-only
    payer statement permits exact-CPT family accumulator terms in that proxy.
    Diagnosis-qualified exact-CPT terms are excluded from unrelated visits;
    unqualified plan-wide family accumulators and matching general coinsurance
    can instead show an explicitly assumed office-deductible planning scenario.
    reported_options.diagnosis_scoped marks payer rows with diagnosis limits.
    Without the governed clinic Cigna quote, the amount to collect is $0 at arrival when no fixed copay is reported;
    final liability remains open until a Cigna estimate or processed claim. The clinic Cigna quote reads active manual Cigna rates before matching BCBS Texas fees for other clinician-selected CPTs, applies the individual deductible before coinsurance and the out-of-pocket cap, and subtracts verified payments. One matched unassigned general STC 30 deductible/out-of-pocket pair can support a disclosed provisional balance; a confirmed dizziness 99203 plus 95992 plan does not use preventive-only office deductible waivers. The clinic-directed refundable amount is not a Cigna Cost of Care Estimator result; missing codes or benefits withhold a full collection quote.
    Saved reads can re-read the protected exact-CPT 271 for this family-only
    term when the stored patient, date, network and CPT scope still match.
    For an in-network Cigna Open Access Plus office visit with selected EMG/NCS
    codes, a separate matched STC 73 inquiry can support one diagnostic
    specialist copay plus diagnostic 0% coinsurance on matching BCBS reference
    fees. Without those terms, the general 0% office-copay scenario remains.
    The UI displays the formula and marks collection pending until Cigna's
    Explanation of Estimate is available; fee_proxy_estimate is not a Cigna allowance.
    The UHC group-plan proxy keeps its separate
    administrator-approved opt-in and member billing-protection clearance.
    For a verified 2026 TRS-Care LPPO group 15725 visit, selecting a separate
    established follow-up CPT 99213 may add the published $10 specialist copay
    to the diagnostic fee proxy when the member deductible is exhausted.
    benefits.reported_options[].places_of_service retains the payer's place-of-service
    codes for scoped benefit display; a reported office copay is not a collection decision.
    reference_benefits and reference_subscriber, when present, come from one
    administrator-attached same-visit Stedi check. The subscriber is displayed
    with a masked member ID in the UI and its benefit rows take display
    precedence over mismatched eCW values. This does not change collection_guidance.
    At NMCT, a payer reference alone does not establish the clinic contract tier;
    the payer-returned date and appointment-date coverage qualification remain visible.
    availity_visit_reference, when present, is a dated administrator-uploaded
    Availity Essentials eligibility PDF matched to patient, birth date,
    requesting provider NPI and visit date. It supplies a separate specialist
    office planning table. It does not replace eCW/Stedi eligibility, establish
    QMB status, or change collection_guidance.
    ecw_eligibility_reference, when present, is an administrator-uploaded eCW
    eligibility printout for the same saved visit. Its patient header and
    report date are checked, and the administrator confirms member, birth date,
    payer and provider. The office individual and family out-of-pocket figures
    can fill missing display rows as dated EHR reference evidence. They never
    change the Stedi calculation or collection_guidance.
    benefits.oop_annual_cents is a unique plan-wide individual maximum when
    the payer returned one; it is distinct from oop_remaining_cents.
    Conflicting individual annual or remaining OOP rows leave that amount null
    and retain their payer paths in the corresponding *_conflict_ids field.
    combined_emg_plan, when present, is a noncollectible planning calculation
    for a separately planned established-patient office visit plus priced
    EMG/NCS lines. It adds one sourced office copay to diagnostic coinsurance
    while disclosing missing remaining deductible and out-of-pocket balances.
    coordination, when present, is a safe summary of a separate Stedi COB
    check, including fixed field names missing at preflight and any eCW birth
    date source used when the 271 omitted it. Payer names and roles remain
    unassigned to discovered policies until
    exact evidence supports the mapping. Saved GET never submits COB.
    payment_explanation is recalculated from the saved result and remains one
    plain sentence with the network/pricing basis and any unresolved CPTs. It
    also returns structured plan, network decision, contract source, selected
    services, allowed-rate provenance, arithmetic and collection-policy fields.
    Dated Aetna Choice POS II office CPT 99213 may show a refundable MPPO fee-reference deposit, never a calculated patient share.
    encounter_summary retains per-CPT rounding and withholds a saved positive
    advance if a CPT's stated coinsurance rate and base contradict its amount.
    Saved insured amounts are withheld if the linked clinic contract, scoped
    plan/provider configuration, or active selected CPT rate changed; staff
    must rerun against current configuration.
    published_requirements_version identifies the current published payer rules
    and stored CPT requirement rows. Saved reads re-evaluate referral and
    authorization when these, Clinical Library sources, or network evidence change.
    selected_ids includes source-backed provisional CPTs as well as reviewed
    CPTs; service_selection_status distinguishes preview from approval. The
    selected IDs and priced code list refer to the same service scenario.
    emg_option.calculation_version identifies the full patient-cost method.
    Older or newly invalid scenarios retain the testing request but return null
    numeric totals until staff recalculates with current visit evidence.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    recovery = case.get('previsit_discovery') or {}
    if (recovery.get('request_state') == 'complete' and recovery.get('outcome') in {'review_needed', 'no_match'}
            and recovery.get('raw_object_key') and recovery.get('candidate_version') != 5):
        await _api._reconcile_saved_discovery(case, session)
    return _api._previsit_result(case, session['practice_id'])


@_api.router.get('/cases/{case_id}/payer-response')
async def saved_payer_response(case_id: str, request: _api.Request):
    """Return protected payer evidence, including linked service replies when present."""
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if (not case or case.get('source_mode') != 'commercial_previsit'
            or case.get('previsit', {}).get('request_state') not in {'complete', 'partial', 'indeterminate', 'rejected'}
            or (case.get('previsit', {}).get('request_state') == 'indeterminate'
                and not case.get('previsit', {}).get('requested_service_types'))
            or not case.get('previsit_raw_object_key')):
        raise _api.HTTPException(404, 'No completed payer response is available for this visit.')
    try:
        payload = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, case['previsit_raw_object_key']))
    except Exception as exc:
        raise _api.HTTPException(502, 'The saved payer response could not be loaded.') from exc
    return _api._response({'source': 'Stedi', 'response': payload})


@_api.router.get('/cases/{case_id}/historical-replay-bundle')
async def historical_replay_bundle(case_id: str, request: _api.Request) -> dict:
    """Export a read-only protected replay bundle for a scoped administrator.

    This route makes no payer request and never updates the saved estimate. The
    response contains PHI and raw payer evidence, including linked service
    inquiries when present; keep it in protected local
    storage and do not place it in repository notes or test fixtures. Current
    Library records are returned with dates for the replay's historical cutoff;
    missing historical versions cannot be reconstructed by this export. The
    replay reports when a saved clinic agreement cannot be reconstructed, so
    its unknown network tier is not mistaken for the saved network decision.
    """
    session = await _api._session(request)
    if (session.get('staff') or {}).get('role') != 'admin':
        raise _api.HTTPException(403, 'An administrator is required to export protected replay evidence.')
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'],
        session['share_manifest_id'], **_api._scope(session))
    if (not case or case.get('source_mode') != 'commercial_previsit'
            or not case.get('previsit_raw_object_key') or not case.get('previsit_context')):
        raise _api.HTTPException(404, 'No linked saved previsit response is available.')
    try:
        raw = await _api.run_in_threadpool(_api.object_store.get_object_bytes,
                                           case['previsit_raw_object_key'])
        _api.json.loads(raw)
    except Exception as exc:
        raise _api.HTTPException(502, 'The protected saved payer response could not be loaded.') from exc
    digest = _api.hashlib.sha256(raw).hexdigest()
    if case.get('previsit_raw_sha256') and case['previsit_raw_sha256'] != digest:
        raise _api.HTTPException(409, 'The saved payer response bytes differ from the recorded digest.')
    context = case['previsit_context']
    payer_id = str(context.get('payer_id') or '')
    connections = await _api.run_in_threadpool(_api.commercial_store.list_connections,
                                                session['practice_id'])
    matches = [row for row in connections if row.get('stedi_payer_id') == payer_id]
    payer = matches[0] if len(matches) == 1 else {}
    payer_name = str(payer.get('display_name') or (context.get('result') or {}).get('payer') or '')
    metadata = await _api.run_in_threadpool(_api.staff_store.list_library, session['practice_id'])
    documents = []
    for row in metadata:
        same_payer = (_api.insurance_identity.normalized(row.get('payer')) ==
                      _api.insurance_identity.normalized(payer_name))
        same_uhc = (_api.commercial.is_united_healthcare_payer({'payer': row.get('payer')})
                    and _api.commercial.is_united_healthcare_payer({'payer': payer_name}))
        if row.get('kind') == 'contract' and (same_payer or same_uhc):
            document = await _api.run_in_threadpool(_api.staff_store.get_library,
                                                     row['id'], session['practice_id'])
            if document:
                documents.append(document)
    saved_result = context.get('result') or {}
    selected = set((case.get('visit_review') or {}).get('selected_ids') or
                   saved_result.get('selected_ids') or [])
    selected_lines = [row for row in saved_result.get('codes') or [] if row.get('id') in selected]
    documented = bool(selected) and len(selected_lines) == len(selected) and all(
        row.get('code') and type(row.get('units')) is int for row in selected_lines)
    return {'case_reference': case_id, 'intake_id': case.get('intake_id') or session.get('intake_id'),
            'case': case, 'raw_reference': case['previsit_raw_object_key'],
            'raw_response_b64': base64.b64encode(raw).decode(), 'retrieved_raw_sha256': digest,
            'source_digest_recorded': bool(case.get('previsit_raw_sha256')),
            'documents': documents,
            'payer_route': payer or {'display_name': payer_name, 'stedi_payer_id': payer_id, 'rules': {}},
            'payer_route_unique': len(matches) == 1,
            'practice_id': session['practice_id'],
            'service_date': (context.get('inputs') or {}).get('service_date'),
            'selection': {'origin': 'documented', 'lines': selected_lines} if documented else None,
            'selection_status': 'documented' if documented else 'requires_historical_cpt_evidence'}


@_api.router.post('/cases/{case_id}/payer-route')
async def select_payer_route(case_id: str, value: _api.PayerRouteInput, request: _api.Request,
                            background_tasks: _api.BackgroundTasks) -> dict:
    """Select a saved directory suggestion and run eligibility with known identity.

    Selection is not coverage verification. Stale, arbitrary and already-pending
    selections are rejected before any payer transaction. No eCW data is changed.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    context = case['previsit_context']
    if case.get('revision', 0) != value.revision or context['context_token'] != value.context_token:
        raise _api.HTTPException(409, 'This estimate changed. Reopen it before selecting a payer route.')
    recovery = case.get('previsit_discovery') or {}
    if _api.stedi_discovery.public_status(recovery)['state'] not in {'complete', 'rejected'} or recovery.get('outcome') == 'recovered':
        raise _api.HTTPException(409, 'Wait for the existing recovery request before selecting a payer route.')
    payer = next((row for row in recovery.get('payer_routes', []) if row['stediId'] == value.route_id), None)
    if not payer:
        raise _api.HTTPException(422, 'Select one of the payer routes returned for this policy.')
    inputs, source = context['inputs'], context['source']
    rendering, billing = source.get('rendering_provider') or {}, source.get('billing_provider') or {}
    provider = {'rendering_npi': str(rendering.get('npi') or (inputs.get('rendering_provider') or {}).get('npi') or ''),
        'rendering_first': str(rendering.get('first_name') or ''), 'rendering_last': str(rendering.get('last_name') or ''),
        'billing_npi': str(billing.get('npi') or ''),
        'organization_name': str(billing.get('name') or session.get('staff', {}).get('practice_name') or '')}
    recovery.update(request_state='pending', outcome='review_needed', stage='eligibility',
        selected_route_id=value.route_id, selection_error=None, started_at=_api._now())
    case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-payer-route-selected')
    _api._recovery_event(case, session, 'payer_routing', 'selected', reason=payer.get('match_basis', 'name_or_alias'))
    background_tasks.add_task(_api._recheck_discovery_candidate, case, session, provider, selected_route=payer)
    return _api._previsit_result(case, session['practice_id'])


@_api.router.post('/cases/{case_id}/coverage-candidate')
async def select_coverage_candidate(case_id: str, value: _api.CoverageCandidateInput, request: _api.Request,
                                    background_tasks: _api.BackgroundTasks) -> dict:
    """Select a returned policy for a follow-up payer check.

    The saved recovery result distinguishes payer error, returned identity mismatch,
    missing service-date medical coverage and an indeterminate submitted check.
    A mismatched response stays protected as evidence without assigning its
    member balances to this patient.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    if case.get('revision', 0) != value.revision or case['previsit_context'].get('context_token') != value.context_token:
        raise _api.HTTPException(409, 'This estimate changed in another session. Reopen it before selecting coverage.')
    discovery = case.get('previsit_discovery') or {}
    if discovery.get('request_state') != 'complete' or discovery.get('outcome') != 'review_needed':
        raise _api.HTTPException(409, 'There is no coverage candidate ready to select.')
    if not any(row.get('id') == value.candidate_id for row in discovery.get('candidates', [])):
        raise _api.HTTPException(422, 'Select a coverage option returned for this patient.')
    source, inputs = case['previsit_context']['source'], case['previsit_context']['inputs']
    # Staff may reconcile a different/new policy. Its patient identity and active
    # service-date coverage are still revalidated against the payer response.
    rendering, billing = source.get('rendering_provider') or {}, source.get('billing_provider') or {}
    provider = {'rendering_npi': str(rendering.get('npi') or (inputs.get('rendering_provider') or {}).get('npi') or ''),
                'rendering_first': str(rendering.get('first_name') or ''), 'rendering_last': str(rendering.get('last_name') or ''),
                'billing_npi': str(billing.get('npi') or ''),
                'organization_name': str(billing.get('name') or session.get('staff', {}).get('practice_name') or '')}
    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=value.candidate_id,
                     selected_candidate_source_ids=[value.candidate_id],
                     selection_error=None, checked_at=_api._now())
    case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-candidate-selected')
    background_tasks.add_task(_api._recheck_discovery_candidate, case, session, provider)
    return _api._previsit_result(case, session['practice_id'])


async def _begin_coverage_discovery(result: dict, session: dict, inputs: dict, source: dict, provider: dict,
                                    background_tasks: _api.BackgroundTasks | None, detail: str, *, refresh_benefits: bool = False,
                                    payer_routes: list[dict] | None = None) -> dict:
    """Save a no-policy appointment as a recoverable Stedi discovery case."""
    coverage = _api._coverage_snapshot(inputs, source)
    binding = _api.hashlib.sha256(_api.json.dumps({'patient': inputs.get('patient'), 'appointment': source.get('appointment_id'),
        'provider': provider, 'service_date': inputs.get('service_date')}, sort_keys=True).encode()).hexdigest()
    card_conflict = bool((result.get('card_check') or {}).get('alternative_supported') and
                         (result.get('card_check') or {}).get('status') == 'conflict')
    slot = 'commercial-previsit-recovery:' + _api.hashlib.sha256(
        f"{source.get('appointment_id')}:{binding}:{(result.get('card_check') or {}).get('alternative_fingerprint') if card_conflict else ''}".encode()).hexdigest()
    try:
        case = await _api.run_in_threadpool(_api.store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'], workflow_slot=slot, **_api._scope(session))
    except _api.store.StaffCaseConflict as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    result['check_id'] = case['id']
    if (case.get('previsit_context') or {}).get('context_token') == _api._visit_context_token(inputs, source):
        result['suggestions'] = case['previsit_context']['result'].get('suggestions')
    elif inputs.get('reason'):
        result['suggestions'] = await _api.run_in_threadpool(_api.cpt_suggester.suggest_preliminary_cpt_codes,
            str(inputs['reason'])[:8000] + f"\nPatient visit status from eCW: {inputs.get('visit_type') or 'unknown'}.",
            source.get('recent_completed_appointments'))
    saved = case.get('previsit') or {}
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        return await _api._start_previsit_recovery(case, session, provider, background_tasks)
    if not refresh_benefits and saved.get('request_state') == 'complete' and case.get('previsit_context'):
        try:
            fresh = _api.datetime.fromisoformat(saved['checked_at']) + _api.timedelta(hours=24) > _api.datetime.now(_api.timezone.utc)
        except (KeyError, TypeError, ValueError):
            fresh = False
        # Reopening completed recovery never submits a payer request. Reprice
        # current evidence; stale/unreadable evidence cannot authorize insurance.
        old_context = case['previsit_context']
        raw_key = case.get('previsit_raw_object_key')
        payload = None
        if fresh and raw_key:
            try:
                payload = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, raw_key))
            except Exception:
                payload = None
        result = _api.deepcopy(old_context['result'])
        same_context = old_context.get('context_token') == _api._visit_context_token(inputs, source)
        if payload:
            result['benefits'] = _api._case_benefits(payload, inputs, source, case)
        else:
            result['benefits'] = (saved_benefits_for_review(old_context['result'].get('benefits'))
                                  if same_context else None) or _api._previsit_benefits({}, coverage, inputs['service_date'])
        if not payload:
            result.setdefault('issues', []).append(SAVED_RESPONSE_UNAVAILABLE if same_context else
                'The saved payer response belongs to earlier visit or policy details. Rerun the payer check for this visit.')
        pricing_source = case.get('previsit_discovery', {}).get('pricing_source')
        await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, payload, session,
                                old_context.get('payer_id') or '', pricing_source=pricing_source)
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'previsit-library-recalculated'))
        _api._recovery_event(case, session, 'discovery_cache', 'reused', reason='saved_result')
        if payload and (result['benefits'].get('has_secondary') or
                        (case.get('previsit_discovery') or {}).get('reason_code') == 'multiple_active_policies'):
            await _api._check_previsit_cob(case, session, provider)
        return _api._previsit_result(case, session['practice_id'])
    discovery_ready = bool(_api.stedi_discovery.request_body(inputs, provider))
    routing_failure = bool(source.get('payer') and coverage.get('member_id'))
    if routing_failure and not discovery_ready:
        missing = _api.stedi_discovery.missing_request_fields(inputs, provider)
        detail = ('The eCW payer name and ID did not identify one Stedi route. Insurance discovery could not start '
                  'because the request lacks ' + ', '.join(missing) + '.')
    if not card_conflict:
        result.update(eligibility_error='payer_routing_unresolved' if routing_failure else 'coverage_identity_missing', eligibility_failure={
            'kind': 'payer_routing_unresolved' if routing_failure else 'coverage_identity_missing',
            'title': 'Insurance payer needs recovery' if routing_failure else 'eCW coverage needs recovery', 'detail': detail,
            'action': ('ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'
                       if routing_failure and discovery_ready else
                       'Correct the listed patient or provider fields in eCW or clinic provider setup, then rerun insurance discovery.' if routing_failure else
                       'ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'),
            'codes': [], 'attempted_at': _api._now()})
    result['benefits'] = _api._previsit_benefits({}, coverage, inputs['service_date'])
    await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, None, session, '')
    recovery = case.get('previsit_discovery') or {}
    try:
        reusable = (not refresh_benefits and recovery.get('request_state') == 'complete'
                    and recovery.get('identity_binding') == _api._recovery_binding(inputs, source)
                    and _api.datetime.fromisoformat(recovery['checked_at']) + _api.timedelta(hours=24) > _api.datetime.now(_api.timezone.utc))
    except (KeyError, TypeError, ValueError):
        reusable = False
    if reusable:
        _api._recovery_event(case, session, 'discovery_cache', 'reused', reason='completed_search')
        case = await _api.run_in_threadpool(_api._save, case, session, 'previsit-discovery-reused')
        return await _api._start_previsit_recovery(case, session, provider, background_tasks)
    case['previsit'] = {'request_state': 'rejected'}
    case['previsit_discovery'] = {'recovery_requested': discovery_ready,
                                  'request_state': 'not_requested' if discovery_ready else 'complete',
                                  'identity_binding': _api._recovery_binding(inputs, source),
                                  'stage': 'not_started' if discovery_ready else 'not_available',
                                  'reason_code': 'payer_route_unresolved' if discovery_ready else 'discovery_input_missing'}
    if card_conflict:
        case['previsit_discovery'].update(card_conflict=True,
            card_fingerprint=result['card_check']['alternative_fingerprint'],
            reason_code='card_alternative_check')
    if payer_routes:
        case['previsit_discovery'].update(payer_routes=payer_routes, route_source='ecw')
    if not discovery_ready:
        case['previsit_discovery'].update(outcome='not_available', exhausted=True, checked_at=_api._now(), selection_error=detail)
    case = await _api.run_in_threadpool(_api._save, case, session, 'previsit-coverage-recovery-requested')
    return await _api._start_previsit_recovery(case, session, provider, background_tasks)
