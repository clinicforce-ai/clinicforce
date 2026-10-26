import { useState } from 'react'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'

const candidateReasonText: Record<string, string> = {
  MEMBER_ID_MISSING: 'Subscriber member ID missing',
  SUBSCRIBER_NAME_MISSING: 'Subscriber name missing',
  SUBSCRIBER_DOB_MISSING: 'Subscriber birth date missing',
  PATIENT_NAME_MISSING: 'Patient name missing',
  PATIENT_NAME_MISMATCH: 'Patient name differs from eCW',
  PATIENT_DOB_MISSING: 'Patient birth date missing',
  PATIENT_DOB_MISMATCH: 'Patient birth date differs from eCW',
  PATIENT_ADDRESS_MISMATCH: 'Patient address differs from eCW',
  PAYER_REJECTED: 'Payer rejected the member inquiry',
  PAYER_ROUTE_MISSING: 'Exact payer route missing',
  RELATIONSHIP_CONFLICT: 'Subscriber relationship differs from eCW',
  MULTIPLE_COVERAGES: 'Multiple policies need payer order',
  ACTIVE_MEDICAL_COVERAGE_NOT_VERIFIED: 'No active medical benefit for this visit date',
  COVERAGE_DATE_NOT_VERIFIED: 'No service-date coverage evidence',
  COVERAGE_EVIDENCE_INVALID: 'Payer coverage fields could not be read',
  ADDITIONAL_IDENTITY_EVIDENCE_REQUIRED: 'No matching eCW policy or address link',
  MALFORMED_RESPONSE: 'Payer response fields could not be read',
}
const candidateReason = (code: string) =>
  candidateReasonText[code] || code.replaceAll('_', ' ').toLowerCase()

export default function CoverageRecovery({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: NonNullable<CommercialEstimateModel['result']>
}) {
  const [priorAddress, setPriorAddress] = useState({
    address1: '',
    city: '',
    state: '',
    postalCode: '',
  })
  const [confirmedCandidateId, setConfirmedCandidateId] = useState('')
  const changePriorAddress = (field: keyof typeof priorAddress, value: string) => {
    setPriorAddress((current) => ({ ...current, [field]: value }))
    setConfirmedCandidateId('')
  }
  const {
    readOnly,
    candidateSuggestion,
    setCandidateSuggestion,
    payerSuggestion,
    setPayerSuggestion,
    busy,
    recovering,
    selectCoverage,
    failed,
    failure,
    guidance,
    unavailableReason,
  } = model
  const verifiedCoverage = result.benefits?.active === true && patientBenefitsBound(result.identity)
  const payerRoutes = result.recovery?.payer_routes || []
  const showPayerRoutes =
    payerRoutes.length > 0 && !['pending', 'indeterminate'].includes(result.recovery?.state || '')
  const shownGroups = new Set<string>()
  const policySuggestions = (result.recovery?.candidates || []).filter((candidate) => {
    const group = candidate.policy_group_id
    if (
      !group ||
      candidate.verification_status !== 'VERIFIED' ||
      result.recovery?.reason_code === 'same_policy_evidence_conflict'
    )
      return true
    if (shownGroups.has(group)) return false
    shownGroups.add(group)
    return true
  })
  return (
    <>
      {showPayerRoutes && (
        <section className="visit-recovery">
          <h3>Payer suggestions</h3>
          <p>
            Optional suggestions from the payer directory. A selected route still needs a successful
            eligibility response.
          </p>
          {payerRoutes.length >= 2 && (
            <label>
              Payer suggestions
              <select
                value={payerRoutes.some((row) => row.id === payerSuggestion) ? payerSuggestion : ''}
                onChange={(event) => setPayerSuggestion(event.target.value)}
              >
                <option value="">View a payer suggestion</option>
                {payerRoutes.map((route) => (
                  <option key={route.id} value={route.id}>
                    {route.name} · {route.payer_id || route.id}
                  </option>
                ))}
              </select>
            </label>
          )}
          {payerRoutes
            .filter((route) => payerRoutes.length < 2 || route.id === payerSuggestion)
            .map((route) => (
              <article key={route.id}>
                <strong>{route.name}</strong>
                <p>
                  Payer ID: {route.payer_id || route.id} · Stedi route: {route.id}
                </p>
                <p>
                  {route.match_basis === 'directory_suggestion'
                    ? 'Suggested name match — review against the card'
                    : 'Matches the recorded payer name or ID'}
                  {route.eligibility === 'ENROLLMENT_REQUIRED' &&
                  (result.benefits?.active !== true || result.benefits.historical_only)
                    ? ' · Provider enrollment required'
                    : ''}
                </p>
                {!readOnly && (
                  <button
                    disabled={!!busy || recovering || result.recovery?.state === 'indeterminate'}
                    onClick={() => void selectCoverage(route.id, true)}
                  >
                    Check eligibility with this payer
                  </button>
                )}
              </article>
            ))}
        </section>
      )}
      {failed && (
        <section className="visit-recovery">
          <h3>
            {recovering
              ? 'Finding coverage…'
              : result.cash_fallback
                ? 'Automatic insurance checks finished'
                : failure?.title || 'Coverage check incomplete'}
          </h3>
          <p>{result.recovery?.message || guidance?.reason || unavailableReason}</p>
          {!!result.coordination?.payer_roles?.length && (
            <p>
              Stedi COB reported{' '}
              {result.coordination.payer_roles
                .map((row) => `${row.role}: ${row.payer_name}`)
                .join(', ')}
              . Match these names to the patient’s policies before using payer order.
            </p>
          )}
          {result.recovery && (result.recovery.reason_code || result.recovery.reference) && (
            <small className="visit-muted">
              {result.recovery.reason_code?.replaceAll('_', ' ')}
              {result.recovery.reason_code && result.recovery.reference ? ' · ' : ''}
              {result.recovery.reference ? `Stedi discovery ${result.recovery.reference}` : ''}
            </small>
          )}
        </section>
      )}
      {!!policySuggestions.length && (
        <section className="visit-recovery" aria-label="Returned insurance candidates">
          <h3>Policy search results</h3>
          <p>
            {result.benefits?.active === true
              ? `${result.payer || 'The current payer'} returned active benefits${verifiedCoverage ? ' matched to this patient' : '; the patient match needs review'}. The entries below are separate saved policy search results.`
              : 'These are saved policy search results, including inactive policies. They do not establish current benefits.'}{' '}
            Selecting a suggestion is optional.
          </p>
          {policySuggestions.length >= 2 && (
            <label>
              Policy search results
              <select
                value={
                  policySuggestions.some((row) => row.id === candidateSuggestion)
                    ? candidateSuggestion
                    : ''
                }
                onChange={(event) => setCandidateSuggestion(event.target.value)}
              >
                <option value="">View a policy suggestion</option>
                {policySuggestions
                  .filter((candidate) => candidate.id)
                  .map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {candidate.payer || 'Unknown payer'} ·{' '}
                      {candidate.member_id || 'Member ID not returned'} ·{' '}
                      {candidate.coverage_evidence?.status?.toLowerCase() || 'Status unknown'}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {policySuggestions
            .filter(
              (candidate) =>
                policySuggestions.length < 2 ||
                !candidate.id ||
                candidate.id === candidateSuggestion,
            )
            .map((candidate, index) => {
              const needsPriorAddress =
                candidate.reason_codes?.includes('PATIENT_ADDRESS_MISMATCH') || false
              const priorAddressReady = Boolean(
                priorAddress.address1.trim() &&
                priorAddress.city.trim() &&
                /^[A-Z]{2}$/.test(priorAddress.state) &&
                /^\d{5}(?:\d{4})?$/.test(priorAddress.postalCode) &&
                confirmedCandidateId === candidate.id,
              )
              const coverageState = candidate.coverage_evidence?.status
              const statusHeadline =
                !!coverageState &&
                ['INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'].includes(coverageState)
              const firstReasonInHeadline =
                !statusHeadline && candidate.verification_status !== 'VERIFIED'
              return (
                <article key={candidate.id || `saved-candidate-${index}`}>
                  <strong>
                    {candidate.payer || 'Payer not returned'} ·{' '}
                    {statusHeadline
                      ? coverageState?.toLowerCase()
                      : candidate.verification_status === 'VERIFIED'
                        ? 'Identity and coverage evidence matched'
                        : candidate.reason_codes?.[0]
                          ? candidateReason(candidate.reason_codes[0])
                          : 'Returned policy has no patient match'}
                  </strong>
                  <p>
                    {candidate.plans?.length
                      ? candidate.plans.join(' · ')
                      : 'Plan name not returned'}
                  </p>
                  {(candidate.policy_response_count || 0) > 1 && (
                    <p>
                      {candidate.policy_response_count} saved payer responses describe this policy.
                    </p>
                  )}
                  <dl>
                    <div>
                      <dt>Candidate member ID</dt>
                      <dd>{candidate.member_id || 'Not returned'}</dd>
                    </div>
                    <div>
                      <dt>Group</dt>
                      <dd>{candidate.group_number || 'Not returned'}</dd>
                    </div>
                    {candidate.coverage_evidence && (
                      <div>
                        <dt>Coverage dates</dt>
                        <dd>
                          {candidate.coverage_evidence.start || 'Start not returned'} –{' '}
                          {candidate.coverage_evidence.end || 'End not returned'}
                        </dd>
                      </div>
                    )}
                    <div>
                      <dt>Policyholder</dt>
                      <dd>
                        {[candidate.subscriber?.firstName, candidate.subscriber?.lastName]
                          .filter(Boolean)
                          .join(' ') || 'Not returned'}
                      </dd>
                    </div>
                    <div>
                      <dt>Policyholder DOB</dt>
                      <dd>{candidate.subscriber?.dateOfBirth || 'Unknown'}</dd>
                    </div>
                    {candidate.dependent && (
                      <div>
                        <dt>Dependent</dt>
                        <dd>
                          {[candidate.dependent.firstName, candidate.dependent.lastName]
                            .filter(Boolean)
                            .join(' ')}{' '}
                          · DOB {candidate.dependent.dateOfBirth || 'Unknown'}
                        </dd>
                      </div>
                    )}
                    <div>
                      <dt>Source</dt>
                      <dd>
                        {candidate.source?.replaceAll('_', ' ') || 'Saved insurance discovery'}
                      </dd>
                    </div>
                  </dl>
                  {!!candidate.reason_codes?.length &&
                    candidate.reason_codes.length > (firstReasonInHeadline ? 1 : 0) && (
                      <ul>
                        {candidate.reason_codes
                          .slice(firstReasonInHeadline ? 1 : 0)
                          .map((reason) => (
                            <li key={reason}>{candidateReason(reason)}</li>
                          ))}
                      </ul>
                    )}
                  {!readOnly && !candidate.id && (
                    <p>
                      Saved discovery has no candidate ID. Rerun policy discovery before checking
                      this policy.
                    </p>
                  )}
                  {!readOnly && needsPriorAddress && (
                    <fieldset className="prior-address-confirmation">
                      <legend>Prior address in eCW history</legend>
                      <p>
                        Enter the prior address shown in this patient's eCW chart. ClinicForce will
                        compare it with the saved payer response.
                      </p>
                      <label>
                        Street address
                        <input
                          value={priorAddress.address1}
                          onChange={(event) => changePriorAddress('address1', event.target.value)}
                          autoComplete="off"
                        />
                      </label>
                      <label>
                        City
                        <input
                          value={priorAddress.city}
                          onChange={(event) => changePriorAddress('city', event.target.value)}
                          autoComplete="off"
                        />
                      </label>
                      <label>
                        State
                        <input
                          value={priorAddress.state}
                          maxLength={2}
                          onChange={(event) =>
                            changePriorAddress('state', event.target.value.toUpperCase())
                          }
                          autoComplete="off"
                        />
                      </label>
                      <label>
                        ZIP code
                        <input
                          value={priorAddress.postalCode}
                          inputMode="numeric"
                          maxLength={9}
                          onChange={(event) =>
                            changePriorAddress('postalCode', event.target.value.replace(/\D/g, ''))
                          }
                          autoComplete="off"
                        />
                      </label>
                      <label className="prior-address-check">
                        <input
                          type="checkbox"
                          checked={confirmedCandidateId === candidate.id}
                          onChange={(event) =>
                            setConfirmedCandidateId(event.target.checked ? candidate.id || '' : '')
                          }
                        />
                        I confirmed this address in the selected patient's eCW chart history.
                      </label>
                    </fieldset>
                  )}
                  {!readOnly && !!candidate.id && (
                    <button
                      disabled={
                        !!busy ||
                        recovering ||
                        result.recovery?.state === 'indeterminate' ||
                        candidate.reason_codes?.some((reason) =>
                          [
                            'PATIENT_NAME_MISMATCH',
                            'PATIENT_DOB_MISMATCH',
                            'PATIENT_DOB_MISSING',
                            'RELATIONSHIP_CONFLICT',
                          ].includes(reason),
                        ) ||
                        (needsPriorAddress && !priorAddressReady)
                      }
                      onClick={() =>
                        candidate.id &&
                        void selectCoverage(
                          candidate.id,
                          false,
                          needsPriorAddress && priorAddressReady
                            ? { ...priorAddress, confirmed_in_ecw_history: true }
                            : undefined,
                        )
                      }
                    >
                      Check this policy
                    </button>
                  )}
                </article>
              )
            })}
        </section>
      )}
      {!verifiedCoverage && !!result.insurance_hints?.length && (
        <section className="visit-recovery">
          <h3>Recorded insurance clues</h3>
          <p>These details can support an inquiry. They do not verify active coverage.</p>
          {result.insurance_hints.map((hint, index) => (
            <p key={index}>
              {hint.payer || 'Payer unknown'} · Member ID {hint.member_id || 'unknown'} ·{' '}
              {hint.sources.join(', ')}
            </p>
          ))}
        </section>
      )}
      {result.insurance_diagnosis && !verifiedCoverage && (
        <details className="visit-recovery" open={!verifiedCoverage || undefined}>
          <summary>Insurance check details</summary>
          <p>
            Identity: {result.insurance_diagnosis.identityStatus.replaceAll('_', ' ').toLowerCase()}{' '}
            · Eligibility:{' '}
            {result.insurance_diagnosis.eligibilityStatus.replaceAll('_', ' ').toLowerCase()} ·
            Insurance estimate:{' '}
            {result.insurance_diagnosis.pricingStatus.replaceAll('_', ' ').toLowerCase()}
          </p>
          {result.insurance_diagnosis.activePolicyCount != null && (
            <p>
              {result.insurance_diagnosis.activePolicyCount} distinct active medical{' '}
              {result.insurance_diagnosis.activePolicyCount === 1 ? 'policy' : 'policies'} found.
              Returned responses may include copies of one policy and other benefit categories.
            </p>
          )}
          {result.insurance_diagnosis.rawCandidateCount != null && (
            <p>
              {result.insurance_diagnosis.rawCandidateCount} payer responses ·{' '}
              {result.insurance_diagnosis.retainedCandidateCount} retained ·{' '}
              {result.insurance_diagnosis.verifiedCandidateCount} identity and coverage matches
            </p>
          )}
          {result.insurance_diagnosis.enrollment &&
            (result.benefits?.active !== true || result.benefits?.historical_only) && (
              <p>
                Provider enrollment:{' '}
                {result.insurance_diagnosis.enrollment.status.replaceAll('_', ' ').toLowerCase()}
              </p>
            )}
          {!!result.insurance_diagnosis.pricingReasons.length && (
            <ul>
              {result.insurance_diagnosis.pricingReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
          {result.cash_fallback && <p>{result.cash_fallback.reason}</p>}
        </details>
      )}
    </>
  )
}
